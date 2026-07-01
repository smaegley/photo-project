import { useState } from "react";
import { api } from "../api";

// Bulk apply/reject across a selection (SPEC §3.5 step 2). Sits above the gallery
// in admin mode. "Select all" pulls every id matching the current filter so you
// can, e.g., filter to an event and strip it from the whole wrong group at once.
export default function AdminBar({ selectedIds, total, filterEventId,
                                  events, people, places, onSelectAll, onClear, onApplied }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const n = selectedIds.length;

  async function run(label, fn) {
    if (!n) return;
    setBusy(true); setMsg(null);
    try {
      const r = await fn();
      const c = r.added ?? r.removed ?? r.updated ?? 0;
      setMsg(`✓ ${label} — ${c} photo(s)`);
      await onApplied();
    } catch (e) { setMsg(`⚠ ${e.message}`); }
    finally { setBusy(false); }
  }

  // <select> that fires once then resets to its placeholder
  const Picker = ({ label, options, onPick, danger }) => (
    <select className={`admin-picker ${danger ? "danger" : ""}`} value="" disabled={busy || !n}
            onChange={(e) => { if (e.target.value !== "") onPick(e.target.value); e.target.value = ""; }}>
      <option value="">{label}</option>
      {options.map((o) => <option key={o.value} value={o.value}>{o.text}</option>)}
    </select>
  );

  const evOpts = [...events].sort((a, b) => b.photo_count - a.photo_count)
    .map((e) => ({ value: String(e.id), text: `${e.name} (${e.photo_count})` }));
  const removeOpts = filterEventId
    ? [{ value: String(filterEventId), text: `${(events.find((e) => e.id === filterEventId) || {}).name} (filtered)` },
       ...evOpts.filter((o) => o.value !== String(filterEventId))]
    : evOpts;
  const personOpts = [...people].sort((a, b) => a.name.localeCompare(b.name))
    .map((p) => ({ value: p.id, text: p.name }));
  const placeOpts = [{ value: "__clear__", text: "— clear place —" },
    ...[...places].sort((a, b) => a.name.localeCompare(b.name)).map((p) => ({ value: p.id, text: p.name }))];

  return (
    <div className="admin-bar">
      <div className="admin-bar-left">
        <span className="admin-count">{n} selected</span>
        <button className="link" onClick={onSelectAll} disabled={busy}>Select all {total.toLocaleString()}</button>
        {n > 0 && <button className="link" onClick={onClear} disabled={busy}>Clear</button>}
        <span className="admin-hint">⌘/Ctrl-click toggles · Shift-click selects a range · plain click opens</span>
      </div>

      <div className="admin-bar-right">
        <Picker label="+ Event" options={evOpts}
                onPick={(id) => run(`added ${(events.find((e) => String(e.id) === id) || {}).name}`,
                  () => api.bulkEvent(selectedIds, Number(id), "add"))} />
        <Picker label="− Event" danger options={removeOpts}
                onPick={(id) => run(`removed ${(events.find((e) => String(e.id) === id) || {}).name}`,
                  () => api.bulkEvent(selectedIds, Number(id), "remove"))} />
        <Picker label="Set place" options={placeOpts}
                onPick={(id) => run(id === "__clear__" ? "cleared place" : "set place",
                  () => api.bulkPlace(selectedIds, id === "__clear__" ? null : id))} />
        <Picker label="+ Person" options={personOpts}
                onPick={(id) => run(`added ${(people.find((p) => p.id === id) || {}).name}`,
                  () => api.bulkPerson(selectedIds, id, "add"))} />
        <Picker label="− Person" danger options={personOpts}
                onPick={(id) => run(`removed ${(people.find((p) => p.id === id) || {}).name}`,
                  () => api.bulkPerson(selectedIds, id, "remove"))} />
      </div>

      {msg && <span className="admin-msg">{msg}</span>}
    </div>
  );
}
