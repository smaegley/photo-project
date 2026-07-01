import { useEffect, useRef, useState } from "react";
import { api } from "../api";

export default function Lightbox({ photos, index, onClose, onNav, onLoadMore,
                                  admin = false, isAdmin = false, events = [], people = [], places = [], magazines = [], onChanged }) {
  const [detail, setDetail] = useState(null);
  const [showNotes, setShowNotes] = useState(true);
  const [showCard, setShowCard] = useState(false);
  const [captionDraft, setCaptionDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const dirty = useRef(false);
  const dragRef = useRef(null);
  const photo = photos[index];

  const reload = () => api.photo(photo.id).then((d) => { setDetail(d); setCaptionDraft(d.caption || ""); });

  useEffect(() => {
    setDetail(null);
    setZoom(1); setPan({ x: 0, y: 0 }); // reset view per photo
    api.photo(photo.id).then((d) => { setDetail(d); setCaptionDraft(d.caption || ""); });
    if (index >= photos.length - 3) onLoadMore();
  }, [photo.id]); // eslint-disable-line

  const zoomBy = (f) => setZoom((z) => {
    const n = Math.min(Math.max(z * f, 1), 6);
    if (n === 1) setPan({ x: 0, y: 0 });
    return n;
  });
  const resetZoom = () => { setZoom(1); setPan({ x: 0, y: 0 }); };
  const onWheel = (e) => { e.preventDefault(); zoomBy(e.deltaY < 0 ? 1.15 : 1 / 1.15); };
  const onDown = (e) => { if (zoom > 1) dragRef.current = { x: e.clientX - pan.x, y: e.clientY - pan.y }; };
  const onMove = (e) => { if (dragRef.current) setPan({ x: e.clientX - dragRef.current.x, y: e.clientY - dragRef.current.y }); };
  const onUp = () => { dragRef.current = null; };

  const close = () => { if (dirty.current) onChanged?.(); onClose(); };
  const prev = () => index > 0 && onNav(index - 1);
  const next = () => index < photos.length - 1 && onNav(index + 1);

  useEffect(() => {
    const h = (e) => {
      if (e.key === "Escape") close();
      if (e.key === "ArrowLeft") prev();
      if (e.key === "ArrowRight") next();
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [index, photos.length, detail]); // eslint-disable-line

  async function edit(fn) {
    setBusy(true);
    try { await fn(); dirty.current = true; await reload(); }
    catch (e) { alert(e.message); }
    finally { setBusy(false); }
  }

  // rotate persists on disk + regenerates the thumb; reload() then returns a
  // fresh mtime-versioned image_url so the new orientation shows immediately.
  const rotate = (deg) => edit(() => api.rotatePhoto(photo.id, deg));
  const saveCaption = () => {
    if (!detail || captionDraft.trim() === (detail.caption || "")) return;
    edit(() => api.editCaption(photo.id, captionDraft.trim() || null));
  };
  const repOf = (personId) => people.find((pp) => pp.id === personId)?.representative_photo_id;

  const eventIdByName = Object.fromEntries(events.map((e) => [e.name, e.id]));
  // Lightbox shows the sized display derivative (originals can be 24MP); download
  // still serves the full-resolution original.
  const src = detail?.display_url || photo.display_url;
  const downloadSrc = detail?.image_url || photo.image_url;
  const roll = detail && magazines.find((mg) => mg.id === detail.magazine_id);
  const rollCards = roll?.card_image_paths || [];

  return (
    <div className={`lightbox ${showNotes ? "" : "notes-hidden"}`} onClick={close}>
      <button className="lb-close" onClick={close}>✕</button>
      {index > 0 && (
        <button className="lb-nav left" onClick={(e) => { e.stopPropagation(); prev(); }}>‹</button>
      )}
      {index < photos.length - 1 && (
        <button className="lb-nav right" onClick={(e) => { e.stopPropagation(); next(); }}>›</button>
      )}

      <div className="lb-stage" onClick={(e) => e.stopPropagation()}
           onWheel={onWheel} onMouseMove={onMove} onMouseUp={onUp} onMouseLeave={onUp}>
        <img src={src} alt={photo.caption || photo.source_file} draggable={false}
             className={zoom > 1 ? "zoomed" : ""}
             style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
                      cursor: zoom > 1 ? (dragRef.current ? "grabbing" : "grab") : "default" }}
             onMouseDown={onDown}
             onDoubleClick={() => (zoom > 1 ? resetZoom() : setZoom(2))} />

        <div className="lb-toolbar" onClick={(e) => e.stopPropagation()}>
          <button onClick={() => zoomBy(1 / 1.4)} disabled={zoom <= 1} title="Zoom out">−</button>
          <button className="lb-tool-pct" onClick={resetZoom} title="Fit to screen">{Math.round(zoom * 100)}%</button>
          <button onClick={() => zoomBy(1.4)} disabled={zoom >= 6} title="Zoom in">+</button>
          {admin && <span className="lb-tool-div" />}
          {admin && <button onClick={() => rotate(270)} disabled={busy} title="Rotate left">↺</button>}
          {admin && <button onClick={() => rotate(90)} disabled={busy} title="Rotate right">↻</button>}
        </div>
      </div>

      <aside className={`lb-notes ${showNotes ? "" : "hidden"}`} onClick={(e) => e.stopPropagation()}>
        <button className="lb-notes-toggle" onClick={() => setShowNotes((s) => !s)}>
          {showNotes ? "›" : "‹ Dad's notes"}
        </button>
        {showNotes && detail && (
          <div className="lb-notes-body">
            {admin ? (
              <textarea className="lb-caption-edit" value={captionDraft} placeholder="Add a caption…"
                        disabled={busy} onChange={(e) => setCaptionDraft(e.target.value)} onBlur={saveCaption} />
            ) : (
              <h3>{detail.caption || "—"}</h3>
            )}
            <div className="lb-meta">
              {detail.date_raw && <div><label>Date</label><span>{detail.date_raw}</span></div>}
              {detail.magazine_id && (
                <div><label>Roll</label><span>Mag {detail.magazine_id} · slide {detail.slide_in_mag}</span></div>
              )}
            </div>

            {/* ---- Place ---- */}
            <div className="lb-edit-sec">
              <label>Place</label>
              {admin ? (
                <div className="lb-edit-row">
                  <select value="" disabled={busy}
                          onChange={(e) => { const v = e.target.value; if (v) edit(() => api.bulkPlace([photo.id], v === "__clear__" ? null : v)); e.target.value = ""; }}>
                    <option value="">{detail.place ? detail.place.name : "— set place —"}</option>
                    {detail.place && <option value="__clear__">— clear place —</option>}
                    {[...places].sort((a, b) => a.name.localeCompare(b.name)).map((p) => (
                      <option key={p.id} value={p.id}>{p.name}</option>
                    ))}
                  </select>
                </div>
              ) : (
                <span>{detail.place ? detail.place.name : "—"}</span>
              )}
            </div>

            {/* ---- People ---- */}
            <div className="lb-edit-sec">
              <label>People</label>
              <div className="chips">
                {detail.people.map((p) => (
                  <span key={p.person_id} className={`chip ${p.uncertain ? "uncertain" : ""}`}>
                    {p.name}{p.uncertain ? "?" : ""}
                    {isAdmin && <button className={`chip-rep ${repOf(p.person_id) === photo.id ? "on" : ""}`}
                      disabled={busy} title="Set this photo as their thumbnail"
                      onClick={() => edit(() => api.setRepresentative(p.person_id, photo.id))}>★</button>}
                    {admin && <button className="chip-x" disabled={busy}
                      onClick={() => edit(() => api.bulkPerson([photo.id], p.person_id, "remove"))}>×</button>}
                  </span>
                ))}
                {detail.people.length === 0 && !admin && <span className="muted-dash">—</span>}
              </div>
              {admin && (
                <select value="" disabled={busy}
                        onChange={(e) => { const v = e.target.value; if (v) edit(() => api.bulkPerson([photo.id], v, "add")); e.target.value = ""; }}>
                  <option value="">+ add person…</option>
                  {[...people].filter((p) => !detail.people.some((dp) => dp.person_id === p.id))
                    .sort((a, b) => a.name.localeCompare(b.name))
                    .map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              )}
            </div>

            {/* ---- Events ---- */}
            <div className="lb-edit-sec">
              <label>Events</label>
              <div className="chips">
                {detail.events.map((name) => (
                  <span key={name} className="chip">
                    {name}
                    {admin && <button className="chip-x" disabled={busy}
                      onClick={() => edit(() => api.bulkEvent([photo.id], eventIdByName[name], "remove"))}>×</button>}
                  </span>
                ))}
                {detail.events.length === 0 && !admin && <span className="muted-dash">—</span>}
              </div>
              {admin && (
                <select value="" disabled={busy}
                        onChange={(e) => { const v = e.target.value; if (v) edit(() => api.bulkEvent([photo.id], Number(v), "add")); e.target.value = ""; }}>
                  <option value="">+ add event…</option>
                  {[...events].filter((ev) => !detail.events.includes(ev.name))
                    .sort((a, b) => a.name.localeCompare(b.name))
                    .map((ev) => <option key={ev.id} value={ev.id}>{ev.name}</option>)}
                </select>
              )}
            </div>

            {/* ---- Dad's roll index card ---- */}
            {rollCards.length > 0 && (
              <div className="lb-rollcard-sec">
                <button className="lb-rollcard-toggle" onClick={() => setShowCard((s) => !s)}>
                  📇 {showCard ? "Hide" : "Show"} roll card{rollCards.length > 1 ? "s" : ""} (Roll {roll.id})
                </button>
                {showCard && (
                  <div className="lb-rollcards">
                    {rollCards.map((c) => (
                      <a key={c} href={`/api/cards/${c}`} target="_blank" rel="noreferrer" title="Open full size">
                        <img className="lb-rollcard" src={`/api/cards/${c}`} alt={`Roll ${roll.id} index card`} loading="lazy" />
                      </a>
                    ))}
                  </div>
                )}
              </div>
            )}

            {detail.notes && <p className="lb-dadnotes">{detail.notes}</p>}
            <a className="lb-download" href={downloadSrc} download
               onClick={() => api.logUsage("download", photo.source_file)}>⬇ Download</a>
          </div>
        )}
      </aside>
    </div>
  );
}
