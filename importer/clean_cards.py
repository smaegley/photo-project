"""Clean up the Airequipt index-card scans: detect the box face, crop to it,
deskew (perspective-correct), and orient upright. Non-destructive — writes to
library/index_cards_cleaned/ for review; originals untouched.

Heuristic document scan; rows where detection is weak are flagged in the report.
Run: python -m importer.clean_cards [--all]  (default processes a few samples)
"""
import sys
from pathlib import Path

import cv2
import numpy as np

CARDS = Path("/mnt/photos/library/index_cards")
OUT = Path("/mnt/photos/library/index_cards_cleaned")


def order_points(pts):
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]      # top-left
    rect[2] = pts[np.argmax(s)]      # bottom-right
    d = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(d)]      # top-right
    rect[3] = pts[np.argmax(d)]      # bottom-left
    return rect


def warp(img, pts):
    rect = order_points(pts)
    tl, tr, br, bl = rect
    W = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
    H = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
    dst = np.array([[0, 0], [W - 1, 0], [W - 1, H - 1], [0, H - 1]], dtype="float32")
    M = cv2.getPerspectiveTransform(rect, dst)
    return cv2.warpPerspective(img, M, (W, H))


def detect_box_bbox(img):
    """Find the box by masking out the green/teal cutting mat, then return the
    bounding box of the largest non-mat blob. Returns (x,y,w,h) or None."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h, w = img.shape[:2]
    # estimate mat colour from the image border (mat usually frames the shot)
    b = 14
    border = np.concatenate([
        hsv[:b, :].reshape(-1, 3), hsv[-b:, :].reshape(-1, 3),
        hsv[:, :b].reshape(-1, 3), hsv[:, -b:].reshape(-1, 3)])
    mh = float(np.median(border[:, 0]))
    mat = cv2.inRange(hsv, np.array([max(mh - 18, 0), 25, 25]),
                      np.array([min(mh + 18, 179), 255, 255]))
    fg = cv2.bitwise_not(mat)
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8), iterations=2)
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8), iterations=2)
    cnts, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    c = max(cnts, key=cv2.contourArea)
    if cv2.contourArea(c) < 0.10 * h * w:
        return None
    x, y, bw, bh = cv2.boundingRect(c)
    # pad a touch, clamp
    pad = int(0.015 * max(h, w))
    x0, y0 = max(x - pad, 0), max(y - pad, 0)
    x1, y1 = min(x + bw + pad, w), min(y + bh + pad, h)
    return x0, y0, x1 - x0, y1 - y0


def detect_orientation(img):
    """Use the blue 'AIREQUIPT MAGAZINE' band (top when upright) to choose the
    rotation that puts it on top. Returns (cv2_rotate_code or None, label)."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    blue = cv2.inRange(hsv, np.array([95, 70, 40]), np.array([135, 255, 255]))
    blue = cv2.morphologyEx(blue, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8), iterations=2)
    cnts, _ = cv2.findContours(blue, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    h, w = img.shape[:2]
    if not cnts:
        return None, "no-band"
    c = max(cnts, key=cv2.contourArea)
    if cv2.contourArea(c) < 0.02 * h * w:
        return None, "weak-band"
    x, y, bw, bh = cv2.boundingRect(c)
    cx, cy = (x + bw / 2) / w, (y + bh / 2) / h
    if bw >= bh:                      # horizontal band -> top or bottom
        return (None, "upright") if cy < 0.5 else (cv2.ROTATE_180, "rot180")
    else:                            # vertical band -> left or right
        return (cv2.ROTATE_90_CLOCKWISE, "rot90cw") if cx < 0.5 \
            else (cv2.ROTATE_90_COUNTERCLOCKWISE, "rot90ccw")


def clean_one(path: Path):
    img = cv2.imread(str(path))
    if img is None:
        return None, "unreadable"
    code, otag = detect_orientation(img)
    if code is not None:
        img = cv2.rotate(img, code)
    # crop to the box (only accept a confident, meaningful crop)
    bbox = detect_box_bbox(img)
    ctag = "no-crop"
    if bbox is not None:
        x, y, bw, bh = bbox
        frac = (bw * bh) / (img.shape[0] * img.shape[1])
        if 0.25 < frac < 0.92:
            img = img[y:y + bh, x:x + bw]
            ctag = f"crop{frac:.0%}"
    flag = "" if otag not in ("no-band", "weak-band") else " ⚠review-orient"
    return img, f"{otag}/{ctag}{flag}"


def run(do_all: bool):
    OUT.mkdir(parents=True, exist_ok=True)
    cards = sorted(CARDS.glob("Mag*_card_*.jpg"))
    if not do_all:
        cards = [CARDS / "Mag13_card_1.jpg", CARDS / "Mag11_card_1.jpg",
                 CARDS / "Mag14_card_2.jpg", CARDS / "Mag12_card_1.jpg"]
    report = []
    for p in cards:
        out, status = clean_one(p)
        if out is not None:
            cv2.imwrite(str(OUT / p.name), out, [cv2.IMWRITE_JPEG_QUALITY, 90])
        report.append((p.name, status))
        print(f"  {p.name:24} {status}")
    flagged = [n for n, s in report if "NO-CROP" in s]
    print(f"\n{len(report)} processed, {len(flagged)} need manual review -> {OUT}")


if __name__ == "__main__":
    run(do_all="--all" in sys.argv)
