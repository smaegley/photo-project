import { useState } from "react";

const REL_ORDER = ["Self", "Parent", "Sibling", "Child", "Grandparent",
  "Aunt / Uncle", "Cousin", "Spouse", "Extended family"];

function Section({ title, count, children, defaultOpen = true }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="rail-section">
      <button className="rail-head" onClick={() => setOpen((o) => !o)}>
        <span className={`chev ${open ? "open" : ""}`}>▸</span>
        {title}
        {count != null && <span className="rail-head-count">{count}</span>}
      </button>
      {open && <div className="rail-body">{children}</div>}
    </section>
  );
}

function Row({ label, count, active, disabled, onClick, swatch }) {
  return (
    <button
      className={`facet-row ${active ? "active" : ""} ${disabled ? "disabled" : ""}`}
      onClick={onClick}
      disabled={disabled && !active}
    >
      {swatch}
      <span className="facet-label">{label}</span>
      <span className="facet-count">{count}</span>
    </button>
  );
}

export default function FilterRail({ people, events, places, peopleCounts, eventCounts, sel, onToggle, onReset, hasFilters }) {
  const pCount = Object.fromEntries(peopleCounts.map((c) => [c.key, c.count]));
  const eCount = Object.fromEntries(eventCounts.map((c) => [c.key, c.count]));

  const grouped = {};
  for (const p of people) (grouped[p.relationship] ||= []).push(p);

  return (
    <aside className="rail">
      {hasFilters && (
        <button className="rail-reset" onClick={onReset}>✕ Clear all filters</button>
      )}

      <Section title="People">
        {REL_ORDER.filter((r) => grouped[r]).map((rel) => (
          <div key={rel} className="rel-group">
            <div className="rel-label">{rel}</div>
            {grouped[rel]
              .sort((a, b) => (pCount[b.id] ?? b.photo_count) - (pCount[a.id] ?? a.photo_count))
              .map((p) => {
                const live = pCount[p.id] ?? 0;
                return (
                  <Row key={p.id} label={p.name} count={live}
                    active={sel.people.includes(p.id)} disabled={live === 0}
                    onClick={() => onToggle("people", p.id)} />
                );
              })}
          </div>
        ))}
      </Section>

      <Section title="Events">
        {events.map((e) => {
          const live = eCount[String(e.id)] ?? 0;
          return (
            <Row key={e.id} label={e.name} count={live}
              active={sel.events.includes(e.id)} disabled={live === 0}
              onClick={() => onToggle("events", e.id)} />
          );
        })}
      </Section>

      <Section title="Places" defaultOpen={false}>
        <div className="places-scroll">
          {places.map((pl) => (
            <Row key={pl.id} label={pl.name} count={pl.photo_count}
              active={sel.places.includes(pl.id)}
              onClick={() => onToggle("places", pl.id)} />
          ))}
        </div>
      </Section>
    </aside>
  );
}
