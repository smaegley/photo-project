"""Stack near-duplicate photos in the inbox (SPEC §15.4 step 5, slice 6).

    python -m app.stacks [--dry-run]

Phones produce bursts: the same group shot three times, a dog on a ramp twelve times.
A stack is a run of *pending* photos in one outing where each was taken within 30 s of
the previous one and their difference hashes are within 12 bits of 64. The inbox shows
a stack as one cell with a count; the admin opens it and chooses. **Nothing is culled
here** (decision 8) — the stack only groups, and suggests the sharpest as the one to keep.

Thresholds were set by eye on the first batch: every pair at ≤ 10 bits was a retake of
the same frame, 11–12 still were, and by 14–16 different poses and different subjects
started to appear. Within 30 s the chain rule (A~B, B~C → one stack) is what collects a
burst whose first and last frames have drifted apart.

Two steps, both idempotent:
1. hash + sharpness for ingested photos that lack them (reads the display derivative;
   one short write per photo);
2. regroup pending photos (chunked writes with pauses — tests/check_write_contention.py).
No ML and no network: runs in the plain `api` image too. Runs after `app.outings`.
"""
import argparse
import sys
import time
from collections import defaultdict

import numpy as np
from PIL import Image

from app import models as m
from app.database import SessionLocal

MAX_SECONDS = 30
MAX_BITS = 12
CHUNK = 100
YIELD_S = 0.05


def measure(path) -> tuple[str, float]:
    """-> (dhash as 16 hex chars, sharpness)."""
    with Image.open(path) as im:
        g = im.convert("L")
        a = np.asarray(g.resize((9, 8), Image.LANCZOS), dtype=np.int16)
        bits = np.packbits((a[:, 1:] > a[:, :-1]).flatten()).tobytes().hex()
        s = np.asarray(g.resize((512, 512)), dtype=np.float32)
    lap = s[1:-1, 1:-1] * 4 - s[:-2, 1:-1] - s[2:, 1:-1] - s[1:-1, :-2] - s[1:-1, 2:]
    return bits, float(lap.var())


def bits_apart(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()


def group(rows) -> dict[int, int | None]:
    """rows: id, taken_at, outing_id, phash (pending photos) -> {photo id: stack id}.
    A stack's id is its lowest photo id; a photo on its own gets None."""
    by_outing = defaultdict(list)
    for r in rows:
        if r.taken_at is not None and r.phash and r.outing_id is not None:
            by_outing[r.outing_id].append(r)
    out: dict[int, int | None] = {r.id: None for r in rows}
    for rs in by_outing.values():
        rs.sort(key=lambda r: (r.taken_at, r.id))
        run = [rs[0]]
        for prev, cur in zip(rs, rs[1:]):
            if ((cur.taken_at - prev.taken_at).total_seconds() <= MAX_SECONDS
                    and bits_apart(cur.phash, prev.phash) <= MAX_BITS):
                run.append(cur)
                continue
            if len(run) > 1:
                sid = min(r.id for r in run)
                out.update({r.id: sid for r in run})
            run = [cur]
        if len(run) > 1:
            sid = min(r.id for r in run)
            out.update({r.id: sid for r in run})
    return out


def run(dry_run: bool = False) -> dict:
    from app.detect_faces import display_path
    db = SessionLocal()
    try:
        # --- 1. measure ---
        todo = (db.query(m.Photo.id, m.Photo.source_file, m.Photo.file_version,
                         m.Photo.storage_backend, m.Photo.rotation)
                .filter(m.Photo.ingest_run_id.isnot(None), m.Photo.phash.is_(None))
                .order_by(m.Photo.id).all())
        measured = 0
        if not dry_run:
            for pid, sf, fv, backend, rot in todo:
                try:
                    h, s = measure(display_path(sf, fv, backend, rot or 0))   # no transaction open
                except Exception as e:  # noqa: BLE001 — a missing derivative is not fatal
                    print(f"  ! stacks {sf}: {type(e).__name__}: {e}", file=sys.stderr)
                    continue
                db.query(m.Photo).filter(m.Photo.id == pid).update(
                    {"phash": h, "sharpness": s}, synchronize_session=False)
                db.commit()
                measured += 1

        # --- 2. group pending photos ---
        rows = (db.query(m.Photo.id, m.Photo.taken_at, m.Photo.outing_id, m.Photo.phash,
                         m.Photo.stack_id)
                .filter(m.Photo.ingest_run_id.isnot(None),
                        m.Photo.visibility == m.VIS_PENDING).all())
        want = group(rows)
        changes = [(r.id, want[r.id]) for r in rows if r.stack_id != want[r.id]]
        sizes = defaultdict(int)
        for sid in want.values():
            if sid is not None:
                sizes[sid] += 1
        st = {"measured": measured, "to_measure": len(todo) if dry_run else 0,
              "pending": len(rows), "stacks": len(sizes), "stacked_photos": sum(sizes.values()),
              "largest": max(sizes.values(), default=0), "updated": len(changes)}
        if dry_run:
            print("stacks (dry run):", st)
            return st
        for k in range(0, len(changes), CHUNK):
            for pid, sid in changes[k:k + CHUNK]:
                db.query(m.Photo).filter(m.Photo.id == pid).update(
                    {"stack_id": sid}, synchronize_session=False)
            db.commit()
            time.sleep(YIELD_S)
        print("stacks:", st)
        return st
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Near-duplicate stacks for the inbox (SPEC §15.4)")
    ap.add_argument("--dry-run", action="store_true")
    run(dry_run=ap.parse_args().dry_run)
    sys.exit(0)
