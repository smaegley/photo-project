import { useState } from "react";

// Must match backend routers/facets.py REL_ORDER — a group missing here is silently
// dropped from the People rail for linked viewers (§12.7 "Friends & others" bug).
const REL_ORDER = ["Self", "Spouse", "Parent", "Sibling", "Child", "Grandparent",
  "Aunt / Uncle", "Cousin", "Extended family", "Friends & others"];

function Section({ title, count, action, children, storageKey, defaultOpen = true }) {
  const [open, setOpen] = useState(() => {
    if (!storageKey) return defaultOpen;
    const saved = localStorage.getItem(`rail-open-${storageKey}`);
    return saved === null ? defaultOpen : saved === "1";
  });
  const toggle = () => setOpen((o) => {
    const next = !o;
    if (storageKey) localStorage.setItem(`rail-open-${storageKey}`, next ? "1" : "0");
    return next;
  });
  return (
    <section className="rail-section">
      <button className="rail-head" onClick={toggle}>
        <span className={`chev ${open ? "open" : ""}`}>▸</span>
        {title}
        {count != null && <span className="rail-head-count">{count}</span>}
        {action && <span className="rail-head-action" onClick={(e) => e.stopPropagation()}>{action}</span>}
      </button>
      {open && <div className="rail-body">{children}</div>}
    </section>
  );
}

function Row({ label, count, active, disabled, onClick }) {
  return (
    <button className={`facet-row ${active ? "active" : ""} ${disabled ? "disabled" : ""}`}
            onClick={onClick} disabled={disabled && !active}>
      <span className="facet-label">{label}</span>
      <span className="facet-count">{count}</span>
    </button>
  );
}

function PersonCell({ p, count, active, disabled, onClick }) {
  return (
    <button className={`person-cell ${active ? "active" : ""} ${disabled ? "disabled" : ""}`}
            onClick={onClick} disabled={disabled && !active} title={`${p.name} · ${count}`}>
      <span className="person-face">
        {p.face_url ? <img src={p.face_url} alt={p.name} loading="lazy" />
                    : <span className="initial">{p.name.slice(0, 1)}</span>}
      </span>
      <span className="person-name">{p.name}</span>
      <span className="person-count">{count}</span>
    </button>
  );
}

export default function FilterRail({ people, events, places, peopleCounts, eventCounts, placeCounts = [], sel, onToggle, onReset, hasFilters }) {
  const [grid, setGrid] = useState(() => localStorage.getItem("peopleView") !== "list");
  const setView = (g) => { setGrid(g); localStorage.setItem("peopleView", g ? "grid" : "list"); };

  const pCount = Object.fromEntries(peopleCounts.map((c) => [c.key, c.count]));
  const eCount = Object.fromEntries(eventCounts.map((c) => [c.key, c.count]));
  const lCount = Object.fromEntries(placeCounts.map((c) => [c.key, c.count]));

  const grouped = {};
  for (const p of people) (grouped[p.relationship] ||= []).push(p);
  const groups = REL_ORDER.filter((r) => grouped[r]);
  const ungrouped = grouped[null] ?? [];
  const byCount = (a, b) => (pCount[b.id] ?? b.photo_count) - (pCount[a.id] ?? a.photo_count);

  // Live counts for section headers (all scoped to the current result)
  const activePeopleCount = peopleCounts.filter((c) => c.count > 0).length;
  const activeEventCount  = eventCounts.filter((c) => c.count > 0).length;
  const activePlaceCount  = placeCounts.filter((c) => c.count > 0).length;

  function renderPeople(list) {
    return grid ? (
      <div className="people-grid">
        {list.sort(byCount).map((p) => {
          const live = pCount[p.id] ?? 0;
          return <PersonCell key={p.id} p={p} count={live}
            active={sel.people.includes(p.id)} disabled={live === 0}
            onClick={() => onToggle("people", p.id)} />;
        })}
      </div>
    ) : (
      list.sort(byCount).map((p) => {
        const live = pCount[p.id] ?? 0;
        return <Row key={p.id} label={p.name} count={live}
          active={sel.people.includes(p.id)} disabled={live === 0}
          onClick={() => onToggle("people", p.id)} />;
      })
    );
  }

  return (
    <aside className="rail">
      {hasFilters && (
        <button className="rail-reset" onClick={onReset}>✕ Clear all filters</button>
      )}

      <Section title="Places" storageKey="places" defaultOpen={false} count={activePlaceCount}>
        <div className="places-scroll">
          {places.map((pl) => {
            const live = lCount[pl.id] ?? 0;
            return <Row key={pl.id} label={pl.name} count={live}
              active={sel.places.includes(pl.id)} disabled={live === 0}
              onClick={() => onToggle("places", pl.id)} />;
          })}
        </div>
      </Section>

      <Section title="Events" storageKey="events" defaultOpen={false} count={activeEventCount}>
        {events.map((e) => {
          const live = eCount[String(e.id)] ?? 0;
          return <Row key={e.id} label={e.name} count={live}
            active={sel.events.includes(e.id)} disabled={live === 0}
            onClick={() => onToggle("events", e.id)} />;
        })}
      </Section>

      <Section title="People" storageKey="people" count={activePeopleCount} action={
        <span className="rail-viewtoggle">
          <button className={grid ? "on" : ""} title="Face grid" onClick={() => setView(true)}>▦</button>
          <button className={!grid ? "on" : ""} title="List" onClick={() => setView(false)}>☰</button>
        </span>
      }>
        {groups.map((rel) => (
          <div key={rel} className="rel-group">
            <div className="rel-label">{rel}</div>
            {renderPeople(grouped[rel])}
          </div>
        ))}
        {ungrouped.length > 0 && renderPeople(ungrouped)}
      </Section>
    </aside>
  );
}
