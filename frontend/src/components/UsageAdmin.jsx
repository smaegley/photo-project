import { useEffect, useState } from "react";
import { api } from "../api";

// Admin usage panel (SPEC hybrid tracking): in-app view/download activity.
// Login + traffic analytics live in Cloudflare, so this focuses on what happens
// inside the app — what's viewed, who's active.
export default function UsageAdmin({ onClose }) {
  const [stats, setStats] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => { api.usageStats().then(setStats).catch((e) => setErr(e.message)); }, []);

  const Rows = ({ items, empty }) =>
    items.length === 0
      ? <div className="usage-recent">{empty}</div>
      : items.map((it) => (
          <div key={it.key} className="usage-row">
            <span className="facet-label">{it.label}</span>
            <span className="u-count">{it.count.toLocaleString()}</span>
          </div>
        ));

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Usage</h2>
          <button className="lb-close" onClick={onClose}>✕</button>
        </div>
        <div className="usage-body">
          {err && <div className="modal-msg">{err}</div>}
          {!stats && !err && <div className="modal-msg">Loading…</div>}
          {stats && (
            <>
              <div className="usage-totals">
                <div className="usage-total"><b>{stats.total_views.toLocaleString()}</b><span>photo views</span></div>
                <div className="usage-total"><b>{stats.total_downloads.toLocaleString()}</b><span>downloads</span></div>
              </div>
              <div className="usage-sec">
                <h3>Active users</h3>
                <Rows items={stats.active_users} empty="No activity yet." />
              </div>
              <div className="usage-sec">
                <h3>Most-viewed photos</h3>
                <Rows items={stats.top_photos} empty="No views yet." />
              </div>
              <div className="usage-sec">
                <h3>Recent activity</h3>
                <div className="usage-recent">
                  {stats.recent.length === 0 ? "—" : stats.recent.map((r, i) => <div key={i}>{r}</div>)}
                </div>
              </div>
              <div className="modal-msg">
                Logins &amp; traffic analytics live in Cloudflare (Zero&nbsp;Trust → Access logs, plus Web Analytics).
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
