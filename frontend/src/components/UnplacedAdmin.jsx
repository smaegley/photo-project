import { useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import { api } from "../api";

// Name the places the importer couldn't resolve (SPEC §3.4, §13.9).
//
// The gazetteer is curated — importers never auto-create places — so a photo taken
// somewhere unnamed keeps its GPS and no place_id. This panel is the other half of that
// loop: the server clusters those loose points (671 digital photos -> 26 locations), the
// map shows where they are, and naming one creates the Place and immediately claims
// every unplaced photo within 25km, which is the same proximity rule the importer uses.
//
// Deliberately not a pin editor: the coordinates already exist and are the photos' own
// GPS. The only thing missing is the human's name for the spot.

const OSM_STYLE = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors",
    },
  },
  layers: [{ id: "osm", type: "raster", source: "osm" }],
};

export default function UnplacedAdmin({ onClose, onChanged }) {
  const [clusters, setClusters] = useState(null);
  const [drafts, setDrafts] = useState({});   // idx -> {name, region}
  const [busy, setBusy] = useState(null);
  const [done, setDone] = useState({});       // idx -> "12 photos → Boulder"
  const [err, setErr] = useState(null);
  const [active, setActive] = useState(0);
  const mapEl = useRef(null);
  const map = useRef(null);
  const markers = useRef([]);

  const load = () =>
    api.unresolvedLocations()
      .then((d) => setClusters(d))
      .catch((e) => setErr(String(e)));

  useEffect(() => { load(); }, []);

  // init map once the container exists
  useEffect(() => {
    if (!mapEl.current || map.current) return;
    map.current = new maplibregl.Map({
      container: mapEl.current,
      style: OSM_STYLE,
      center: [-98, 39],
      zoom: 2.6,
      attributionControl: { compact: true },
    });
    map.current.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    return () => { map.current?.remove(); map.current = null; };
  }, []);

  // (re)draw pins whenever the cluster list changes
  useEffect(() => {
    if (!map.current || !clusters) return;
    markers.current.forEach((mk) => mk.remove());
    markers.current = [];
    const bounds = new maplibregl.LngLatBounds();
    clusters.forEach((c, i) => {
      const el = document.createElement("button");
      el.className = "unplaced-pin" + (done[i] ? " placed" : "") + (i === active ? " active" : "");
      // area ∝ count so a 421-photo cluster reads as bigger without dwarfing the rest
      const size = Math.max(22, Math.min(56, 14 + Math.sqrt(c.count) * 2.2));
      el.style.width = el.style.height = `${size}px`;
      el.textContent = c.count;
      el.title = `${c.count} photos · ${c.first_year ?? "?"}–${c.last_year ?? "?"}`;
      el.onclick = () => { setActive(i); flyTo(c); };
      markers.current.push(new maplibregl.Marker({ element: el }).setLngLat([c.lon, c.lat]).addTo(map.current));
      bounds.extend([c.lon, c.lat]);
    });
    if (clusters.length) map.current.fitBounds(bounds, { padding: 60, maxZoom: 9, duration: 0 });
  }, [clusters, done, active]);

  const flyTo = (c) => map.current?.flyTo({ center: [c.lon, c.lat], zoom: 10 });

  const setDraft = (i, patch) =>
    setDrafts((d) => ({ ...d, [i]: { ...(d[i] || {}), ...patch } }));

  async function save(i) {
    const c = clusters[i];
    const name = (drafts[i]?.name || "").trim();
    if (!name) return;
    setBusy(i); setErr(null);
    try {
      // One place at the cluster's own coordinates, then let it claim its neighbours —
      // same 25km rule the importer applies, so this matches what a re-import would do.
      const place = await api.createPlace({
        name, region: (drafts[i]?.region || "").trim() || null,
        precision: "city", lat: c.lat, lon: c.lon,
      });
      const res = await api.claimNearby(place.id);
      setDone((d) => ({ ...d, [i]: `${res.claimed} photos → ${name}` }));
      await load();          // remaining clusters shrink as photos get claimed
      onChanged?.();
    } catch (e) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(null);
    }
  }

  const remaining = clusters?.reduce((n, c) => n + c.count, 0) ?? 0;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal wide unplaced-admin" onClick={(e) => e.stopPropagation()}>
        <header>
          <h2>Unnamed locations</h2>
          <button className="icon" onClick={onClose} aria-label="Close">✕</button>
        </header>

        <p className="hint">
          These photos carry GPS but no named place. Name a spot and every unplaced photo
          within 25&nbsp;km joins it — the same rule the importer uses.
          {clusters && <> <b>{clusters.length}</b> locations, <b>{remaining.toLocaleString()}</b> photos.</>}
        </p>

        {err && <p className="error">{err}</p>}

        <div className="unplaced-body">
          <div className="unplaced-map" ref={mapEl} />

          <ol className="unplaced-list">
            {!clusters && <li className="muted">Loading…</li>}
            {clusters?.length === 0 && <li className="muted">Everything with GPS has a place. 🎉</li>}
            {clusters?.map((c, i) => (
              <li key={`${c.lat},${c.lon}`} className={i === active ? "active" : ""}
                  onMouseEnter={() => setActive(i)}>
                <div className="unplaced-meta">
                  <b>{c.count.toLocaleString()}</b> photos
                  <span className="muted"> · {c.first_year ?? "?"}–{c.last_year ?? "?"}</span>
                  <button className="link" onClick={() => flyTo(c)}>show</button>
                </div>
                <div className="muted coords">{c.lat.toFixed(4)}, {c.lon.toFixed(4)}</div>
                {done[i] ? (
                  <div className="ok">✓ {done[i]}</div>
                ) : (
                  <div className="unplaced-form">
                    <input placeholder="Place name (e.g. Boulder)" value={drafts[i]?.name || ""}
                           onChange={(e) => setDraft(i, { name: e.target.value })}
                           onKeyDown={(e) => e.key === "Enter" && save(i)} disabled={busy === i} />
                    <input placeholder="Region (optional)" value={drafts[i]?.region || ""}
                           onChange={(e) => setDraft(i, { region: e.target.value })}
                           onKeyDown={(e) => e.key === "Enter" && save(i)} disabled={busy === i} />
                    <button onClick={() => save(i)} disabled={busy === i || !(drafts[i]?.name || "").trim()}>
                      {busy === i ? "Saving…" : "Save"}
                    </button>
                  </div>
                )}
              </li>
            ))}
          </ol>
        </div>
      </div>
    </div>
  );
}
