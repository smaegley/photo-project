import { YEAR_MIN, YEAR_MAX } from "../App";

// Dual-handle year range. Two overlaid range inputs (SPEC §4.1).
export default function DateSlider({ years, onChange }) {
  const [lo, hi] = years;
  const span = YEAR_MAX - YEAR_MIN;
  const pct = (y) => ((y - YEAR_MIN) / span) * 100;

  const setLo = (v) => onChange([Math.min(+v, hi), hi]);
  const setHi = (v) => onChange([lo, Math.max(+v, lo)]);

  return (
    <div className="dateband">
      <span className="date-label">{lo}</span>
      <div className="slider">
        <div className="track" />
        <div className="track-fill" style={{ left: `${pct(lo)}%`, right: `${100 - pct(hi)}%` }} />
        <input type="range" min={YEAR_MIN} max={YEAR_MAX} value={lo}
               onChange={(e) => setLo(e.target.value)} aria-label="Start year" />
        <input type="range" min={YEAR_MIN} max={YEAR_MAX} value={hi}
               onChange={(e) => setHi(e.target.value)} aria-label="End year" />
      </div>
      <span className="date-label">{hi}</span>
      {(lo !== YEAR_MIN || hi !== YEAR_MAX) && (
        <button className="link" onClick={() => onChange([YEAR_MIN, YEAR_MAX])}>All dates</button>
      )}
    </div>
  );
}
