"""Shared photo metadata extraction (SPEC §11.2 / §11.6).

Reads embedded XMP + EXIF from an image file using **Pillow only** — no exiftool
or other system dependency (exiftool isn't installed and needs sudo). Lightroom
writes the fields we care about into the XMP packet, which Pillow exposes via
`Image.info['xmp']`.

Used by both the slide importer (LR-tagged people on the 1,140 slides) and the
future non-slide importer (digital/scanned photos — dates, GPS, keywords).

Notes learned from the real exports (2026-07-01):
- People are written three consistent ways: `Iptc4xmpExt:PersonInImage`,
  `mwg-rs` face-region `Name`, and `dc:subject` keywords. We take people from the
  first two (structured, person-only); keywords are a noisier mix (they include a
  batch "slides" tag) and are returned separately.
- Slides carry the *scan* date in XMP (e.g. 2024), NOT the photo date — callers
  ingesting slides must ignore `capture_date` and keep the manifest date.
"""
import html
import re
from datetime import date

from PIL import Image

# --- XMP field patterns (the packet is RDF/XML; regex is enough and matches the
#     importer's existing pragmatic parsing style) ---
_PII_BLOCK = re.compile(r"<Iptc4xmpExt:PersonInImage>(.*?)</Iptc4xmpExt:PersonInImage>", re.S)
_SUBJECT_BLOCK = re.compile(r"<dc:subject>(.*?)</dc:subject>", re.S)
_RDF_LI = re.compile(r"<rdf:li[^>]*>(.*?)</rdf:li>", re.S)
_MWG_NAME_EL = re.compile(r"<mwg-rs:Name>(.*?)</mwg-rs:Name>", re.S)
_MWG_NAME_ATTR = re.compile(r'mwg-rs:Name="([^"]*)"')


def _clean(s: str) -> str:
    return html.unescape(s.strip())


# --- name normalization (shared by the importer + the LR overlay so resolution
#     stays identical between them; SPEC §3.9) ---
def norm_text(s: str) -> str:
    s = s.strip().strip('"').strip().lower()
    s = re.sub(r"[.“”]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def canon_alias_key(a: str) -> str:
    """Canon alias key: a whole-parenthesized form is a GROUP label -> inner text."""
    a = a.strip()
    mm = re.fullmatch(r"\((.*)\)", a)
    if mm:
        a = mm.group(1)
    return norm_text(a)


def read_xmp(path) -> str | None:
    """Return the raw XMP packet as text, or None if absent/unreadable."""
    try:
        with Image.open(path) as im:
            x = im.info.get("xmp")
    except Exception:
        return None
    if not x:
        return None
    return x.decode("utf-8", "ignore") if isinstance(x, (bytes, bytearray)) else str(x)


def people_from_xmp(xmp: str) -> set[str]:
    """Named people from PersonInImage + mwg-rs face regions (union of both)."""
    names: set[str] = set()
    for blk in _PII_BLOCK.findall(xmp):
        names.update(_clean(li) for li in _RDF_LI.findall(blk))
    names.update(_clean(n) for n in _MWG_NAME_EL.findall(xmp))
    names.update(_clean(n) for n in _MWG_NAME_ATTR.findall(xmp))
    return {n for n in names if n}


def keywords_from_xmp(xmp: str) -> list[str]:
    """dc:subject keywords (may include people + noise like a batch 'slides' tag)."""
    blk = _SUBJECT_BLOCK.search(xmp)
    if not blk:
        return []
    return [k for k in (_clean(li) for li in _RDF_LI.findall(blk.group(1))) if k]


# --- EXIF (for digital/scan photos; slides have no camera EXIF date/GPS) ---
def _exif(path):
    try:
        with Image.open(path) as im:
            return im.getexif(), im.size
    except Exception:
        return None, (None, None)


def image_size(path) -> tuple[int | None, int | None]:
    _, size = _exif(path)
    return size


def capture_date_from_exif(path) -> date | None:
    """EXIF DateTimeOriginal (falls back to DateTimeDigitized / DateTime)."""
    ex, _ = _exif(path)
    if not ex:
        return None
    ifd = {}
    try:
        ifd = ex.get_ifd(0x8769)  # Exif IFD
    except Exception:
        pass
    raw = ifd.get(0x9003) or ifd.get(0x9004) or ex.get(0x0132)
    if not raw:
        return None
    mm = re.match(r"(\d{4})[:\-](\d{2})[:\-](\d{2})", str(raw))
    if not mm:
        return None
    try:
        return date(int(mm.group(1)), int(mm.group(2)), int(mm.group(3)))
    except ValueError:
        return None


def gps_from_exif(path) -> tuple[float, float] | None:
    """(lat, lon) in signed decimal degrees, or None."""
    ex, _ = _exif(path)
    if not ex:
        return None
    try:
        gps = ex.get_ifd(0x8825)  # GPS IFD
    except Exception:
        return None
    if not gps or 2 not in gps or 4 not in gps:
        return None

    def dms(v):
        d, m, s = v
        return float(d) + float(m) / 60 + float(s) / 3600

    try:
        lat = dms(gps[2])
        if str(gps.get(1, "N")).upper().startswith("S"):
            lat = -lat
        lon = dms(gps[4])
        if str(gps.get(3, "E")).upper().startswith("W"):
            lon = -lon
        return (lat, lon)
    except Exception:
        return None


def extract(path) -> dict:
    """One-shot bundle for the non-slide importer (SPEC §11.6)."""
    xmp = read_xmp(path) or ""
    w, h = image_size(path)
    return {
        "people": people_from_xmp(xmp) if xmp else set(),
        "keywords": keywords_from_xmp(xmp) if xmp else [],
        "capture_date": capture_date_from_exif(path),
        "gps": gps_from_exif(path),
        "width": w,
        "height": h,
    }
