"""On-demand geocoding for the in-app place editor (SPEC §3.4).

A trimmed port of importer/geocode.py — same OSM Nominatim query with the guards
that matter (expand ", XX" state codes so "CO" isn't read as Colombia; constrain
to US/CA; coarse fallbacks). Used interactively by an admin, so a single lookup
with a short in-process cache; results are a *suggestion* the admin confirms.
"""
import re
import time
import urllib.parse
import urllib.request
from json import load as _jsonload

USER_AGENT = "maegley-photo-album/1.0 (steve@maegley.com)"
NOMINATIM = "https://nominatim.openstreetmap.org/search?"
RATE_SECONDS = 1.1  # Nominatim usage policy: <= 1 req/sec

STATE_NAMES = {
    "CO": "Colorado", "MN": "Minnesota", "FL": "Florida", "WY": "Wyoming",
    "TN": "Tennessee", "CA": "California", "UT": "Utah", "SD": "South Dakota",
    "OH": "Ohio", "DC": "Washington, D.C.", "MO": "Missouri", "WI": "Wisconsin",
    "IN": "Indiana", "MT": "Montana", "NM": "New Mexico", "SC": "South Carolina",
    "NV": "Nevada", "VA": "Virginia", "AZ": "Arizona", "NY": "New York",
}
CA_PROVINCES = {"ontario", "quebec", "manitoba", "alberta", "british columbia"}

# Hand-pinned spots Nominatim resolves poorly (bare "Washington, DC" matches a
# Washington in TN under the US constraint). Keyed by lowercased name.
OVERRIDES: dict[str, tuple[float, float]] = {
    "washington, dc": (38.907192, -77.036873),
    "washington, d.c.": (38.907192, -77.036873),
}

_cache: dict[str, tuple | None] = {}


def clean_query(name: str) -> str:
    q = re.sub(r"\([^)]*\)", "", name)
    q = re.sub(r"^\s*(off|near|en route to|en route|in flight|aboard)\b[, ]*", "", q, flags=re.I)
    q = re.sub(r"\s*/\s*", " / ", q)
    q = re.sub(r",\s*([A-Z]{2})\b", lambda m: ", " + STATE_NAMES.get(m.group(1), m.group(1)), q)
    return re.sub(r"\s+", " ", q).strip().strip(",").strip()


def detect_country(name: str) -> str:
    low = name.lower()
    if "canada" in low or any(p in low for p in CA_PROVINCES):
        return "ca"
    return "us"


def detect_state(name: str):
    m = re.search(r",\s*([A-Z]{2})\b", name)
    if m and m.group(1) in STATE_NAMES:
        return STATE_NAMES[m.group(1)]
    low = name.lower()
    for full in STATE_NAMES.values():
        if full.lower() in low:
            return full
    return None


def fallback_queries(name: str):
    q = clean_query(name)
    state = detect_state(name)
    if q:
        yield q
    parts = [p.strip() for p in q.split(",") if p.strip()]
    if len(parts) >= 2 and parts[-2].lower() != (state or "").lower():
        yield f"{parts[-2]}, {state or parts[-1]}"
    if state:
        yield state


def _nominatim(query: str, country: str):
    ckey = f"{country}|{query}"
    if ckey in _cache:
        return _cache[ckey]
    params = {"q": query, "format": "json", "limit": 1, "addressdetails": 0}
    if country:
        params["countrycodes"] = country
    req = urllib.request.Request(NOMINATIM + urllib.parse.urlencode(params),
                                 headers={"User-Agent": USER_AGENT})
    time.sleep(RATE_SECONDS)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = _jsonload(r)
    except Exception:  # noqa: BLE001
        return None  # don't cache transient errors
    if not data:
        _cache[ckey] = None
        return None
    d = data[0]
    result = (float(d["lat"]), float(d["lon"]), d.get("display_name", ""))
    _cache[ckey] = result
    return result


def geocode(name: str):
    """Return {lat, lon, display_name, query, approximate} or None."""
    if not name or not name.strip():
        return None
    if name.strip().lower() in OVERRIDES:
        lat, lon = OVERRIDES[name.strip().lower()]
        return {"lat": lat, "lon": lon, "display_name": "Washington, D.C.",
                "query": name, "approximate": False}
    country = detect_country(name)
    primary = clean_query(name)
    for q in fallback_queries(name):
        hit = _nominatim(q, country)
        if hit:
            lat, lon, disp = hit
            return {"lat": lat, "lon": lon, "display_name": disp,
                    "query": q, "approximate": q != primary}
    return None
