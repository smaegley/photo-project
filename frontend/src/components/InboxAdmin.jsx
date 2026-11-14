import { useEffect, useState } from "react";
import { api } from "../api";

// Inbox (SPEC §15.8): photos the nightly ingest pulled from B2. Nobody but admins can
// see them until they are published.
//   Inbox        — photos showing someone the app knows, plus the rest of their outing
//   Not selected — everything else; Rescue moves photos (or a whole outing) to Inbox
// Photos are grouped by outing (close in time and place). Naming an outing tags its
// photos with that event as they are published. Click to mark, then Publish / Cull the
// marked ones — or act on a whole outing at once.
// Near-duplicate bursts show as one cell ("▸ 3 similar"): open it to choose, or use
// "keep only this" / "Keep best of stacks". Publishing never culls anything by itself.
//   Missing from B2 — existing photos whose master file has gone from the bucket
//                  (deleted in Lightroom, or a mistake). Only shown when there are any.
//   Likely junk  — no face, and it looks like a label, receipt, screenshot…; a guess,
//                  never culled automatically. "Cull all" confirms the lot.
const TABS = [["inbox", "Inbox"], ["junk", "Likely junk"], ["unselected", "Not selected"],
  ["missing", "Missing from B2"]];

const hhmm = (iso) => iso.slice(11, 16);
function when(g) {
  if (g.undated) return `${g.date || "No date"} (import date — no capture time)`;
  if (!g.start) return g.date || "No date";
  const d1 = g.start.slice(0, 10), d2 = g.end.slice(0, 10);
  if (g.start === g.end) return `${d1} ${hhmm(g.start)}`;
  return d1 === d2 ? `${d1} ${hhmm(g.start)}–${hhmm(g.end)}`
    : `${d1} ${hhmm(g.start)} – ${d2} ${hhmm(g.end)}`;
}

// Consecutive photos sharing a stack_id are one near-duplicate stack (app.stacks).
// `best` is the sharpest — the suggestion for which one to keep.
function stackItems(photos) {
  const items = [];
  for (const p of photos) {
    const last = items[items.length - 1];
    if (p.stack_id != null && last && last.stack === p.stack_id) last.photos.push(p);
    else items.push({ key: p.stack_id != null ? `s${p.stack_id}` : `p${p.id}`, stack: p.stack_id, photos: [p] });
  }
  for (const it of items) {
    it.best = it.photos.reduce((a, b) => ((b.sharpness ?? 0) > (a.sharpness ?? 0) ? b : a));
  }
  return items;
}
const stackExtras = (photos) => stackItems(photos).filter((it) => it.photos.length > 1)
  .flatMap((it) => it.photos.filter((p) => p.id !== it.best.id).map((p) => p.id));

function EventField({ group, events, busy, onSave }) {
  const [val, setVal] = useState(group.event_name || "");
  useEffect(() => { setVal(group.event_name || ""); }, [group.event_name]);
  const save = (v) => { if ((v || "") !== (group.event_name || "")) onSave(group, v); };
  const listId = `ev-${group.outing_id}`;
  return (
    <span className="inbox-event">
      <input value={val} placeholder="Event name (optional)" list={listId} disabled={busy}
             onChange={(e) => setVal(e.target.value)}
             onBlur={() => save(val.trim())}
             onKeyDown={(e) => { if (e.key === "Enter") e.target.blur(); }} />
      <datalist id={listId}>{events.map((n) => <option key={n} value={n} />)}</datalist>
      {!group.event_name && group.keywords.map((k) => (
        <button key={k.name} className="inbox-kw" disabled={busy} onClick={() => save(k.name)}
                title={k.known ? "Keyword in the files matches an existing event"
                  : "Keyword found in the files — use it as the event name"}>
          {k.known ? "★ " : ""}{k.name}
        </button>
      ))}
    </span>
  );
}

