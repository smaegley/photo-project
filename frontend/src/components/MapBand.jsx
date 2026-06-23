import { useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";

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

export default function MapBand({ places, selectedPlaces, bbox, pinned, onTogglePin, onClose, onSelectPlace, onBbox }) {
  const ref = useRef(null);
  const map = useRef(null);
  const markers = useRef([]);
  const states = useRef(null); // cached US-states GeoJSON (loaded on demand)
  const [drawing, setDrawing] = useState(false);
  const drawStart = useRef(null);

  // init map once
  useEffect(() => {
    map.current = new maplibregl.Map({
      container: ref.current,
      style: OSM_STYLE,
      center: [-100, 40],
      zoom: 3,
    });
    map.current.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.current.on("load", () => {
      // shaded outline for a selected region-level place (e.g. a whole state)
      map.current.addSource("region", { type: "geojson", data: emptyFC() });
      map.current.addLayer({ id: "region-fill", type: "fill", source: "region",
        paint: { "fill-color": "#2563eb", "fill-opacity": 0.12 } });
      map.current.addLayer({ id: "region-line", type: "line", source: "region",
        paint: { "line-color": "#2563eb", "line-width": 2 } });
      map.current.addSource("selbox", { type: "geojson", data: emptyFC() });
      map.current.addLayer({ id: "selbox-fill", type: "fill", source: "selbox",
        paint: { "fill-color": "#2563eb", "fill-opacity": 0.12 } });
      map.current.addLayer({ id: "selbox-line", type: "line", source: "selbox",
        paint: { "line-color": "#2563eb", "line-width": 1.5 } });
      fitToPlaces();
    });
    return () => map.current?.remove();
  }, []);

  // (re)draw markers when places / selection change
  useEffect(() => {
    if (!map.current) return;
    markers.current.forEach((m) => m.remove());
    markers.current = places.map((pl) => {
      const el = document.createElement("div");
      el.className = "map-pin" + (selectedPlaces.includes(pl.id) ? " sel" : "");
      el.title = `${pl.name} · ${pl.photo_count}`;
      el.onclick = (e) => { e.stopPropagation(); onSelectPlace(pl.id); };
      return new maplibregl.Marker({ element: el }).setLngLat([pl.lon, pl.lat]).addTo(map.current);
    });
  }, [places, selectedPlaces]);

  function fitToPlaces() {
    const pts = places.filter((p) => p.lat != null);
    if (!pts.length) return;
    const b = new maplibregl.LngLatBounds();
    pts.forEach((p) => b.extend([p.lon, p.lat]));
    map.current.fitBounds(b, { padding: 40, maxZoom: 8, duration: 0 });
  }

  async function loadStates() {
    if (!states.current) {
      states.current = await (await fetch("/us-states.geojson")).json();
    }
    return states.current;
  }

  // re-frame on selected place(s); for region-level places, shade the state
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    const sel = places.filter((p) => selectedPlaces.includes(p.id) && p.lat != null);
    let cancelled = false;

    const apply = async () => {
      // shade any region-level selections we can match to a US state
      const regionSel = sel.filter((p) => p.precision === "region");
      let feats = [];
      if (regionSel.length) {
        const gj = await loadStates();
        if (cancelled) return;
        feats = regionSel.map((p) => matchState(gj, p.name)).filter(Boolean);
      }
      m.getSource("region")?.setData({ type: "FeatureCollection", features: feats });

      if (sel.length === 0) return; // leave the view when nothing is picked
      if (sel.length === 1 && feats.length === 1) {
        m.fitBounds(featureBounds(feats[0]), { padding: 40, maxZoom: 9, duration: 700 });
      } else if (sel.length === 1) {
        const p = sel[0];
        const z = { exact: 12, landmark: 12, city: 10, region: 6, unknown: 8 }[p.precision] || 9;
        m.flyTo({ center: [p.lon, p.lat], zoom: z, duration: 700 });
      } else {
        const b = new maplibregl.LngLatBounds();
        sel.forEach((p) => b.extend([p.lon, p.lat]));
        feats.forEach((f) => extendBounds(b, f));
        m.fitBounds(b, { padding: 70, maxZoom: 11, duration: 700 });
      }
    };

    m.loaded() ? apply() : m.once("load", apply);
    return () => { cancelled = true; };
  }, [selectedPlaces, places]); // eslint-disable-line

  // box-draw interactions
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    const down = (e) => {
      if (!drawing) return;
      drawStart.current = e.lngLat;
      m.dragPan.disable();
    };
    const move = (e) => {
      if (!drawing || !drawStart.current) return;
      m.getSource("selbox")?.setData(boxFC(drawStart.current, e.lngLat));
    };
    const up = (e) => {
      if (!drawing || !drawStart.current) return;
      const a = drawStart.current, b = e.lngLat;
      onBbox([Math.min(a.lng, b.lng), Math.min(a.lat, b.lat), Math.max(a.lng, b.lng), Math.max(a.lat, b.lat)]);
      drawStart.current = null;
      setDrawing(false);
      m.dragPan.enable();
    };
    m.on("mousedown", down); m.on("mousemove", move); m.on("mouseup", up);
    return () => { m.off("mousedown", down); m.off("mousemove", move); m.off("mouseup", up); };
  }, [drawing]);

  // clear the drawn rectangle when bbox is cleared
  useEffect(() => {
    if (!bbox && map.current?.getSource("selbox")) map.current.getSource("selbox").setData(emptyFC());
  }, [bbox]);

  return (
    <div className="mapband">
      <div ref={ref} className="map" />
      <div className="map-tools">
        <button className={drawing ? "on" : ""} onClick={() => setDrawing((d) => !d)}>▭ Draw area</button>
        {bbox && <button onClick={() => onBbox(null)}>Clear area</button>}
        <button className={pinned ? "on" : ""} onClick={onTogglePin} title="Keep map open">📌</button>
        {!pinned && <button onClick={onClose}>Hide ✕</button>}
      </div>
    </div>
  );
}

const emptyFC = () => ({ type: "FeatureCollection", features: [] });

// match a place name to a US-state feature (longest name wins: "West Virginia" > "Virginia")
function matchState(gj, name) {
  if (!name) return null;
  const n = name.toLowerCase();
  const cands = gj.features.filter((f) => n.includes(f.properties.name.toLowerCase()));
  cands.sort((a, b) => b.properties.name.length - a.properties.name.length);
  return cands[0] || null;
}

function extendBounds(b, feat) {
  const ring = (r) => r.forEach(([lng, lat]) => b.extend([lng, lat]));
  const g = feat.geometry;
  if (g.type === "Polygon") g.coordinates.forEach(ring);
  else if (g.type === "MultiPolygon") g.coordinates.forEach((poly) => poly.forEach(ring));
  return b;
}

function featureBounds(feat) {
  return extendBounds(new maplibregl.LngLatBounds(), feat);
}

function boxFC(a, b) {
  const x1 = a.lng, y1 = a.lat, x2 = b.lng, y2 = b.lat;
  return { type: "FeatureCollection", features: [{ type: "Feature", geometry: {
    type: "Polygon", coordinates: [[[x1, y1], [x2, y1], [x2, y2], [x1, y2], [x1, y1]]] } }] };
}
