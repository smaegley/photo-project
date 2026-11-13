import { useEffect, useMemo, useState } from "react";
import { api } from "../api";

// Inbox (SPEC §15.8, slice 3): photos the nightly ingest pulled from B2. Nobody but
// admins can see them until they are published. Photos are grouped by day; click to
// mark, then Publish or Cull the marked ones — or act on a whole day at once.
// (Outing grouping, the junk tab and stacks arrive in later slices.)
export default function InboxAdmin({ onClose, onChanged }) {
  const [data, setData] = useState({ total: 0, photos: [], last_run: null });
  const [marked, setMarked] = useState(() => new Set());
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [peek, setPeek] = useState(null);

  const load = () => api.inbox().then((d) => { setData(d); setMarked(new Set()); });
  useEffect(() => { load().catch((e) => setMsg(`⚠ ${e.message}`)); }, []);

  const days = useMemo(() => {
    const by = new Map();
    for (const p of data.photos) {
      const k = p.date || "No date";
      if (!by.has(k)) by.set(k, []);
      by.get(k).push(p);
    }
    return [...by.entries()];
  }, [data.photos]);

  const toggle = (id) => setMarked((s) => {
    const n = new Set(s);
    if (n.has(id)) n.delete(id); else n.add(id);
    return n;
  });
  const markDay = (photos) => setMarked((s) => {
    const n = new Set(s);
    const all = photos.every((p) => n.has(p.id));
    photos.forEach((p) => (all ? n.delete(p.id) : n.add(p.id)));
    return n;
  });

  const act = async (label, fn, ids) => {
    if (!ids.length) return;
    setBusy(true); setMsg(null);
    try {
      const r = await fn(ids);
      setMsg(`✓ ${label} ${r.changed} photo${r.changed !== 1 ? "s" : ""}`);
      await load();
      await onChanged?.();
    } catch (e) { setMsg(`⚠ ${e.message}`); }
    finally { setBusy(false); }
  };
  const publish = (ids) => act("published", api.publishPhotos, ids);
  const cull = (ids) => act("culled", api.cullPhotos, ids);

  const run = data.last_run;
  const runNote = run
    ? `Last ingest ${run.finished_at ? run.finished_at.slice(0, 16).replace("T", " ") + " UTC" : "still running"}`
      + ` — ${run.new} new${run.errors ? `, ${run.errors} file error${run.errors !== 1 ? "s" : ""}` : ""}`
      + (run.state === "failed" ? " — FAILED" : "")
    : "No ingest has run yet.";

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal modal-wide rotation-sweep inbox-admin" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Inbox <span className="modal-sub">{data.total} waiting · {marked.size} marked</span></h2>
          <button className="lb-close" onClick={onClose}>✕</button>
        </div>

        <div className="rotation-toolbar">
          <span className="modal-sub">
            Only admins see these. Click to mark; double-click to view larger. {runNote}
          </span>
          <button className="ghost rotation-apply" onClick={() => cull([...marked])} disabled={busy || !marked.size}>
            Cull {marked.size || ""}
          </button>
          <button className="ghost" onClick={() => publish([...marked])} disabled={busy || !marked.size}>
            Publish {marked.size || ""}
          </button>
        </div>

        <div className="inbox-days">
          {days.map(([day, photos]) => (
            <section key={day} className="inbox-day">
              <header className="inbox-day-head">
                <b>{day}</b>
                <span className="modal-sub">
                  {photos.length} photo{photos.length !== 1 ? "s" : ""}
                  {(() => { const pl = [...new Set(photos.map((p) => p.place).filter(Boolean))]; return pl.length ? ` · ${pl.join(", ")}` : ""; })()}
                </span>
                <button className="link" disabled={busy} onClick={() => markDay(photos)}>
                  {photos.every((p) => marked.has(p.id)) ? "Unmark day" : "Mark day"}
                </button>
                <button className="link" disabled={busy} onClick={() => publish(photos.map((p) => p.id))}>
                  Publish day
                </button>
              </header>
              <div className="rotation-grid">
                {photos.map((p) => (
                  <button key={p.id} className={`rotation-cell ${marked.has(p.id) ? "marked" : ""}`}
                          onClick={() => toggle(p.id)} onDoubleClick={() => setPeek(p)} disabled={busy}
                          title={`${p.filename || p.source_file}${p.date_raw ? " · " + p.date_raw : ""}`
                            + `${p.suggested.length ? " · looks like " + p.suggested.join(", ") : ""}`}>
                    <span className="rotation-frame">
                      <img src={p.thumb_url} alt={p.filename || p.source_file} loading="lazy" />
                    </span>
                    <span className="rotation-badge">{marked.has(p.id) ? "✓" : ""}</span>
                    <span className="inbox-names">
                      {p.suggested.length ? p.suggested.slice(0, 3).join(", ")
                        : p.faces ? `${p.faces} unknown face${p.faces !== 1 ? "s" : ""}` : ""}
                    </span>
                  </button>
                ))}
              </div>
            </section>
          ))}
          {data.photos.length === 0 && <div className="modal-msg">The inbox is empty.</div>}
        </div>
        {data.total > data.photos.length && (
          <div className="modal-msg">Showing the first {data.photos.length} of {data.total}; publish or cull some to see more.</div>
        )}
        {msg && <div className="modal-msg">{msg}</div>}
      </div>

      {peek && (
        <div className="lb-backfull" onClick={(e) => { e.stopPropagation(); setPeek(null); }}>
          <img src={peek.display_url} alt={peek.filename || peek.source_file}
               style={{ maxWidth: "94vw", maxHeight: "94vh", objectFit: "contain" }} />
        </div>
      )}
    </div>
  );
}
