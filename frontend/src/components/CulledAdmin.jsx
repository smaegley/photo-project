import { useEffect, useState } from "react";
import { api } from "../api";

// Culled photos (SPEC §15.9): everything hidden with Cull, newest first. Culling never
// deletes — the row, tags, faces and images all stay — so Restore puts a photo back
// exactly as it was. Click tiles to mark them, then Restore.
export default function CulledAdmin({ onClose, onChanged }) {
  const [data, setData] = useState({ total: 0, photos: [] });
  const [marked, setMarked] = useState(() => new Set());
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);

  const load = () => api.culled().then((d) => { setData(d); setMarked(new Set()); });
  useEffect(() => { load().catch((e) => setMsg(`⚠ ${e.message}`)); }, []);

  const toggle = (id) => setMarked((s) => {
    const n = new Set(s);
    if (n.has(id)) n.delete(id); else n.add(id);
    return n;
  });

  const restore = async () => {
    if (!marked.size) return;
    setBusy(true); setMsg(null);
    try {
      const r = await api.restorePhotos([...marked]);
      setMsg(`✓ restored ${r.changed} photo${r.changed !== 1 ? "s" : ""}`);
      await load();
      await onChanged?.();
    } catch (e) { setMsg(`⚠ ${e.message}`); }
    finally { setBusy(false); }
  };

  const when = (p) => (p.culled_at ? p.culled_at.slice(0, 10) : "");

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal modal-wide rotation-sweep" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Culled photos <span className="modal-sub">{data.total} hidden · {marked.size} marked</span></h2>
          <button className="lb-close" onClick={onClose}>✕</button>
        </div>

        <div className="rotation-toolbar">
          <span className="modal-sub">
            Hidden from everyone, nothing deleted. Click photos to mark them, then Restore.
          </span>
          {data.photos.length > 0 && (
            <button className="ghost" disabled={busy}
                    onClick={() => setMarked(marked.size === data.photos.length
                      ? new Set() : new Set(data.photos.map((p) => p.id)))}>
              {marked.size === data.photos.length ? "Clear" : "Mark all"}
            </button>
          )}
          <button className="ghost rotation-apply" onClick={restore} disabled={busy || !marked.size}>
            {busy ? "Restoring…" : `Restore ${marked.size || ""}`}
          </button>
        </div>

        <div className="rotation-grid">
          {data.photos.map((p) => (
            <button key={p.id} className={`rotation-cell ${marked.has(p.id) ? "marked" : ""}`}
                    onClick={() => toggle(p.id)} disabled={busy}
                    title={`${p.source_file}${p.caption ? " · " + p.caption : ""} · culled ${when(p)}${p.culled_by ? " by " + p.culled_by : ""}`}>
              <span className="rotation-frame">
                <img src={p.thumb_url} alt={p.caption || p.source_file} loading="lazy" />
              </span>
              <span className="rotation-badge">{marked.has(p.id) ? "✓" : ""}</span>
            </button>
          ))}
          {data.photos.length === 0 && <div className="modal-msg">Nothing is culled.</div>}
        </div>
        {data.total > data.photos.length && (
          <div className="modal-msg">Showing the {data.photos.length} most recently culled of {data.total}.</div>
        )}

        {msg && <div className="modal-msg">{msg}</div>}
      </div>
    </div>
  );
}
