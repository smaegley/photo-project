"""Preserve dev's face state across a prod-snapshot refresh (dev-side only).

`scripts/load-prod-snapshot.sh` replaces dev's DB with prod's — which is correct for
everything prod owns, but wipes the three dev-only §14 tables (`face`,
`face_cluster`, `face_suggestion`) and the `faces_scanned_version` markers. That
used to cost ~85 minutes of re-detection per refresh and, much worse, silently
discarded Steve's review verdicts: rejected suggestions came back as fresh
suggestions and ignored-stranger clusters re-asked their questions.

    # BEFORE the refresh
    python -m app.faces_io --export        # -> data/review/faces_state.json.gz
    # refresh, then:
    python -m app.faces_io --import [--force]

Keyed on `photo.source_file` and `person.id` (§14.8a — row ids differ per database
and change across refreshes). Face/cluster row ids are remapped on import;
suggestions follow their face by position. Photos the new snapshot no longer has
(deleted on prod) drop their faces with a report line — that is prod's call winning,
as it should. After import, `detect_faces` sees only genuinely new photos.
"""
import argparse
import base64
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

from app import models as m
from app.database import SessionLocal
from app.import_photos import REVIEW_DIR

DEFAULT_PATH = "faces_state.json.gz"


def _b64(b: bytes | None) -> str | None:
    return base64.b64encode(b).decode("ascii") if b else None


def _unb64(s: str | None) -> bytes | None:
    return base64.b64decode(s) if s else None


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def _undt(s: str | None):
    return datetime.fromisoformat(s) if s else None


def export_faces(path: Path) -> None:
    db = SessionLocal()
    try:
        photos = dict(db.query(m.Photo.id, m.Photo.source_file).all())
        markers = {photos[pid]: v for pid, v in
                   db.query(m.Photo.id, m.Photo.faces_scanned_version)
                   .filter(m.Photo.faces_scanned_version.isnot(None)).all()
                   if pid in photos}
        clusters = [{"old_id": c.id, "status": c.status, "person_id": c.person_id,
                     "centroid": _b64(c.centroid), "n_faces": c.n_faces,
                     "prominence": c.prominence, "decided_by": c.decided_by,
                     "decided_at": _iso(c.decided_at), "created_at": _iso(c.created_at)}
                    for c in db.query(m.FaceCluster).all()]
        faces, face_index = [], {}   # old face id -> position in list
        skipped = 0
        for f in db.query(m.Face).order_by(m.Face.id).all():
            sf = photos.get(f.photo_id)
            if sf is None:
                skipped += 1
                continue
            face_index[f.id] = len(faces)
            faces.append({"source_file": sf, "x": f.x, "y": f.y, "w": f.w, "h": f.h,
                          "det_score": f.det_score, "embedding": _b64(f.embedding),
                          "detector_version": f.detector_version,
                          "cluster_old_id": f.cluster_id,
                          "created_at": _iso(f.created_at)})
        suggestions = []
        for s in db.query(m.FaceSuggestion).all():
            if s.face_id not in face_index:
                continue
            suggestions.append({"face_pos": face_index[s.face_id],
                                "person_id": s.person_id, "score": s.score,
                                "status": s.status, "decided_by": s.decided_by,
                                "decided_at": _iso(s.decided_at)})
    finally:
        db.close()

    payload = {"exported_at": datetime.now(timezone.utc).isoformat(),
               "markers": markers, "clusters": clusters, "faces": faces,
               "suggestions": suggestions}
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh)
    mb = path.stat().st_size / 1048576
    print(f"exported {len(faces)} faces ({skipped} orphaned skipped), "
          f"{len(clusters)} clusters, {len(suggestions)} suggestions, "
          f"{len(markers)} scan markers -> {path} ({mb:.1f} MB)")


