"""Dev-side CLI to pre-warm slide derivatives (thumbnails + display images).

Thin wrapper over `app.prewarm` (which also runs inside the prod container). See
`backend/app/prewarm.py`. Staleness-aware: regenerates only missing/zero-byte/stale
derivatives unless --force.

Run:  python -m importer.make_thumbnails [--force]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.prewarm import prewarm_all  # noqa: E402

if __name__ == "__main__":
    prewarm_all(force="--force" in sys.argv)
