"""Junk score for ingested photos (SPEC §15.5, slice 5).

    python -m app.junk [--limit N] [--force]

A local zero-shot classifier: CLIP ViT-B/32's image embedding is compared with two
fixed lists of text prompts ("a family photo", "a landscape photo" … versus "a photo of
a receipt", "a screenshot of a phone screen" …) and the score is the share of
probability that lands on the junk list, 0..1. Nothing leaves the machine.

It is a **pre-sort, never a verdict** (P-I2): on the first real batch it found the
serial-number plates and hotel-room shots, and also a dog asleep in a cardboard box.
So the score only moves a photo to the "Likely junk" tab — it never culls — and
`app.outings` ignores it for any photo with a face in it.

Only the vision model is needed at run time (`<models>/clip/vision_model.onnx`, mounted
read-only like the face models). The text side is precomputed into
`app/junk_prompts.npz` by `tools/build_junk_prompts.py`. If the model file is absent
the step is skipped with a warning and nothing is classed as junk.

One short write per photo, inference outside any transaction
(tests/check_write_contention.py).
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

from app import models as m
from app.config import settings
from app.database import SessionLocal

THRESHOLD = 0.5          # P-I2: 20 of 450 flagged on the first batch
MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32)
STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32)
PROMPTS = Path(__file__).with_name("junk_prompts.npz")


def _prep(path) -> np.ndarray:
    with Image.open(path) as im:
        im = im.convert("RGB")
        w, h = im.size
        s = 224 / min(w, h)
        im = im.resize((max(224, round(w * s)), max(224, round(h * s))), Image.BICUBIC)
        w, h = im.size
        left, top = (w - 224) // 2, (h - 224) // 2
        a = np.asarray(im.crop((left, top, left + 224, top + 224)), np.float32) / 255
    return ((a - MEAN) / STD).transpose(2, 0, 1)


def load():
    """-> score(path) -> float, or None when the model is not installed."""
    model = Path(settings.junk_model_path).expanduser()
    if not model.exists():
        return None
    from app import ort_threads
    ort_threads.pin()
    import onnxruntime as ort
    z = np.load(PROMPTS)
    text, n_keep = z["text"], int(z["n_keep"])
    sess = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
    name = sess.get_inputs()[0].name

    def score(path) -> float:
        e = sess.run(None, {name: _prep(path)[None]})[0][0]
        e = e / np.linalg.norm(e)
        lg = 100 * (text @ e)
        p = np.exp(lg - lg.max())
        return float(p[n_keep:].sum() / p.sum())
    return score


def run(limit: int | None = None, force: bool = False) -> dict:
    from app.detect_faces import display_path
    score = load()
    if score is None:
        print(f"junk: model not found at {settings.junk_model_path} — step skipped, "
              "nothing will be classed as junk", file=sys.stderr)
        return {"skipped": True}
    db = SessionLocal()
    try:
        q = (db.query(m.Photo.id, m.Photo.source_file, m.Photo.file_version,
                      m.Photo.storage_backend, m.Photo.rotation)
             .filter(m.Photo.ingest_run_id.isnot(None)))
        if not force:
            q = q.filter(m.Photo.junk_score.is_(None))
        rows = q.order_by(m.Photo.id).all()
        if limit:
            rows = rows[:limit]
        t0, done, missing, flagged = time.time(), 0, 0, 0
        for pid, sf, fv, backend, rot in rows:
            path = display_path(sf, fv, backend, rot or 0)
            if not path.exists():
                missing += 1
                continue
            try:
                s = score(path)                       # slow part: no transaction open
            except Exception as e:  # noqa: BLE001 — one unreadable derivative is not fatal
                print(f"  ! junk {sf}: {type(e).__name__}: {e}", file=sys.stderr)
                missing += 1
                continue
            db.query(m.Photo).filter(m.Photo.id == pid).update(
                {"junk_score": s}, synchronize_session=False)
            db.commit()
            done += 1
            flagged += s >= THRESHOLD
        st = {"scored": done, "over_threshold": int(flagged), "no_derivative": missing,
              "secs": round(time.time() - t0)}
        print("junk:", st)
        return st
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Junk score for ingested photos (SPEC §15.5)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force", action="store_true", help="rescore photos that already have a score")
    a = ap.parse_args()
    run(limit=a.limit, force=a.force)
    sys.exit(0)
