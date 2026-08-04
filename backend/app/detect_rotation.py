"""Find sideways photos by re-running face detection at 90/180/270 (dev-side).

The detector almost never finds a face in a rotated photo, so a photo with people
in it and zero detections is very likely on its side. This probes exactly those:
every photo with **no face rows** gets detected at all four orientations, and any
rotation that finds faces where 0° found none becomes a proposal for the Rotation
sweep admin UI. Photos that are faceless at every angle (landscapes, buildings)
simply report "checked, nothing at any angle" — only human eyes can judge those.

    python -m app.detect_rotation [--limit N] [--min-score 0.60]

Writes data/review/rotation_proposals.json. Detection-only (no embeddings), so it
runs at roughly 2-3 photos/s per angle. Like all §14 machinery this NEVER runs on
prod — rotations are applied on dev and shipped as files + a tags re-import.
"""
import argparse
import json
import sys
import time
import warnings
from datetime import datetime, timezone

import numpy as np
from PIL import Image

from app import models as m
from app.database import SessionLocal
from app.detect_faces import DET_SIZE, display_path
from app.import_photos import REVIEW_DIR

OUT_PATH = REVIEW_DIR / "rotation_proposals.json"


def _load_detector():
    warnings.filterwarnings("ignore")
    from insightface.app import FaceAnalysis
    fa = FaceAnalysis(name="buffalo_l", allowed_modules=["detection"],
                      providers=["CPUExecutionProvider"])
    fa.prepare(ctx_id=-1, det_size=DET_SIZE)
    return fa


def run(limit: int | None = None, min_score: float = 0.60) -> None:
    db = SessionLocal()
    try:
        rows = (db.query(m.Photo.id, m.Photo.source_file, m.Photo.file_version,
                         m.Photo.storage_backend, m.Photo.origin)
                .filter(~m.Photo.id.in_(db.query(m.Face.photo_id).distinct()))
                .order_by(m.Photo.id).all())
    finally:
        db.close()
    if limit:
        rows = rows[:limit]
    print(f"{len(rows)} photos with zero detected faces to probe", flush=True)

    fa = _load_detector()
    results, proposals = [], 0
    t0 = time.time()
    for i, (pid, sf, fv, backend, origin) in enumerate(rows, 1):
        path = display_path(sf, fv, backend)
        if not path.exists():
            print(f"  MISSING derivative for photo {pid}: {path}", flush=True)
            continue
        try:
            img = Image.open(path).convert("RGB")
        except Exception as e:  # truncated file etc — report, keep going
            print(f"  UNREADABLE photo {pid}: {e}", flush=True)
            continue
        faces_at = {}
        for cw in (0, 90, 180, 270):
            # PIL rotates counter-clockwise; a photo needing `cw` clockwise to be
            # upright is tested by rotating the pixels clockwise, i.e. rotate(-cw).
            arr = np.asarray(img if cw == 0 else img.rotate(-cw, expand=True))
            dets = fa.get(arr[:, :, ::-1])  # BGR, as the detector expects
            scores = [float(f.det_score) for f in dets if f.det_score >= min_score]
            faces_at[str(cw)] = [len(scores), round(max(scores), 3) if scores else 0.0]
        # propose the angle with the most (then best-scoring) faces — but only if
        # 0° really found nothing, which selection already guarantees for face rows
        # but re-checks here at this min_score.
        best_cw = max((int(k) for k in faces_at),
                      key=lambda k: (faces_at[str(k)][0], faces_at[str(k)][1]))
        proposal = best_cw if best_cw != 0 and faces_at[str(best_cw)][0] > 0 \
            and faces_at["0"][0] == 0 else None
        proposals += proposal is not None
        results.append({"photo_id": pid, "source_file": sf, "origin": origin,
                        "proposal": proposal, "faces_at": faces_at})
        if i % 25 == 0 or i == len(rows):
            rate = i / (time.time() - t0)
            print(f"  {i}/{len(rows)} ({rate:.1f}/s) — {proposals} proposals so far",
                  flush=True)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "min_score": min_score, "probed": len(results), "results": results}, indent=1))
    print(f"\n{proposals} rotation proposals across {len(results)} probed photos "
          f"-> {OUT_PATH}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--min-score", type=float, default=0.60)
    args = ap.parse_args()
    sys.exit(run(limit=args.limit, min_score=args.min_score))
