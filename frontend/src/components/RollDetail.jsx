import { useEffect, useState } from "react";
import { api } from "../api";
import Lightbox from "./Lightbox";

// One roll: Dad's index-card scan(s) beside the ordered, clickable caption list,
// plus a thumbnail strip. Clicking a caption or thumbnail opens that slide (SPEC §3.7).
export default function RollDetail({ roll, onBack, onViewInGallery }) {
  const [photos, setPhotos] = useState([]);
  const [lightboxIdx, setLightboxIdx] = useState(null);

  useEffect(() => {
    api.photos({ people: [], events: [], places: [], magazineId: roll.id }, 1, 300)
      .then((r) => {
        const sorted = [...r.photos].sort((a, b) => (a.slide_in_mag ?? 0) - (b.slide_in_mag ?? 0));
        setPhotos(sorted);
      });
  }, [roll.id]);

  return (
    <div className="gallery-scroll roll-detail">
      <div className="roll-detail-head">
        <button className="link" onClick={onBack}>‹ All rolls</button>
        <div>
          <div className="roll-num">Roll {roll.id} · {roll.span_label}</div>
          <h2>{roll.title}</h2>
        </div>
        <button className="ghost" onClick={() => onViewInGallery(roll.id)}>View {roll.slide_count} slides in gallery →</button>
      </div>

      <div className="roll-detail-body">
        <div className="roll-cards">
          <div className="roll-cards-label">Dad's index card{roll.card_image_paths.length > 1 ? "s" : ""}</div>
          {roll.card_image_paths.map((c) => (
            <img key={c} className="roll-card-full" src={`/api/cards/${c}`} alt="index card" />
          ))}
        </div>

        <div className="roll-captions">
          <div className="roll-cards-label">Slides in this roll ({photos.length})</div>
          <div className="grid">
            {photos.map((p, i) => (
              <button key={p.id} className="tile" onClick={() => setLightboxIdx(i)} title={p.caption || ""}>
                <img src={p.thumb_url} alt={p.caption || p.source_file} loading="lazy" />
                {p.slide_in_mag != null && <span className="tile-num">{p.slide_in_mag}</span>}
              </button>
            ))}
          </div>
        </div>
      </div>

      {lightboxIdx !== null && (
        <Lightbox
          photos={photos}
          index={lightboxIdx}
          onClose={() => setLightboxIdx(null)}
          onNav={setLightboxIdx}
          onLoadMore={() => {}}
        />
      )}
    </div>
  );
}
