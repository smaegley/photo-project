import { useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import { api } from "../api";

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

// In-app location tagging (SPEC §3.4): look up by name (Nominatim) or click/drag a
// pin to set a place's lat/lon. Setting it fixes every photo that shares the place.
export default function PinEditor({ place, onClose, onSaved }) {
  const ref = useRef(null);
  const map = useRef(null);
  const marker = useRef(null);
  const hasPin = place.lat != null;
  const [pos, setPos] = useState(hasPin ? { lng: place.lon, lat: place.lat } : null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);

  const placeMarker = (lngLat) => {
    setPos(lngLat);
    if (!marker.current) {
      const el = document.createElement("div");
      el.className = "pin-marker";
      marker.current = new maplibregl.Marker({ element: el, draggable: true, anchor: "bottom" })
        .setLngLat(lngLat).addTo(map.current);
      marker.current.on("dragend", () => setPos(marker.current.getLngLat()));
    } else {
      marker.current.setLngLat(lngLat);
    }
  };

  useEffect(() => {
    map.current = new maplibregl.Map({
      container: ref.current,
      style: OSM_STYLE,
      center: hasPin ? [place.lon, place.lat] : [-98, 39],
      zoom: hasPin ? 10 : 3,
    });
    map.current.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.current.on("load", () => map.current.resize());
    if (hasPin) placeMarker({ lng: place.lon, lat: place.lat });
    map.current.on("click", (e) => placeMarker(e.lngLat));
    return () => { map.current?.remove(); map.current = null; marker.current = null; };
  }, []); // eslint-disable-line

  async function lookup() {
    setBusy(true); setMsg("Looking up…");
    try {
      const r = await api.geocode(place.name);
      if (r && r.found !== false && r.lat != null) {
        placeMarker({ lng: r.lon, lat: r.lat });
        map.current.flyTo({ center: [r.lon, r.lat], zoom: 10, duration: 700 });
        setMsg(`✓ Found${r.approximate ? " (approximate — check it)" : ""}: ${(r.display_name || "").slice(0, 64)}`);
      } else {
        setMsg("No match found — drop the pin manually.");
      }
    } catch (e) { setMsg(`⚠ ${e.message}`); }
    finally { setBusy(false); }
  }

  async function save() {
    if (!pos) return;
    setBusy(true);
    try {
      await api.updatePlace(place.id, { lat: pos.lat, lon: pos.lng });
      await onSaved();
      onClose();
    } catch (e) { setMsg(`⚠ ${e.message}`); setBusy(false); }
  }

  return (
    <div className="modal-backdrop pin-backdrop" onClick={onClose}>
      <div className="modal pin-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Set location <span className="modal-sub">{place.name}</span></h2>
          <button className="lb-close" onClick={onClose}>✕</button>
        </div>
        <div className="pin-map" ref={ref} />
        {msg && <div className="pin-msg">{msg}</div>}
        <div className="pin-foot">
          <button className="ghost" onClick={lookup} disabled={busy}>🔍 Look up coordinates</button>
          <span className="pin-coords">
            {pos ? `📍 ${pos.lat.toFixed(5)}, ${pos.lng.toFixed(5)}` : "Click the map or look up by name"}
          </span>
          <button className="link" onClick={onClose}>Cancel</button>
          <button className="ghost primary" onClick={save} disabled={busy || !pos}>Save location</button>
        </div>
      </div>
    </div>
  );
}
