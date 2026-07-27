import { useEffect, useMemo, useState } from "react";
import { api } from "../api";

// Face-matching review (SPEC §14.7). Two jobs, deliberately separated because they ask
// different questions of the reviewer:
//
//   Suggestions  — "is this Kate?"  One person at a time, crops sorted by confidence,
//                  so the judgement is near-binary and a screenful clears in seconds.
//   Unknown      — "who is this, if anyone?"  Faces matching nobody, grouped so a
//                  recurring stranger is one decision instead of forty.
//
// Nothing here auto-applies: P-F3 measured ~4.6% of complete strangers clearing the 0.45
// threshold, so the human click is the defence, not the score. Every action is one
// undoable contribution covering the whole batch.

function Crop({ faceId, score, badge, selected, onClick }) {
  return (
    <button className={`fq-crop ${selected ? "on" : ""}`} onClick={onClick} type="button">
      <img src={`/api/face-crop/${faceId}`} alt="" loading="lazy" />
      {score != null && <span className="fq-score">{score.toFixed(2)}</span>}
      {badge && <span className="fq-badge">{badge}</span>}
    </button>
  );
}

export default function FaceQueueAdmin({ people, onClose, onChanged }) {
  const [tab, setTab] = useState("suggestions");
  const [queue, setQueue] = useState(null);
  const [person, setPerson] = useState(null);
  const [items, setItems] = useState([]);
  const [sel, setSel] = useState(() => new Set());
  const [clusters, setClusters] = useState(null);
  const [summary, setSummary] = useState(null);
  const [naming, setNaming] = useState({});     // cluster id -> person_id being assigned
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const loadQueue = () => api.faceQueue().then(setQueue).catch((e) => setErr(String(e)));
  const loadClusters = () => Promise.all([api.faceClusters(), api.faceClusterSummary()])
    .then(([c, s]) => { setClusters(c); setSummary(s); })
    .catch((e) => setErr(String(e)));

  useEffect(() => { loadQueue(); loadClusters(); }, []);

  useEffect(() => {
    if (!person) { setItems([]); return; }
    setSel(new Set());
    api.faceQueuePerson(person).then(setItems).catch((e) => setErr(String(e)));
  }, [person]);

  const toggle = (id) => setSel((s) => {
    const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n;
  });

  async function decide(action, ids) {
    if (!ids.length) return;
    setBusy(true); setErr(null);
    try {
      await api.decideFaces(ids, action);
      const rest = items.filter((i) => !ids.includes(i.suggestion_id));
      setItems(rest);
      setSel(new Set());
      await loadQueue();
      if (!rest.length) setPerson(null);
      onChanged?.();
    } catch (e) { setErr(String(e?.message || e)); } finally { setBusy(false); }
  }

  async function decideCluster(body) {
    setBusy(true); setErr(null);
    try {
      await api.decideClusters(body);
      await loadClusters();
      onChanged?.();
    } catch (e) { setErr(String(e?.message || e)); } finally { setBusy(false); }
  }

  const totalPending = useMemo(
    () => (queue || []).reduce((n, r) => n + r.pending, 0), [queue]);
  const highConf = items.filter((i) => i.score >= 0.6).map((i) => i.suggestion_id);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal wide face-queue" onClick={(e) => e.stopPropagation()}>
        <header>
          <h2>Face review</h2>
          <button className="icon" onClick={onClose} aria-label="Close">✕</button>
        </header>

        <div className="seg fq-tabs">
          <button className={tab === "suggestions" ? "on" : ""} onClick={() => setTab("suggestions")}>
            Suggestions {totalPending ? `(${totalPending.toLocaleString()})` : ""}
          </button>
          <button className={tab === "unknown" ? "on" : ""} onClick={() => setTab("unknown")}>
            Unknown faces {clusters ? `(${clusters.length})` : ""}
          </button>
        </div>

        {err && <p className="error">{err}</p>}

        {tab === "suggestions" && (
          <div className="fq-body">
            <ol className="fq-people">
              {!queue && <li className="muted">Loading…</li>}
              {queue?.length === 0 && <li className="muted">Nothing pending. 🎉</li>}
              {queue?.map((r) => (
                <li key={r.person_id}>
                  <button className={person === r.person_id ? "on" : ""}
                          onClick={() => setPerson(r.person_id)}>
                    <span className="fq-name">{r.name}</span>
                    <span className="fq-count">{r.pending}</span>
                    <span className="muted fq-sub">
                      {r.new} new{r.backfill ? ` · ${r.backfill} box-only` : ""} · avg {r.avg_score}
                    </span>
                  </button>
                </li>
              ))}
            </ol>

            <div className="fq-grid-wrap">
              {!person && <p className="hint">Pick a person to review their suggested faces.</p>}
              {person && (
                <>
                  <div className="fq-actions">
                    <b>{queue?.find((q) => q.person_id === person)?.name}</b>
                    <span className="muted">{items.length} pending</span>
                    <button onClick={() => setSel(new Set(items.map((i) => i.suggestion_id)))}
                            disabled={busy}>Select all</button>
                    <button onClick={() => setSel(new Set())} disabled={busy || !sel.size}>Clear</button>
                    <span className="spacer" />
                    <button disabled={busy || !highConf.length}
                            title="Accept every suggestion scoring 0.60 or better"
                            onClick={() => decide("accept", highConf)}>
                      Accept ≥0.60 ({highConf.length})
                    </button>
                    <button className="primary" disabled={busy || !sel.size}
                            onClick={() => decide("accept", [...sel])}>
                      ✓ Accept {sel.size || ""}
                    </button>
                    <button disabled={busy || !sel.size}
                            onClick={() => decide("reject", [...sel])}>
                      ✕ Reject {sel.size || ""}
                    </button>
                  </div>
                  <p className="hint">
                    Click a face to select it. Crops are sorted most-confident first, so the
                    reliable ones cluster at the top — scan down until they stop looking right.
                    <b> box-only</b> means this person is already tagged on that photo and
                    accepting just adds the face outline.
                  </p>
                  <div className="fq-grid">
                    {items.map((i) => (
                      <Crop key={i.suggestion_id} faceId={i.face_id} score={i.score}
                            badge={i.backfill ? "box-only" : (i.year || null)}
                            selected={sel.has(i.suggestion_id)}
                            onClick={() => toggle(i.suggestion_id)} />
                    ))}
                  </div>
                </>
              )}
            </div>
          </div>
        )}

        {tab === "unknown" && (
          <div className="fq-unknown">
            <p className="hint">
              Faces matching nobody enrolled, grouped by who they look like and ordered by
              prominence — the biggest, sharpest faces first, since those are the ones worth
              naming. Name a group to tag every face in it, or ignore it and it stays quiet
              on future runs.
            </p>
            {summary && (
              <div className="fq-actions">
                <span className="muted">
                  {summary.by_status?.find((s) => s.status === "pending")?.clusters ?? 0} groups pending
                </span>
                <span className="spacer" />
                <button disabled={busy || !summary.pending_singletons}
                        title="One-off faces that appear in a single photo — almost always background people"
                        onClick={() => decideCluster({ action: "ignore", singletons: true })}>
                  Ignore all {summary.pending_singletons.toLocaleString()} one-off faces
                </button>
              </div>
            )}
            <div className="fq-clusters">
              {!clusters && <p className="muted">Loading…</p>}
              {clusters?.length === 0 && <p className="muted">No groups left to review.</p>}
              {clusters?.map((c) => (
                <div className="fq-cluster" key={c.id}>
                  <div className="fq-cluster-head">
                    <b>{c.n_faces} faces</b>
                    <span className="spacer" />
                    <select value={naming[c.id] || ""} disabled={busy}
                            onChange={(e) => setNaming((n) => ({ ...n, [c.id]: e.target.value }))}>
                      <option value="">Name as…</option>
                      {people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                    </select>
                    <button className="primary" disabled={busy || !naming[c.id]}
                            onClick={() => decideCluster({ action: "name", cluster_ids: [c.id],
                                                           person_id: naming[c.id] })}>
                      Tag all
                    </button>
                    <button disabled={busy}
                            onClick={() => decideCluster({ action: "ignore", cluster_ids: [c.id] })}>
                      Ignore
                    </button>
                  </div>
                  <div className="fq-grid small">
                    {c.sample_face_ids.map((fid) => <Crop key={fid} faceId={fid} />)}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
