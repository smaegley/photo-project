import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";

// Draw a face box on the current photo, THEN say who it is (SPEC §14).
//
// Distinct from FaceCropEditor, which picks an existing person's thumbnail: this exists
// because a drawn box is now *data* — it feeds the face index and the match centroids —
// so tagging without geometry leaves the archive poorer than it looks. Draw-first also
// matches how the review queue works and handles "who is that?", where you can see the
// face before you can name it.
//
// Existing boxes on the photo are drawn underneath, which doubles as a mis-tag check:
// a labelled box sitting on a shoulder or a wall is visible at a glance.
export default function FaceTagEditor({ photo, detail, people, onClose, onSaved }) {
  const imgRef = useRef(null);
  const start = useRef(null);
  const nameRef = useRef(null);
  const [box, setBox] = useState(null);       // normalized top-left + size while drawing
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const existing = (detail?.people || []).filter((p) => p.region_w != null);
  const valid = box && box.w > 0.015 && box.h > 0.015;

  // Focus the name box the moment a box exists — the whole point is draw, type, enter.
  useEffect(() => { if (valid && !picked) nameRef.current?.focus(); }, [valid, picked]);

  const norm = (e) => {
    const r = imgRef.current.getBoundingClientRect();
    return {
      x: Math.min(Math.max((e.clientX - r.left) / r.width, 0), 1),
      y: Math.min(Math.max((e.clientY - r.top) / r.height, 0), 1),
    };
  };
  const down = (e) => {
    e.preventDefault();
    setPicked(null); setErr(null);
    start.current = norm(e);
    setBox({ ...start.current, w: 0, h: 0 });
  };
  const move = (e) => {
    if (!start.current) return;
    const p = norm(e), s = start.current;
    setBox({ x: Math.min(s.x, p.x), y: Math.min(s.y, p.y),
             w: Math.abs(p.x - s.x), h: Math.abs(p.y - s.y) });
  };
  const up = () => { start.current = null; };

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    const taken = new Set((detail?.people || []).map((p) => p.person_id));
    return [...people]
      .filter((p) => !taken.has(p.id) && (!q || p.name.toLowerCase().includes(q)))
      .sort((a, b) => a.name.localeCompare(b.name))
      .slice(0, 8);
  }, [people, query, detail]);

  async function save(personId) {
    if (!valid || !personId) return;
    setBusy(true); setErr(null);
    try {
      await api.setFaceRegion(personId, {
        photo_id: photo.id,
        x: box.x + box.w / 2, y: box.y + box.h / 2, w: box.w, h: box.h,
        // deliberately NOT set_representative: a routine face box must not hijack
        // this person's thumbnail. The ★ on the chip is still how you choose that.
      });
      onSaved();
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  }

  // Same slug rule as the face queue — person ids are caller-supplied (PersonCreate).
  const slugify = (n) =>
    (n || "").toLowerCase().trim().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");

  async function addNew() {
    const name = query.trim();
    const slug = slugify(name);
    if (!name || !slug) { setErr("Type a name first."); return; }
    setBusy(true); setErr(null);
    try {
      const p = await api.createPerson({ id: slug, canonical_name: name });
      await save(p.id || slug);
    } catch (e) { setErr(e.message); setBusy(false); }
  }

  return (
    <div className="modal-backdrop pin-backdrop" onClick={onClose}>
      <div className="modal facetag-modal" onClick={(e) => e.stopPropagation()}
           onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } }}>
        <div className="modal-head">
          <h2>Tag a face</h2>
          <button className="lb-close" onClick={onClose}>✕</button>
        </div>

        <div className="facecrop-wrap">
          <img ref={imgRef} className="facecrop-img" src={photo.display_url} alt=""
               draggable={false} onMouseDown={down} onMouseMove={move}
               onMouseUp={up} onMouseLeave={up} />
          {existing.map((p) => (
            <div key={p.person_id} className="facetag-existing"
                 style={{ left: `${(p.region_x - p.region_w / 2) * 100}%`,
                          top: `${(p.region_y - p.region_h / 2) * 100}%`,
                          width: `${p.region_w * 100}%`, height: `${p.region_h * 100}%` }}>
              <span>{p.name}</span>
            </div>
          ))}
          {box && (
            <div className="facecrop-box"
                 style={{ left: `${box.x * 100}%`, top: `${box.y * 100}%`,
                          width: `${box.w * 100}%`, height: `${box.h * 100}%` }} />
          )}
        </div>

        {!valid ? (
          <div className="pin-msg">Drag a box over a face to tag it.
            {existing.length > 0 && <> Boxes already on this photo are outlined in grey.</>}
          </div>
        ) : (
          <div className="facetag-name">
            <input ref={nameRef} type="text" placeholder="Who is this?" value={query}
                   disabled={busy}
                   onChange={(e) => { setQuery(e.target.value); setPicked(null); }}
                   onKeyDown={(e) => {
                     if (e.key === "Enter") {
                       e.preventDefault();
                       if (matches.length) save(matches[0].id);
                       else if (query.trim()) addNew();
                     }
                   }} />
            <div className="facetag-matches">
              {matches.map((p) => (
                <button key={p.id} className="ghost" disabled={busy}
                        onClick={() => { setPicked(p); save(p.id); }}>{p.name}</button>
              ))}
              {query.trim() && !matches.some((p) => p.name.toLowerCase() === query.trim().toLowerCase()) && (
                <button className="ghost primary" disabled={busy} onClick={addNew}>
                  + Add “{query.trim()}”
                </button>
              )}
            </div>
          </div>
        )}

        {err && <div className="error">{err}</div>}
        <div className="pin-foot">
          <span className="pin-coords">
            {valid ? "Pick a name, or type a new one and press Enter" : "No box drawn yet"}
          </span>
          <button className="ghost" onClick={onClose}>Done</button>
        </div>
      </div>
    </div>
  );
}
