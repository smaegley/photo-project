import { useState } from "react";
import RollDetail from "./RollDetail";

// Browse-by-roll (SPEC §3.7). Grid of magazines; click one to open its detail
// (Dad's index-card scan + the ordered, clickable caption list for that roll).
export default function RollsView({ magazines, onViewInGallery }) {
  const [roll, setRoll] = useState(null);

  if (roll) {
    return <RollDetail roll={roll} onBack={() => setRoll(null)} onViewInGallery={onViewInGallery} />;
  }

  return (
    <div className="gallery-scroll">
      <div className="rolls-grid">
        {magazines.map((m) => (
          <button key={m.id} className="roll-card" onClick={() => setRoll(m)}>
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