export default function InboxAdmin({ onClose, onChanged }) {
  const [tab, setTab] = useState("inbox");
  const [data, setData] = useState({ total: 0, groups: [], counts: {}, last_run: null });
  const [events, setEvents] = useState([]);
  const [marked, setMarked] = useState(() => new Set());
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [peek, setPeek] = useState(null);
  const [openStacks, setOpenStacks] = useState(() => new Set());
  const toggleStack = (k) => setOpenStacks((s) => {
    const n = new Set(s);
    if (n.has(k)) n.delete(k); else n.add(k);
    return n;
  });

  const load = (t = tab) => api.inbox(t).then((d) => { setData(d); setMarked(new Set()); });
  useEffect(() => { load(tab).catch((e) => setMsg(`⚠ ${e.message}`)); }, [tab]);
  useEffect(() => {
    api.events(false).then((ev) => setEvents(ev.map((e) => e.name).sort())).catch(() => {});
  }, []);

  const toggle = (id) => setMarked((s) => {
    const n = new Set(s);
    if (n.has(id)) n.delete(id); else n.add(id);
    return n;
  });
  const markGroup = (photos) => setMarked((s) => {
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
      setMsg(`✓ ${label} ${r.changed} photo${r.changed !== 1 ? "s" : ""}`
        + (r.event_tagged ? ` · ${r.event_tagged} tagged with their event` : ""));
      await load();
      await onChanged?.();
    } catch (e) { setMsg(`⚠ ${e.message}`); }
    finally { setBusy(false); }
  };
  const publish = (ids) => act("published", api.publishPhotos, ids);
  const cull = (ids) => act("culled", api.cullPhotos, ids);
  const rescue = (ids) => act("moved to Inbox:", api.rescuePhotos, ids);
  const ids = (g) => g.photos.map((p) => p.id);

  const nameOuting = async (g, name) => {
    setBusy(true); setMsg(null);
    try {
      const r = await api.setOutingEvent(g.outing_id, name);
      setMsg(name ? `✓ outing named “${r.event_name}”`
        + (r.retagged ? ` · ${r.retagged} already-published photo${r.retagged !== 1 ? "s" : ""} tagged` : "")
        : "✓ event name cleared");
      setData((d) => ({ ...d, groups: d.groups.map((x) => (x.key === g.key ? { ...x, event_name: r.event_name } : x)) }));
      if (r.retagged) await onChanged?.();
    } catch (e) { setMsg(`⚠ ${e.message}`); }
    finally { setBusy(false); }
  };

  const run = data.last_run;
  const runNote = run
    ? `Last ingest ${run.finished_at ? run.finished_at.slice(0, 16).replace("T", " ") + " UTC" : "still running"}`
      + ` — ${run.new} new${run.errors ? `, ${run.errors} file error${run.errors !== 1 ? "s" : ""}` : ""}`
      + (run.state === "failed" ? " — FAILED" : "")
    : "No ingest has run yet.";
  const isInbox = tab === "inbox";
  const isMissing = tab === "missing";
  const isJunk = tab === "junk";
  const canRescue = tab === "unselected" || isJunk;
  const allIds = data.groups.flatMap((g) => g.photos.map((p) => p.id));

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal modal-wide rotation-sweep inbox-admin" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Inbox <span className="modal-sub">{data.total} here · {marked.size} marked</span></h2>
          <button className="lb-close" onClick={onClose}>✕</button>
        </div>

        <div className="rotation-toolbar">
          <span className="inbox-tabs">
            {TABS.filter(([k]) => k !== "missing" || data.counts.missing || tab === "missing").map(([k, label]) => (
              <button key={k} className={`ghost ${tab === k ? "on" : ""}`} disabled={busy}
                      onClick={() => setTab(k)}>
                {label}{data.counts[k] != null ? ` (${data.counts[k]})` : ""}
              </button>
            ))}
          </span>
          <button className="ghost rotation-apply" onClick={() => cull([...marked])} disabled={busy || !marked.size}>
            Cull {marked.size || ""}
          </button>
          {isJunk && (
            <button className="ghost rotation-apply" onClick={() => cull(allIds)} disabled={busy || !allIds.length}>
              Cull all {allIds.length || ""}
            </button>
          )}
          {canRescue && (
            <button className="ghost" onClick={() => rescue([...marked])} disabled={busy || !marked.size}>
              Rescue {marked.size || ""}
            </button>
          )}
          {!isMissing && (
            <button className="ghost" onClick={() => publish([...marked])} disabled={busy || !marked.size}>
              Publish {marked.size || ""}
            </button>
          )}
          <span className="modal-sub inbox-help">
            {isInbox ? "Someone the app recognises, plus the rest of the same outing."
              : isMissing ? (data.reconcile_mode === "apply"
                ? "These files are no longer in B2. The photos were culled automatically; put a file back in Lightroom and its photo returns by itself the next night."
                : "These files are no longer in B2. Nothing has been changed (report-only mode): put the file back in Lightroom if it was a mistake, or cull the photo here if it was not.")
              : isJunk ? "No faces, and they look like labels, receipts, screenshots or similar. This is a guess: rescue anything it got wrong, then Cull all."
              : "No one recognised. Rescue what is worth keeping; the rest can stay here."}
            {" "}Click to mark; double-click to view larger. {runNote}
          </span>
        </div>

        <div className="inbox-days">
          {data.groups.map((g) => (
            <section key={g.key} className="inbox-day">
              <header className="inbox-day-head">
                <b>{isMissing ? "Master file missing from B2" : when(g)}</b>
                <span className="modal-sub">
                  {g.photos.length} photo{g.photos.length !== 1 ? "s" : ""}
                  {g.place ? ` · ${g.place}` : ""}
                  {g.already_published ? ` · ${g.already_published} already published` : ""}
                  {tab === "unselected" && g.has_faces ? " · has faces" : ""}
                </span>
                {g.outing_id != null && !isJunk && (
                  <EventField group={g} events={events} busy={busy} onSave={nameOuting} />
                )}
                <span className="inbox-acts">
                <button className="link" disabled={busy} onClick={() => markGroup(g.photos)}>
                  {g.photos.every((p) => marked.has(p.id)) ? "Unmark all" : "Mark all"}
                </button>
                {canRescue && (
                  <button className="link" disabled={busy} onClick={() => rescue(ids(g))}>
                    {isJunk ? "Rescue these" : "Rescue outing"}
                  </button>
                )}
                {!isMissing && !isJunk && <button className="link" disabled={busy} onClick={() => publish(ids(g))}>Publish outing</button>}
                {!isMissing && stackExtras(g.photos).length > 0 && (
                  <button className="link" disabled={busy} onClick={() => cull(stackExtras(g.photos))}
                          title="In every stack of similar shots in this outing, keep the sharpest and cull the others">
                    Keep best of stacks (cull {stackExtras(g.photos).length})
                  </button>
                )}
                {!isMissing && <button className="link" disabled={busy} onClick={() => cull(ids(g))}>Cull outing</button>}
                </span>
              </header>
              <div className="rotation-grid">
                {stackItems(g.photos).map((it) => {
                  const open = it.photos.length === 1 || openStacks.has(it.key);
                  const shown = open ? it.photos : [it.best];
                  return shown.map((p, i) => {
                    const isStack = it.photos.length > 1;
                    const on = open ? marked.has(p.id) : it.photos.every((x) => marked.has(x.id));
                    return (
                      <button key={p.id}
                              className={`rotation-cell ${on ? "marked" : ""} ${isStack ? "in-stack" : ""} ${isStack && open && p.id === it.best.id ? "stack-best" : ""}`}
                              onClick={() => (open ? toggle(p.id) : markGroup(it.photos))}
                              onDoubleClick={() => setPeek(p)} disabled={busy}
                              title={`${p.filename || p.source_file}${p.date_raw ? " · " + p.date_raw : ""}`
                                + `${p.suggested.length ? " · looks like " + p.suggested.join(", ") : ""}`
                                + (isStack && !open ? ` · ${it.photos.length} similar shots — click marks them all` : "")}>
                        <span className="rotation-frame">
                          <img src={p.thumb_url} alt={p.filename || p.source_file} loading="lazy" />
                        </span>
                        <span className="rotation-badge">{on ? "✓" : ""}</span>
                        {isStack && (!open || i === 0) && (
                          <span className="stack-chip" role="button" tabIndex={0}
                                title={open ? "Collapse this stack" : "Show all the similar shots"}
                                onClick={(e) => { e.stopPropagation(); toggleStack(it.key); }}>
                            {open ? "◂ close" : `▸ ${it.photos.length} similar`}
                          </span>
                        )}
                        {isStack && open && p.id === it.best.id && (
                          <span className="stack-chip stack-keep" role="button" tabIndex={0}
                                title="Cull the other shots in this stack and keep this one (the sharpest)"
                                onClick={(e) => { e.stopPropagation(); cull(it.photos.filter((x) => x.id !== p.id).map((x) => x.id)); }}>
                            ★ keep only this
                          </span>
                        )}
                        <span className="inbox-names">
                          {isMissing ? (p.visibility === "culled" ? "culled" : `still ${p.visibility}`)
                            : isJunk ? `junk score ${p.junk_score ?? "?"}`
                            : p.suggested.length ? p.suggested.slice(0, 3).join(", ")
                            : p.faces ? `${p.faces} unknown face${p.faces !== 1 ? "s" : ""}` : ""}
                        </span>
                      </button>
                    );
                  });
                })}
              </div>
            </section>
          ))}
          {data.groups.length === 0 && (
            <div className="modal-msg">{isInbox ? "The inbox is empty." : "Nothing here."}</div>
          )}
        </div>
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
