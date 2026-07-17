import { YEAR_MIN, YEAR_MAX } from "../App";

// Dual-handle year range. Two overlaid range inputs (SPEC §4.1). Bounds are
// dynamic — they grow as scans/digital extend the library (SPEC §12.8) — with the
// frozen slide range as the fallback until /api/photos/meta resolves.
export default function DateSlider({ years, onChange, min = YEAR_MIN, max = YEAR_MAX }) {
  const [lo, hi] = years;
  const span = Math.max(max - min, 1);
  const pct = (y) => ((y - min) / span) * 100;

  const setLo = (v) => onChange([Math.min(+v, hi), hi]);
  const setHi = (v) => onChange([lo, Math.max(+v, lo)]);

  return (
    <div className="dateband">
      <span className="date-label">{lo}</span>
      <div className="slider">
        <div className="track" />
        <div className="track-fill" style={{ left: `${pct(lo)}%`, right: `${100 - pct(hi)}%` }} />
        <input type="range" min={min} max={max} value={lo}
               onChange={(e) => setLo(e.target.value)} aria-label="Start year" />
        <input type="range" min={min} max={max} value={hi}
               onChange={(e) => setHi(e.target.value)} aria-label="End year" />
      </div>
      <span className="date-label">{hi}</span>
      {(lo !== min || hi !== max) && (
        <button className="link" onClick={() => onChange([min, max])}>All dates</button>
      )}
    </div>
  );
}
