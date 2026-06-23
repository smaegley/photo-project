import { useMemo, useState } from "react";
import { api } from "../api";

// Vocabulary cleanup (SPEC §3.5): rename, merge, delete, create events. Merge is
// the heavy hitter — re-points every tagged photo from one event onto another.
export default function EventsAdmin({ events, onClose, onChanged }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [mergeFrom, setMergeFrom] = useState(null); // event being merged
  const [pendingTarget, setPendingTarget] = useState(null); // chosen target, awaiting confirm
  const [q, setQ] = useState("");
  const [newName, setNewName] = useState("");

  const cancelMerge = () => { setMergeFrom(null); setPendingTarget(null); };

  const list = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return [...events]
      .filter((e) => !needle || e.name.toLowerCase().includes(needle))
      .sort((a, b) => b.photo_count - a.photo_count);
  }, [events, q]);

  async function run(label, fn) {
    setBusy(true); setMsg(null);
    try { const r = await fn(); setMsg(`✓ ${label}`); await onChanged(); return r; }
    catch (e) { setMsg(`⚠ ${e.message}`); }
    finally { setBusy(false); }
  }

  const rename = (ev) => {
    const name = window.prompt(`Rename "${ev.name}" to:`, ev.name);
    if (name && name.trim() && name !== ev.name) run(`renamed to ${name}`, () => api.renameEvent(ev.id, name.trim()));
  };
  const del = (ev) => {
    if (window.confirm(`Delete "${ev.name}" and remove it from ${ev.photo_count} photo(s)? This cannot be undone.`))
      run(`deleted ${ev.name}`, () => api.deleteEvent(ev.id));
  };
  const confirmMerge = () => {
    const into = pendingTarget;
    run(`merged ${mergeFrom.name} → ${into.name}`, () => api.mergeEvent(mergeFrom.id, into.id));
    cancelMerge();
  };
  const create = () => {
    if (newName.trim()) run(`created ${newName}`, () => api.createEvent(newName.trim())).then(() => setNewName(""));
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Manage events <span className="modal-sub">{events.length}</span></h2>
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
              <span>Merge <b>{mergeFrom.name}</b> ({mergeFrom.photo_count}) into which event? Pick one below.</span>
            )}
            <button className="link merge-back" onClick={cancelMerge}>← Back to events</button>
          </div>
        )}

        <div className="modal-search">
          <input autoFocus value={q}
                 placeholder={mergeFrom ? "Search for the event to merge into…" : "Search events…"}
                 onChange={(e) => setQ(e.target.value)} />
        </div>

        <div className="event-admin-list">
          {list.map((ev) => (
            <div className={`event-admin-row ${mergeFrom && mergeFrom.id !== ev.id ? "merge-target" : ""} ${pendingTarget && pendingTarget.id === ev.id ? "pending" : ""}`}
                 key={ev.id}
                 onClick={() => mergeFrom && mergeFrom.id !== ev.id && setPendingTarget(ev)}>
              <span className="event-admin-name">{ev.name}</span>
              <span className="event-admin-count">{ev.photo_count}</span>
              {!mergeFrom && (
                <span className="event-admin-actions">
                  <button className="link" onClick={() => rename(ev)} disabled={busy}>rename</button>
                  <button className="link" onClick={() => setMergeFrom(ev)} disabled={busy || ev.photo_count === 0}>merge</button>
                  <button className="link danger" onClick={() => del(ev)} disabled={busy}>delete</button>
                </span>
              )}
            </div>
          ))}
          {list.length === 0 && <div className="modal-msg">No events match “{q}”.</div>}
        </div>

        <div className="event-admin-new">
          <input value={newName} placeholder="New event name…"
                 onChange={(e) => setNewName(e.target.value)}
                 onKeyDown={(e) => e.key === "Enter" && create()} />
          <button className="ghost" onClick={create} disabled={busy || !newName.trim()}>Add</button>
        </div>

        {msg && <div className="modal-msg">{msg}</div>}
      </div>
    </div>
  );
}