def import_faces_state(path: Path, force: bool = False) -> None:
    if not path.exists():
        print(f"state file not found: {path}")
        return
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        payload = json.load(fh)
    print(f"loaded state from {payload.get('exported_at')}: "
          f"{len(payload['faces'])} faces, {len(payload['clusters'])} clusters, "
          f"{len(payload['suggestions'])} suggestions")

    db = SessionLocal()
    try:
        have = db.query(m.Face).count()
        if have and not force:
            print(f"REFUSING: this DB already has {have} face rows — this import is "
                  f"for a fresh snapshot. --force replaces them.")
            return
        if have and force:
            db.query(m.FaceSuggestion).delete(synchronize_session=False)
            db.query(m.Face).delete(synchronize_session=False)
            db.query(m.FaceCluster).delete(synchronize_session=False)
            db.flush()

        photo_by_src = dict(db.query(m.Photo.source_file, m.Photo.id).all())
        people = {pid for (pid,) in db.query(m.Person.id).all()}

        cluster_map = {}
        for c in payload["clusters"]:
            row = m.FaceCluster(status=c["status"],
                                person_id=c["person_id"] if c["person_id"] in people else None,
                                centroid=_unb64(c["centroid"]), n_faces=c["n_faces"],
                                prominence=c["prominence"], decided_by=c["decided_by"],
                                decided_at=_undt(c["decided_at"]),
                                created_at=_undt(c["created_at"]) or datetime.utcnow())
            db.add(row)
            db.flush()
            cluster_map[c["old_id"]] = row.id

        face_ids, missing_photos = [], set()
        for f in payload["faces"]:
            pid = photo_by_src.get(f["source_file"])
            if pid is None:
                face_ids.append(None)
                missing_photos.add(f["source_file"])
                continue
            row = m.Face(photo_id=pid, x=f["x"], y=f["y"], w=f["w"], h=f["h"],
                         det_score=f["det_score"], embedding=_unb64(f["embedding"]),
                         detector_version=f["detector_version"],
                         cluster_id=cluster_map.get(f["cluster_old_id"]),
                         created_at=_undt(f["created_at"]) or datetime.utcnow())
            db.add(row)
            db.flush()
            face_ids.append(row.id)

        n_sugg = dropped_sugg = 0
        for s in payload["suggestions"]:
            fid = face_ids[s["face_pos"]]
            if fid is None or s["person_id"] not in people:
                dropped_sugg += 1
                continue
            db.add(m.FaceSuggestion(face_id=fid, person_id=s["person_id"],
                                    score=s["score"], status=s["status"],
                                    decided_by=s["decided_by"],
                                    decided_at=_undt(s["decided_at"])))
            n_sugg += 1

        n_marked = 0
        for sf, ver in payload["markers"].items():
            pid = photo_by_src.get(sf)
            if pid is not None:
                db.query(m.Photo).filter(m.Photo.id == pid).update(
                    {"faces_scanned_version": ver}, synchronize_session=False)
                n_marked += 1

        db.commit()
        restored = sum(1 for x in face_ids if x is not None)
        unscanned = db.query(m.Photo).filter(
            m.Photo.faces_scanned_version.is_(None)).count()
        print(f"\nrestored {restored} faces ({len(missing_photos)} photos no longer "
              f"exist here — prod's call), {len(cluster_map)} clusters, "
              f"{n_sugg} suggestions ({dropped_sugg} dropped), {n_marked} scan markers")
        print(f"photos still unscanned (detect_faces will pick these up): {unscanned}")
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Export/import dev face state across snapshot refreshes")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--export", action="store_true")
    g.add_argument("--import", dest="do_import", action="store_true")
    ap.add_argument("--path", type=Path, default=Path(DEFAULT_PATH))
    ap.add_argument("--force", action="store_true",
                    help="import even if face rows already exist (replaces them)")
    args = ap.parse_args()
    p = args.path if args.path.is_absolute() else REVIEW_DIR / args.path.name
    if args.export:
        export_faces(p)
    else:
        import_faces_state(p, force=args.force)
