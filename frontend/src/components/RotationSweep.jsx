import { useEffect, useMemo, useState } from "react";
import { api } from "../api";

// Rotation sweep (admin, dev-side workflow): eyeball every slide + scan, click a tile
// to cycle its pending rotation 0→90→180→270 (CSS preview only), then Apply runs the
// real rotations — pixels, thumbnails and face boxes — through the existing endpoint.
// Detector proposals (app.detect_rotation) arrive pre-marked and sorted first; photos
// the detector probed but found nothing at any angle are next (landscapes only human
// eyes can judge), then everything else in mag/slide order.
export default function RotationSweep({ onClose, onChanged }) {
  const [items, setItems] = useState([]);
  const [generatedAt, setGeneratedAt] = useState(null);
  const [pending, setPending] = useState({});   // photo_id -> 90|180|270
  const [tab, setTab] = useState("flagged");    // flagged | local | digital
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(null);
  const [msg, setMsg] = useState(null);

  async function load() {
    const q = await api.rotationQueue();
    setItems(q.items);
    setGeneratedAt(q.generated_at);
    // Detector proposals arrive pre-marked — EXCEPT on pet-only photos, where the
    // face-angle signal is known-unreliable (dogs detect at random angles); those
    // show the suggestion as a badge hint and wait for a human click.
    const pre = {};
    for (const it of q.items) if (it.proposal && !it.pet_only) pre[it.id] = it.proposal;
    setPending(pre);
  }
  useEffect(() => { load(); }, []);

  const isFlagged = (i) => i.proposal || (i.probed && i.faces === 0);
  const flaggedCount = useMemo(() => items.filter(isFlagged).length, [items]);
  const localCount = useMemo(() => items.filter((i) => i.origin !== "digital").length, [items]);
  const digitalCount = useMemo(() => items.filter((i) => i.origin === "digital").length, [items]);
  const shown = useMemo(() => {
    if (tab === "flagged") return items.filter(isFlagged);
    if (tab === "local") return items.filter((i) => i.origin !== "digital");
    return items.filter((i) => i.origin === "digital");
  }, [items, tab]);
  const markedCount = Object.keys(pending).length;

  const cycle = (id) => {
    if (busy) return;
    setPending((p) => {
      const next = (((p[id] ?? 0) + 90) % 360);
      const copy = { ...p };
      if (next === 0) delete copy[id]; else copy[id] = next;
      return copy;
    });
  };

  const apply = async () => {
    const work = Object.entries(pending);
    if (!work.length) return;
    if (!window.confirm(`Rotate ${work.length} photo${work.length !== 1 ? "s" : ""}? ` +
                        "Pixels, thumbnails and face boxes all rotate together. Each is individually undoable.")) return;
    setBusy(true); setMsg(null);
    const failures = [];
    for (let i = 0; i < work.length; i++) {
      const [id, deg] = work[i];
      setProgress(`${i + 1}/${work.length}`);
      try {
        await api.rotatePhoto(id, deg);
      } catch (e) {
        failures.push(`#${id}: ${e.message}`);
      }
    }
    setProgress(null);
    setBusy(false);
    setPending({});
    await load();
    await onChanged();
    setMsg(failures.length
      ? `⚠ ${work.length - failures.length} rotated, ${failures.length} failed — ${failures.slice(0, 3).join("; ")}`
      : `✓ rotated ${work.length} photo${work.length !== 1 ? "s" : ""}`);
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal modal-wide rotation-sweep" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Rotation sweep <span className="modal-sub">{shown.length} shown · {markedCount} marked</span></h2>
          <button className="lb-close" onClick={onClose}>✕</button>
        </div>

        <div className="rotation-toolbar">
          <button className={`ghost ${tab === "flagged" ? "active" : ""}`} onClick={() => setTab("flagged")} disabled={busy}>
            Flagged ({flaggedCount})
          </button>
          <button className={`ghost ${tab === "local" ? "active" : ""}`} onClick={() => setTab("local")} disabled={busy}>
            Slides &amp; scans ({localCount})
          </button>
          <button className={`ghost ${tab === "digital" ? "active" : ""}`} onClick={() => setTab("digital")} disabled={busy}>
            Digital ({digitalCount})
          </button>
          <span className="modal-sub">
            Click a photo to turn it; Apply makes it real.
            {generatedAt ? ` Detector pass: ${generatedAt.slice(0, 10)}.` : " No detector pass found — run app.detect_rotation."}
          </span>
          <button className="ghost rotation-apply" onClick={apply} disabled={busy || !markedCount}>
            {busy ? `Rotating ${progress}…` : `Apply ${markedCount || ""}`}
          </button>
        </div>

        <div className="rotation-grid">
          {shown.map((it) => {
            const deg = pending[it.id] ?? 0;
            return (
              <button key={it.id} className={`rotation-cell ${deg ? "marked" : ""} ${it.proposal ? "proposed" : ""}`}
                      onClick={() => cycle(it.id)} disabled={busy}
                      title={`${it.source_file}${it.caption ? " · " + it.caption : ""} · ${it.faces} face${it.faces !== 1 ? "s" : ""} detected`}>
                <span className="rotation-frame">
                  <img src={it.thumb} alt={it.caption || it.source_file} loading="lazy"
                       style={deg ? { transform: `rotate(${deg}deg)` } : undefined} />
                </span>
                <span className="rotation-badge">
                  {deg ? `${deg}°`
                       : it.proposal && it.pet_only ? `🐾${it.proposal}°?`
                       : (it.probed && it.faces === 0 && !it.proposal ? "?" : "")}
                </span>
              </button>
            );
          })}
          {shown.length === 0 && <div className="modal-msg">Nothing flagged — switch to "All" for the full sweep.</div>}
        </div>

        {msg && <div className="modal-msg">{msg}</div>}
      </div>
    </div>
  );
}
