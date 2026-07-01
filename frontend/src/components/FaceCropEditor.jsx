import { useRef, useState } from "react";
import { api } from "../api";

// Drag a box over a person's face on the current photo; saves it as their face
// crop + representative (SPEC §4.2). Used for people ★'d on a photo that has no
// Lightroom face region (so the auto-crop fell back to the whole picture).
export default function FaceCropEditor({ person, photo, onClose, onSaved }) {
  const imgRef = useRef(null);
  const start = useRef(null);
  const [box, setBox] = useState(null);   // {x, y, w, h} normalized top-left + size
  const [busy, setBusy] = useState(false);

  const norm = (e) => {
    const r = imgRef.current.getBoundingClientRect();
    return {
      x: Math.min(Math.max((e.clientX - r.left) / r.width, 0), 1),
      y: Math.min(Math.max((e.clientY - r.top) / r.height, 0), 1),
    };
  };
  const down = (e) => { e.preventDefault(); start.current = norm(e); setBox({ ...start.current, w: 0, h: 0 }); };
  const move = (e) => {
    if (!start.current) return;
    const p = norm(e), s = start.current;
    setBox({ x: Math.min(s.x, p.x), y: Math.min(s.y, p.y), w: Math.abs(p.x - s.x), h: Math.abs(p.y - s.y) });
  };
  const up = () => { start.current = null; };

  const valid = box && box.w > 0.02 && box.h > 0.02;
  const save = async () => {
    if (!valid) return;
    setBusy(true);
    try {
      await api.setFaceRegion(person.person_id, {
        photo_id: photo.id, x: box.x + box.w / 2, y: box.y + box.h / 2, w: box.w, h: box.h,
      });
      onSaved();
    } catch (e) { alert(e.message); }
    finally { setBusy(false); }
  };

  return (
    <div className="modal-backdrop pin-backdrop" onClick={onClose}>
      <div className="modal pin-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Face crop — {person.name}</h2>
          <button className="lb-close" onClick={onClose}>✕</button>
        </div>
        <div className="facecrop-wrap">
          <img ref={imgRef} className="facecrop-img" src={photo.display_url} alt="" draggable={false}
               onMouseDown={down} onMouseMove={move} onMouseUp={up} onMouseLeave={up} />
          {box && (
            <div className="facecrop-box"
                 style={{ left: `${box.x * 100}%`, top: `${box.y * 100}%`,
                          width: `${box.w * 100}%`, height: `${box.h * 100}%` }} />
          )}
        </div>
        <div className="pin-msg">Drag a box over <b>{person.name}</b>&rsquo;s face, then Save.</div>
        <div className="pin-foot">
          <span className="pin-coords">{valid ? "Face box drawn" : "Draw a box over the face"}</span>
          <button className="ghost" onClick={onClose}>Cancel</button>
          <button className="ghost primary" disabled={!valid || busy} onClick={save}>Save face</button>
        </div>
      </div>
    </div>
  );
}
