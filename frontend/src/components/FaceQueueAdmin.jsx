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

function Crop({ faceId, score, badge, selected, onClick, onPeek, sourceFile, box }) {
  return (
    <button className={`fq-crop ${selected ? "on" : ""}`} onClick={onClick} type="button"
            onMouseEnter={() => onPeek?.({ faceId, sourceFile, box })}
            onFocus={() => onPeek?.({ faceId, sourceFile, box })}>
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
  const [openCluster, setOpenCluster] = useState(null);   // expanded cluster id
  const [clusterFaces, setClusterFaces] = useState([]);   // its full face list
  const [faceSel, setFaceSel] = useState(() => new Set());
  const [peek, setPeek] = useState(null);   // {faceId, sourceFile} under the cursor
  const [undoInfo, setUndoInfo] = useState(null);
  const [activeCluster, setActiveCluster] = useState(null);  // what `I` will ignore
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [note, setNote] = useState(null);

  const refreshUndo = () => api.undoPeek().then(setUndoInfo).catch(() => setUndoInfo(null));
  const loadQueue = () => api.faceQueue().then(setQueue).catch((e) => setErr(String(e)));
  const loadClusters = () => Promise.all([api.faceClusters(), api.faceClusterSummary()])
    .then(([c, s]) => { setClusters(c); setSummary(s); })
    .catch((e) => setErr(String(e)));

  useEffect(() => { loadQueue(); loadClusters(); refreshUndo(); }, []);

  // Land on the first person automatically — on open, and again whenever the current
  // one is finished and drops out of the queue. Saves a click per person across a long
  // pass, and keeps the reviewer in the grid rather than back in the sidebar.
  useEffect(() => {
    if (!queue || !queue.length) return;
    if (!person || !queue.some((q) => q.person_id === person)) {
      setPerson(queue[0].person_id);
    }
  }, [queue]);   // eslint-disable-line

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
      const res = await api.decideFaces(ids, action);
      if (action === "accept" && res && res.decided > (res.tags_added + res.regions_filled)) {
        // Not an error: some faces resolve to a (photo, person) that already has a box,
        // so accepting them changes nothing. Say so rather than appearing to no-op.
        setNote(`${res.decided} reviewed · ${res.tags_added} tagged` +
                (res.regions_filled ? ` · ${res.regions_filled} outlined` : "") +
                ` · ${res.decided - res.tags_added - res.regions_filled} already covered`);
      } else setNote(null);
      const rest = items.filter((i) => !ids.includes(i.suggestion_id));
      setItems(rest);
      setSel(new Set());
      await loadQueue();
      await refreshUndo();
      onChanged?.();
    } catch (e) { setErr(String(e?.message || e)); } finally { setBusy(false); }
  }

  async function openGroup(id) {
    if (openCluster === id) { setOpenCluster(null); return; }
    setOpenCluster(id); setFaceSel(new Set()); setClusterFaces([]);
    try { setClusterFaces(await api.clusterFaces(id)); }
    catch (e) { setErr(String(e?.message || e)); }
  }

  // Split a mixed group: act on the selected faces only. Clustering groups by
  // appearance, not identity — two different dogs land together — so whole-group
  // decisions alone would force naming both as one animal.
  async function assignSelected(action, personId) {
    const ids = [...faceSel];
    if (!ids.length) return;
    setBusy(true); setErr(null);
    try {
      await api.assignFaces({ face_ids: ids, action, person_id: personId || null });
      setClusterFaces((f) => f.filter((x) => !faceSel.has(x.face_id)));
      setFaceSel(new Set());
      await loadClusters();
      await refreshUndo();
      onChanged?.();
    } catch (e) { setErr(String(e?.message || e)); } finally { setBusy(false); }
  }

  async function doUndo() {
    setBusy(true); setErr(null);
    try {
      const r = await api.undo();
      setNote(`Undone: ${r.undone}${r.new ? ` — ${r.new}` : ""}`);
      await Promise.all([loadQueue(), loadClusters(), refreshUndo()]);
      if (person) api.faceQueuePerson(person).then(setItems).catch(() => {});
      onChanged?.();
    } catch (e) { setErr(String(e?.message || e)); } finally { setBusy(false); }
  }

  async function decideCluster(body) {
    setBusy(true); setErr(null);
    try {
      await api.decideClusters(body);
      await loadClusters();
      await refreshUndo();
      onChanged?.();
    } catch (e) { setErr(String(e?.message || e)); } finally { setBusy(false); }
  }

  // Keyboard shortcuts. A 360-item pass is mostly mouse travel otherwise, and accept/
  // reject are the two keys pressed constantly. Guarded so typing in the cluster-naming
  // select or any input never triggers a bulk write.
  useEffect(() => {
    const onKey = (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const tag = e.target?.tagName;
      if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
      const k = e.key.toLowerCase();
      if (k === "escape") {            // clear first, close only when nothing is selected
        if (sel.size) { e.preventDefault(); setSel(new Set()); } else onClose();
        return;
      }
      if (busy) return;
      if (tab === "unknown") {
        // `I` ignores whatever is highlighted: the selected faces inside an expanded
        // group, else the whole group under the cursor. The highlight is what makes it
        // unambiguous which one is about to go.
        if (k === "i") {
          if (openCluster && faceSel.size) { e.preventDefault(); assignSelected("ignore"); }
          else if (activeCluster) {
            e.preventDefault();
            decideCluster({ action: "ignore", cluster_ids: [activeCluster] });
          }
        }
        return;
      }
      if (tab !== "suggestions" || !person) return;
      if (k === "a" && sel.size) { e.preventDefault(); decide("accept", [...sel]); }
      else if (k === "r" && sel.size) { e.preventDefault(); decide("reject", [...sel]); }
      else if (k === "s") { e.preventDefault(); setSel(new Set(items.map((i) => i.suggestion_id))); }
      else if (k === "c") { e.preventDefault(); setSel(new Set()); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [tab, person, sel, items, busy, activeCluster, openCluster, faceSel]);   // eslint-disable-line

  const totalPending = useMemo(
    () => (queue || []).reduce((n, r) => n + r.pending, 0), [queue]);
  const highConf = items.filter((i) => i.score >= 0.6).map((i) => i.suggestion_id);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal wide face-queue" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Face review</h2>
          <button className="lb-close" onClick={onClose} aria-label="Close">✕</button>
        </div>

        <div className="fq-topbar">
          <div className="seg fq-tabs">
          <button className={tab === "suggestions" ? "on" : ""} onClick={() => setTab("suggestions")}>
            Suggestions {totalPending ? `(${totalPending.toLocaleString()})` : ""}
          </button>
          <button className={tab === "unknown" ? "on" : ""} onClick={() => setTab("unknown")}>
            Unknown faces {clusters ? `(${clusters.length})` : ""}
          </button>
          </div>
          {/* Undo lives in the header menu, which this panel covers — and closing the
              panel loses your place mid-pass. Surfacing it here makes a mis-click one
              click to reverse without leaving the queue. */}
          <button className="fq-undo" disabled={busy || !undoInfo?.available}
                  onClick={doUndo}
                  title={undoInfo?.available
                    ? `Undo ${undoInfo.field}${undoInfo.new ? `: ${undoInfo.new}` : ""}`
                    : "Nothing to undo"}>
            ↶ Undo{undoInfo?.available && undoInfo.new ? ` · ${undoInfo.new}` : ""}
          </button>
        </div>

        {err && (
          <p className="error" role="alert">
            {err}
            <button className="link" style={{ marginLeft: 10, color: "#fff" }}
                    onClick={() => setErr(null)}>dismiss</button>
          </p>
        )}

        {note && <p className="hint note">{note}</p>}

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
                            disabled={busy}>Select all <kbd>S</kbd></button>
                    <button onClick={() => setSel(new Set())} disabled={busy || !sel.size}>
                      Clear <kbd>C</kbd></button>
                    {/* Bulk-accept-by-score is a power tool: it commits without the
                        reviewer looking at the faces, so it sits with the selection
                        helpers as a quiet link rather than beside the primary actions
                        where it can be hit by reflex. */}
                    <button className="link fq-bulk" disabled={busy || !highConf.length}
                            title="Accept every suggestion scoring 0.60 or better, without reviewing them"
                            onClick={() => decide("accept", highConf)}>
                      accept ≥0.60 ({highConf.length})
                    </button>
                    <span className="spacer" />
                    <button className="fq-accept" disabled={busy || !sel.size}
                            onClick={() => decide("accept", [...sel])}>
                      ✓ Accept {sel.size || ""} <kbd>A</kbd>
                    </button>
                    <button className="fq-reject" disabled={busy || !sel.size}
                            onClick={() => decide("reject", [...sel])}>
                      ✕ Reject {sel.size || ""} <kbd>R</kbd>
                    </button>
                  </div>
                  <p className="hint">
                    Click a face to select it. Crops are sorted most-confident first, so the
                    reliable ones cluster at the top — scan down until they stop looking right.
                    <b> box-only</b> means this person is already tagged on that photo and
                    accepting just adds the face outline.
                  </p>
                  <div className="fq-grid" onMouseLeave={() => setPeek(null)}>
                    {items.map((i) => (
                      <Crop key={i.suggestion_id} faceId={i.face_id} score={i.score}
                            sourceFile={i.source_file} box={i.box} onPeek={setPeek}
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

        {peek && (
          // Grid crops are ~96px, which is not enough to tell two dogs or two siblings
          // apart. Hovering shows a much larger crop AND the whole photo — context often
          // settles it faster than resolution does (who else is in frame, where it is).
          <div className="fq-peek" onMouseLeave={() => setPeek(null)}>
            <img className="fq-peek-face" src={`/api/face-crop/${peek.faceId}?size=480`} alt="" />
            {peek.sourceFile && (
              // The wrapper shrink-wraps the image (no object-fit), so a box positioned
              // in % of the wrapper lands exactly on the face regardless of how the
              // photo scales. Without this outline, two adjacent faces both sit inside
              // the crop and there is no way to tell which one is being judged.
              <span className="fq-peek-wrap">
                <img className="fq-peek-photo"
                     src={`/api/display/${encodeURIComponent(peek.sourceFile)}`} alt="" />
                {peek.box && (
                  <span className="fq-peek-box" style={{
                    left: `${(peek.box[0] - peek.box[2] / 2) * 100}%`,
                    top: `${(peek.box[1] - peek.box[3] / 2) * 100}%`,
                    width: `${peek.box[2] * 100}%`,
                    height: `${peek.box[3] * 100}%`,
                  }} />
                )}
              </span>
            )}
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
                <div className={`fq-cluster ${activeCluster === c.id ? "active" : ""}`}
                     key={c.id} onMouseEnter={() => setActiveCluster(c.id)}>
                  <div className="fq-cluster-head">
                    <b>{c.n_faces} faces</b>
                    <button className="link" onClick={() => openGroup(c.id)}>
                      {openCluster === c.id ? "collapse" : "split / pick faces"}
                    </button>
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
                    <button className="fq-reject" disabled={busy}
                            onClick={() => decideCluster({ action: "ignore", cluster_ids: [c.id] })}>
                      Ignore <kbd>I</kbd>
                    </button>
                  </div>
                  {openCluster === c.id ? (
                    <>
                      <div className="fq-actions fq-split">
                        <span className="muted">
                          {faceSel.size ? `${faceSel.size} selected` : "Click faces to select"}
                        </span>
                        <button onClick={() => setFaceSel(new Set(clusterFaces.map((f) => f.face_id)))}
                                disabled={busy}>All</button>
                        <button onClick={() => setFaceSel(new Set())} disabled={busy || !faceSel.size}>
                          None</button>
                        <span className="spacer" />
                        <select value={naming[c.id] || ""} disabled={busy}
                                onChange={(e) => setNaming((n) => ({ ...n, [c.id]: e.target.value }))}>
                          <option value="">Tag selected as…</option>
                          {people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                        </select>
                        <button className="primary" disabled={busy || !faceSel.size || !naming[c.id]}
                                onClick={() => assignSelected("name", naming[c.id])}>
                          Tag {faceSel.size || ""}
                        </button>
                        <button disabled={busy || !faceSel.size}
                                onClick={() => assignSelected("ignore")}>
                          Ignore {faceSel.size || ""}
                        </button>
                      </div>
                      <div className="fq-grid small">
                        {clusterFaces.map((f) => (
                          <Crop key={f.face_id} faceId={f.face_id}
                                sourceFile={f.source_file} box={f.box} onPeek={setPeek}
                                selected={faceSel.has(f.face_id)}
                                onClick={() => setFaceSel((s) => {
                                  const n = new Set(s);
                                  n.has(f.face_id) ? n.delete(f.face_id) : n.add(f.face_id);
                                  return n;
                                })} />
                        ))}
                      </div>
                    </>
                  ) : (
                    <div className="fq-grid small">
                      {c.sample_face_ids.map((fid) => <Crop key={fid} faceId={fid} />)}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
