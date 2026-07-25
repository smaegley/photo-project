import RollDetail from "./RollDetail";

// Browse-by-roll (SPEC §3.7). Grid of magazines; click one to open its detail
// (Dad's index-card scan + the ordered, clickable caption list for that roll).
// The selected roll is lifted to App (`roll`/`onSelectRoll`) so the header count
// can reflect it — this view holds no state of its own.
export default function RollsView({ magazines, roll, onSelectRoll, onViewInGallery,
                                    admin, isAdmin, events, people, places, onChanged }) {
  if (roll) {
    return <RollDetail roll={roll} onBack={() => onSelectRoll(null)} onViewInGallery={onViewInGallery}
                       admin={admin} isAdmin={isAdmin} events={events} people={people}
                       places={places} onChanged={onChanged} />;
  }

  return (
    <div className="gallery-scroll">
      <div className="rolls-grid">
        {magazines.map((m) => (
          <button key={m.id} className="roll-card" onClick={() => onSelectRoll(m)}>
            <div className="roll-card-img">
              {m.card_image_paths[0] ? (
                <img src={`/api/card-thumbs/${m.card_image_paths[0]}`} alt={`Roll ${m.id} index card`} loading="lazy" />
              ) : (
                <div className="roll-card-blank">no card</div>
              )}
            </div>
            <div className="roll-card-meta">
              <div className="roll-num">Roll {m.id}</div>
              <div className="roll-title">{m.title || "—"}</div>
              <div className="roll-sub">{m.span_label} · {m.slide_count} slides</div>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
