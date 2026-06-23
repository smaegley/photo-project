"""Geocode the places gazetteer (SPEC §3.4).

Fills the empty lat/lon columns of places_gazetteer_firstpass.csv using OSM
Nominatim (build-time, rate-limited, cached). Non-destructive: only the lat/lon
cells are written; all curation columns (merges, precision) are preserved.

- precision == 'unknown'  -> skipped (no pin; in-app tagging later)
- precision == 'region'   -> geocoded but stays flagged approximate (state/country centroid)
- everything else         -> geocoded; obscure misses go to a review report

Emits importer/review/geocode_report.csv with the source + confidence per row.
Re-runnable: rows that already have lat/lon are left alone unless --refresh.
"""
import csv
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GAZ = REPO_ROOT / "places_gazetteer_firstpass.csv"
REVIEW_DIR = REPO_ROOT / "importer" / "review"
CACHE = REPO_ROOT / "importer" / "review" / ".geocode_cache.json"

USER_AGENT = "maegley-photo-album/0.1 (steve@maegley.com)"
NOMINATIM = "https://nominatim.openstreetmap.org/search?"
RATE_SECONDS = 1.1  # Nominatim usage policy: <= 1 req/sec

STATE_NAMES = {
    "CO": "Colorado", "MN": "Minnesota", "FL": "Florida", "WY": "Wyoming",
    "TN": "Tennessee", "CA": "California", "UT": "Utah", "SD": "South Dakota",
    "OH": "Ohio", "DC": "Washington, D.C.", "MO": "Missouri", "WI": "Wisconsin",
    "IN": "Indiana", "MT": "Montana", "NM": "New Mexico", "SC": "South Carolina",
}
# Canadian provinces seen in the data -> country code for the query constraint.
CA_PROVINCES = {"ontario", "quebec", "manitoba", "alberta", "british columbia"}

# Hand-pinned coordinates for places Nominatim resolves poorly or that must be
# exact (SPEC §3.4: the two home addresses pin exactly). Keyed by place_id.
OVERRIDES: dict[str, tuple[float, float]] = {
    # Nominatim (countrycodes=us) matches a "Washington" in TN for bare "Washington, DC".
    "washington, dc": (38.907192, -77.036873),
}


def clean_query(name: str) -> str:
    """canonical_name -> a Nominatim-friendly query (state codes expanded)."""
    q = re.sub(r"\([^)]*\)", "", name)            # drop parenthetical hints
    q = re.sub(r"^\s*(off|near|en route to|en route|in flight|aboard)\b[, ]*", "", q, flags=re.I)
    q = re.sub(r"\s*/\s*", " / ", q)
    # expand a trailing ", XX" state code to its full name (avoids "CO"->Colombia)
    q = re.sub(r",\s*([A-Z]{2})\b", lambda m: ", " + STATE_NAMES.get(m.group(1), m.group(1)), q)
    return re.sub(r"\s+", " ", q).strip().strip(",").strip()


def detect_country(name: str) -> str:
    low = name.lower()
    if "canada" in low or any(p in low for p in CA_PROVINCES):
        return "ca"
    return "us"


def detect_state(name: str) -> str | None:
    m = re.search(r",\s*([A-Z]{2})\b", name)
    if m and m.group(1) in STATE_NAMES:
        return STATE_NAMES[m.group(1)]
    low = name.lower()
    for full in STATE_NAMES.values():
        if full.lower() in low:
            return full
    return None


def fallback_queries(name: str):
    """Yield progressively coarser queries. Never a bare 2-letter code (which
    Nominatim reads as a country); coarse fallbacks expand to the full state."""
    q = clean_query(name)
    state = detect_state(name)
    if q:
        yield q
    parts = [p.strip() for p in q.split(",") if p.strip()]
    # city + full state (e.g. "Littleton, Colorado")
    if len(parts) >= 2 and parts[-2].lower() not in {(state or "").lower()}:
        yield f"{parts[-2]}, {state or parts[-1]}"
    # whole state/region centroid
    if state:
        yield state


def load_cache() -> dict:
    if CACHE.exists():
        return json.loads(CACHE.read_text())
    return {}


def save_cache(cache: dict):
    CACHE.write_text(json.dumps(cache, indent=0))


def nominatim(query: str, cache: dict, country: str = "us"):
    """Return (lat, lon, display_name, importance) or None. Cached + rate-limited."""
    ckey = f"{country}|{query}"
    if ckey in cache:
        hit = cache[ckey]
        return tuple(hit) if hit else None
    params = {"q": query, "format": "json", "limit": 1, "addressdetails": 0}
    if country:
        params["countrycodes"] = country
    url = NOMINATIM + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    time.sleep(RATE_SECONDS)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    except Exception as e:  # noqa: BLE001
        print(f"  ! request error for {query!r}: {type(e).__name__}", file=sys.stderr)
        return None
    if not data:
        cache[ckey] = None
        return None
    d = data[0]
    result = (float(d["lat"]), float(d["lon"]), d.get("display_name", ""),
              float(d.get("importance", 0) or 0))
    cache[ckey] = list(result)
    return result


def run(refresh: bool = False):
    rows = list(csv.DictReader(open(GAZ)))
    fieldnames = rows[0].keys()
    cache = load_cache()

    report = []
    n_geo = n_skip = n_miss = n_kept = 0

    for r in rows:
        pid = r["place_id"].strip()
        name = r["canonical_name"].strip()
        prec = r["precision"].strip()

        if r["lat"].strip() and not refresh:
            n_kept += 1
            continue

        if prec == "unknown":
            report.append([pid, name, prec, "", "", "skipped (no pin)", ""])
            n_skip += 1
            continue

        if name.lower() in OVERRIDES:
            lat, lon = OVERRIDES[name.lower()]
            r["lat"], r["lon"] = f"{lat:.6f}", f"{lon:.6f}"
            report.append([pid, name, prec, r["lat"], r["lon"], "override", ""])
            n_geo += 1
            continue

        country = detect_country(name)
        hit = None
        used_q = ""
        for q in fallback_queries(name):
            hit = nominatim(q, cache, country)
            used_q = q
            if hit:
                break

        if hit:
            lat, lon, disp, imp = hit
            r["lat"], r["lon"] = f"{lat:.6f}", f"{lon:.6f}"
            # Approximate if we had to fall back past the full name to a city/state.
            if used_q != clean_query(name):
                flag = "REVIEW (fell back: pin is approximate)"
            else:
                flag = "ok"
            report.append([pid, name, prec, r["lat"], r["lon"], flag, f"{used_q} -> {disp[:55]}"])
            n_geo += 1
        else:
            report.append([pid, name, prec, "", "", "MISS (hand-pin needed)", ""])
            n_miss += 1
        save_cache(cache)  # checkpoint each row so a re-run resumes cheaply

    # write lat/lon back, preserving all columns
    with open(GAZ, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    with open(REVIEW_DIR / "geocode_report.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["place_id", "canonical_name", "precision", "lat", "lon", "status", "match"])
        w.writerows(report)

    print("=== GEOCODE COMPLETE ===")
    print(f"geocoded:      {n_geo}")
    print(f"skipped(unkn): {n_skip}")
    print(f"misses:        {n_miss} (need hand-pin)")
    print(f"already had:   {n_kept}")
    review = [x for x in report if x[5].startswith("REVIEW") or x[5].startswith("MISS")]
    print(f"flagged for review: {len(review)} -> review/geocode_report.csv")
    for x in review:
        print(f"   [{x[5]}] {x[1]}")


if __name__ == "__main__":
    run(refresh="--refresh" in sys.argv)
