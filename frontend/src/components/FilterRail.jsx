import { useState } from "react";

const REL_ORDER = ["Self", "Parent", "Sibling", "Child", "Grandparent",
  "Aunt / Uncle", "Cousin", "Spouse", "Extended family"];

function Section({ title, count, action, children, defaultOpen = true }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="rail-section">
      <button className="rail-head" onClick={() => setOpen((o) => !o)}>
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

// A face cell for the grid view: cropped face thumbnail (or an initial), name, count.
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

export default function FilterRail({ people, events, places, peopleCounts, eventCounts, sel, onToggle, onReset, hasFilters }) {
  const [grid, setGrid] = useState(() => localStorage.getItem("peopleView") !== "list");
  const setView = (g) => { setGrid(g); localStorage.setItem("peopleView", g ? "grid" : "list"); };

  const pCount = Object.fromEntries(peopleCounts.map((c) => [c.key, c.count]));
  const eCount = Object.fromEntries(eventCounts.map((c) => [c.key, c.count]));

  const grouped = {};
  for (const p of people) (grouped[p.relationship] ||= []).push(p);
  const groups = REL_ORDER.filter((r) => grouped[r]);
  const byCount = (a, b) => (pCount[b.id] ?? b.photo_count) - (pCount[a.id] ?? a.photo_count);

  return (
    <aside className="rail">
      {hasFilters && (
        <button className="rail-reset" onClick={onReset}>✕ Clear all filters</button>
      )}

      <Section title="People" action={
        <span className="rail-viewtoggle">
          <button className={grid ? "on" : ""} title="Face grid" onClick={() => setView(true)}>▦</button>
          <button className={!grid ? "on" : ""} title="List" onClick={() => setView(false)}>☰</button>
        </span>
      }>
        {groups.map((rel) => (
          <div key={rel} className="rel-group">
            <div className="rel-label">{rel}</div>
            {grid ? (
              <div className="people-grid">
                {grouped[rel].sort(byCount).map((p) => {
                  const live = pCount[p.id] ?? 0;
                  return <PersonCell key={p.id} p={p} count={live}
                    active={sel.people.includes(p.id)} disabled={live === 0}
                    onClick={() => onToggle("people", p.id)} />;
                })}
              </div>
            ) : (
              grouped[rel].sort(byCount).map((p) => {
                const live = pCount[p.id] ?? 0;
                return <Row key={p.id} label={p.name} count={live}
                  active={sel.people.includes(p.id)} disabled={live === 0}
                  onClick={() => onToggle("people", p.id)} />;
              })
            )}
          </div>
        ))}
      </Section>

      <Section title="Events">
        {events.map((e) => {
          const live = eCount[String(e.id)] ?? 0;
          return <Row key={e.id} label={e.name} count={live}
            active={sel.events.includes(e.id)} disabled={live === 0}
            onClick={() => onToggle("events", e.id)} />;
        })}
      </Section>

      <Section title="Places" defaultOpen={false}>
        <div className="places-scroll">
          {places.map((pl) => (
            <Row key={pl.id} label={pl.name} count={pl.photo_count}
              active={sel.places.includes(pl.id)} disabled={pl.photo_count === 0}
              onClick={() => onToggle("places", pl.id)} />
          ))}
        </div>
      </Section>
    </aside>
  );
}
