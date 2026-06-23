import { useMemo, useState } from "react";
import { api } from "../api";
import PinEditor from "./PinEditor";

const PRECISIONS = ["exact", "landmark", "city", "region", "unknown"];

// Gazetteer cleanup (SPEC §3.4/§3.5): rename, merge, delete, create places, plus
// quick precision edits. Merge re-points every photo's place_id onto the target.
export default function PlacesAdmin({ places, onClose, onChanged }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [mergeFrom, setMergeFrom] = useState(null);
  const [pendingTarget, setPendingTarget] = useState(null);
  const [pinning, setPinning] = useState(null); // place whose location is being set
  const [q, setQ] = useState("");
  const [newName, setNewName] = useState("");

  const cancelMerge = () => { setMergeFrom(null); setPendingTarget(null); };

  const list = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return [...places]
      .filter((p) => !needle || p.name.toLowerCase().includes(needle))
      .sort((a, b) => (b.photo_count || 0) - (a.photo_count || 0));
  }, [places, q]);

  async function run(label, fn) {
    setBusy(true); setMsg(null);
    try { const r = await fn(); setMsg(`✓ ${label}`); await onChanged(); return r; }
    catch (e) { setMsg(`⚠ ${e.message}`); }
    finally { setBusy(false); }
  }

  const rename = (pl) => {
    const name = window.prompt(`Rename "${pl.name}" to:`, pl.name);
    if (name && name.trim() && name !== pl.name) run(`renamed to ${name}`, () => api.updatePlace(pl.id, { name: name.trim() }));
  };
  const setPrecision = (pl, precision) =>
    run(`${pl.name} → ${precision}`, () => api.updatePlace(pl.id, { precision }));
  const del = (pl) => {
    if (window.confirm(`Delete "${pl.name}"? ${pl.photo_count} photo(s) will lose their place. This cannot be undone.`))
      run(`deleted ${pl.name}`, () => api.deletePlace(pl.id));
  };
  const confirmMerge = () => {
    const into = pendingTarget;
    run(`merged ${mergeFrom.name} → ${into.name}`, () => api.mergePlace(mergeFrom.id, into.id));
    cancelMerge();
  };
  const create = () => {
    if (newName.trim()) run(`created ${newName}`, () => api.createPlace({ name: newName.trim() })).then(() => setNewName(""));
  };

  return (
    <>
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Manage places <span className="modal-sub">{places.length}</span></h2>
          <button className="lb-close" onClick={onClose}>✕</button>
        </div>

        {mergeFrom && (
          <div className="merge-banner">
            {pendingTarget ? (
              <>
                Merge <b>{mergeFrom.name}</b> ({mergeFrom.photo_count}) into <b>{pendingTarget.name}</b>?
                <button className="ghost merge-ok" onClick={confirmMerge} disabled={busy}>Confirm merge</button>
                <button className="link" onClick={() => setPendingTarget(null)}>pick another</button>
              </>
            ) : (
              <span>Merge <b>{mergeFrom.name}</b> ({mergeFrom.photo_count}) into which place? Pick one below.</span>
            )}
            <button className="link merge-back" onClick={cancelMerge}>← Back to places</button>
          </div>
        )}
        <div className="modal-search">
          <input autoFocus value={q}
                 placeholder={mergeFrom ? "Search for the place to merge into…" : "Search places…"}
                 onChange={(e) => setQ(e.target.value)} />
        </div>

        <div className="event-admin-list">
          {list.map((pl) => (
            <div className={`event-admin-row ${mergeFrom && mergeFrom.id !== pl.id ? "merge-target" : ""} ${pendingTarget && pendingTarget.id === pl.id ? "pending" : ""}`}
                 key={pl.id}
                 onClick={() => mergeFrom && mergeFrom.id !== pl.id && setPendingTarget(pl)}>
              <span className="event-admin-name">
                {pl.name}
                {pl.lat == null && <span className="pin-warn" title="No map pin">⚲ no pin</span>}
              </span>
              <span className="event-admin-count">{pl.photo_count}</span>
              {!mergeFrom && (
                <span className="event-admin-actions">
                  <select className="prec-select" value={pl.precision} disabled={busy}
                          onChange={(e) => setPrecision(pl, e.target.value)} title="Precision">
                    {PRECISIONS.map((p) => <option key={p} value={p}>{p}</option>)}
                  </select>
                  <button className="link" onClick={() => rename(pl)} disabled={busy}>rename</button>
                  <button className="link" onClick={() => setPinning(pl)} disabled={busy}>📍 location</button>
                  <button className="link" onClick={() => setMergeFrom(pl)} disabled={busy || pl.photo_count === 0}>merge</button>
                  <button className="link danger" onClick={() => del(pl)} disabled={busy}>delete</button>
                </span>
              )}
            </div>
          ))}
          {list.length === 0 && <div className="modal-msg">No places match “{q}”.</div>}
        </div>

        <div className="event-admin-new">
          <input value={newName} placeholder="New place name…"
                 onChange={(e) => setNewName(e.target.value)}
                 onKeyDown={(e) => e.key === "Enter" && create()} />
          <button className="ghost" onClick={create} disabled={busy || !newName.trim()}>Add</button>
        </div>

        {msg && <div className="modal-msg">{msg}</div>}
      </div>
    </div>
    {pinning && (
      <PinEditor place={pinning} onClose={() => setPinning(null)} onSaved={onChanged} />
    )}
    </>
  );
}
