"""Metadata probe for the §12.9 FastFoto/Lightroom contract tests.

Dumps everything we might map from a sample file so the §12.4/§12.6 blanks can be
closed by inspection: filename + folder (the FastFoto grammar), every EXIF IFD
with tag names (dates, GPS, Make/Model), the raw XMP packet (people, keywords,
hierarchical subjects, IPTC location fields), and what `metadata.extract()`
currently understands — so gaps between "in the file" and "parsed" are obvious.

Usage:
    python -m importer.probe <file-or-dir> [...]

A directory is walked recursively (skipping macOS `._*` junk).
"""
import sys
from pathlib import Path

from PIL import Image
from PIL.ExifTags import GPSTAGS, TAGS

from importer import metadata

# IFDs worth walking: (label, pointer tag in the 0th IFD or None for the 0th itself)
_EXIF_IFD = 0x8769
_GPS_IFD = 0x8825


def _dump_ifd(label: str, ifd, tag_names) -> None:
    if not ifd:
        print(f"  [{label}] (absent)")
        return
    print(f"  [{label}]")
    for tag, val in sorted(ifd.items()):
        name = tag_names.get(tag, f"0x{tag:04x}")
        sval = repr(val)
        if len(sval) > 120:
            sval = sval[:120] + f"… ({len(sval)} chars)"
        print(f"    {name} (0x{tag:04x}) = {sval}")


def probe(path: Path) -> None:
    print("=" * 78)
    print(f"FILE    {path.name}")
    print(f"FOLDER  {path.parent}")

    try:
        with Image.open(path) as im:
            size = im.size
            exif = im.getexif()
    except Exception as e:  # noqa: BLE001 — a probe should report, not crash
        print(f"  !! unreadable: {e}")
        return
    print(f"SIZE    {size[0]} x {size[1]}")

    print("EXIF")
    _dump_ifd("0th IFD", exif, TAGS)
    try:
        _dump_ifd("Exif IFD", exif.get_ifd(_EXIF_IFD), TAGS)
    except Exception:
        print("  [Exif IFD] (unreadable)")
    try:
        _dump_ifd("GPS IFD", exif.get_ifd(_GPS_IFD), GPSTAGS)
    except Exception:
        print("  [GPS IFD] (unreadable)")

    xmp = metadata.read_xmp(path)
    print(f"XMP     {'absent' if not xmp else f'{len(xmp)} chars — raw packet below'}")
    if xmp:
        print("-" * 78)
        print(xmp.strip())
        print("-" * 78)

    print("metadata.extract() →")
    b = metadata.extract(path)
    print(f"  people       = {sorted(b['people']) or '(none)'}")
    print(f"  keywords     = {b['keywords'] or '(none)'}")
    print(f"  capture_date = {b['capture_date']}")
    print(f"  gps          = {b['gps']}")
    print()


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    files: list[Path] = []
    for a in argv:
        p = Path(a)
        if p.is_dir():
            files += sorted(
                f for f in p.rglob("*")
                if f.is_file() and not f.name.startswith("._")
                and f.suffix.lower() in {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
            )
        else:
            files.append(p)
    for f in files:
        probe(f)
    print(f"probed {len(files)} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
