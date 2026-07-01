import { useEffect, useState } from "react";
import { api } from "../api";

// Admin usage panel (SPEC hybrid tracking): in-app view/download activity.
// Login + traffic analytics live in Cloudflare, so this focuses on what happens
// inside the app — what's viewed, who's active.
export default function UsageAdmin({ onClose }) {
  const [stats, setStats] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => { api.usageStats().then(setStats).catch((e) => setErr(e.message)); }, []);

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
                <h3>By user</h3>
                {stats.per_user.length === 0
                  ? <div className="usage-recent">No activity yet.</div>
                  : (
                    <div className="usage-usertable">
                      <div className="usage-urow usage-uhead"><span>User</span><span>Views</span><span>Downloads</span></div>
                      {stats.per_user.map((u) => (
                        <div key={u.email} className="usage-urow">
                          <span className="facet-label">{u.email}</span>
                          <span>{u.views.toLocaleString()}</span>
                          <span>{u.downloads.toLocaleString()}</span>
                        </div>
                      ))}
                    </div>
                  )}
              </div>
              <div className="usage-sec">
                <h3>Most-viewed photos</h3>
                {stats.top_photos.length === 0
                  ? <div className="usage-recent">No views yet.</div>
                  : stats.top_photos.map((it) => (
                      <div key={it.key} className="usage-row">
                        <span className="usage-thumb"><img src={`/api/thumbnails/${it.key}`} alt="" loading="lazy" /></span>
                        <span className="facet-label">{it.label}</span>
                        <span className="u-count">{it.count.toLocaleString()}</span>
                      </div>
                    ))}
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
