import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import FaceInspector from "./FaceInspector";

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

function Crop({ faceId, score, badge, selected, onClick, onPeek, sourceFile, box,
                peeked, hoverPeek = true, tagRef, tagged }) {
  // `tagRef` crops the TAG's own region. A tag's box and a detected face's box are
  // independent, so LR-imported tags usually match no detection — resolving them to a
  // face_id left 254 of 400 rendering as broken squares.
  const info = { faceId, sourceFile, box, tagRef, tagged };
  const src = tagRef
    ? `/api/tag-crop/${tagRef[0]}/${encodeURIComponent(tagRef[1])}`
    : `/api/face-crop/${faceId}`;
  return (
    <button className={`fq-crop ${selected ? "on" : ""} ${peeked ? "peeked" : ""}`}
            type="button"
            onClick={(e) => { onPeek?.(info); onClick?.(e); }}
            onMouseEnter={hoverPeek ? () => onPeek?.(info) : undefined}
            onFocus={hoverPeek ? () => onPeek?.(info) : undefined}>
      <img src={src} alt="" loading="lazy" />
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
  // Two lenses on the same problem. "score" only sees tags this app accepted; "tiny"
  // sees every boxed tag however it arrived — including straight from Lightroom, which
  // is where the Mary Emma Beck / Brendan Lefkowicz mis-tags actually came from.
  // One loader for the whole audit. Filters are held in state and merged per call, so a
  // control can change just its own dimension without resetting the others.
  const PAGE = 300;
  // Paged rather than capped. The grid makes one image request per row, so loading
  // 10,000 at once stalls the browser — but a silent cap is worse, because you cannot
  // tell what you are not seeing. The sidebar counts give the true total, and "load
  // more" appends a page.
  const auditFilters = (over = {}) => ({
    personId: "personId" in over ? over.personId : tinyPerson,
    maxScore: "maxScore" in over ? over.maxScore : auditMax,
    maxArea:  "maxArea"  in over ? over.maxArea  : (tinyOnly ? 0.003 : null),
    sort:     over.sort  ?? auditSort,
  });
  const reloadAudit = (over = {}) => {
    const q = auditFilters(over);
    setAuditFilter(q);
    api.faceTags({ ...q, limit: PAGE, offset: 0 })
      .then((d) => setAudit(d.map((r) => ({ ...r, key: `${r.photo_id}:${r.person_id}` }))))
      .catch((e) => setErr(String(e)));
    api.faceTagsByPerson({ maxScore: q.maxScore, maxArea: q.maxArea })
      .then(setTinyPeople).catch(() => {});
    setAuditSel(new Set());
  };
  const loadNoFace = () =>
    api.peopleWithoutFace().then((d) => {
      setNoFace(d);
      // Pre-select each person's largest face. A thumbnail is cosmetic, not a claim
      // about who is in a photo, so proposing one is safe — but it is still a proposal:
      // nothing is written until Set is pressed.
      setPick(Object.fromEntries(d.filter((p) => p.candidates.length)
                                  .map((p) => [p.person_id, p.candidates[0].photo_id])));
    }).catch((e) => setErr(String(e)));

  async function saveRepresentatives(only = null) {
    const picks = Object.entries(pick).filter(([k]) => !only || k === only);
    if (!picks.length) return;
    setBusy(true); setErr(null);
    try {
      const r = await api.setRepresentatives(picks);
      setNote(`Set ${r.set} thumbnail(s).`);
      await Promise.all([loadNoFace(), refreshUndo()]);
      onChanged?.();
    } catch (e) { setErr(String(e?.message || e)); } finally { setBusy(false); }
  }

  const loadMoreAudit = () => {
    setBusy(true);
    api.faceTags({ ...auditFilter, limit: PAGE, offset: audit.length })
      .then((d) => setAudit((cur) => [...cur,
        ...d.map((r) => ({ ...r, key: `${r.photo_id}:${r.person_id}` }))]))
      .catch((e) => setErr(String(e)))
      .finally(() => setBusy(false));
  };
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

  // Same auto-advance for groups: after a Tag all / Ignore the list reloads and the
  // old id is gone, which left nothing highlighted and broke the ↑/↓ loop.
  useEffect(() => {
    if (!clusters || !clusters.length) return;
    if (!activeCluster || !clusters.some((c) => c.id === activeCluster)) {
      setActiveCluster(clusters[0].id);
    }
  }, [clusters]);   // eslint-disable-line

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

  const slugify = (n) =>
    (n || "").toLowerCase().trim().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");

  // Creation is a DISTINCT, labelled action — never a silent side-effect of a typo.
  // The button says ＋ Create "Dave Wilson" only when the text matches nobody, so a
  // mistyped "Kat" can't quietly become a new person alongside Kate.
  async function createAndTag(id, applyTo) {
    const name = (naming[id] || "").trim();
    const slug = slugify(name);
    if (!name || !slug) { setErr("Type a name first."); return; }
    setBusy(true); setErr(null);
    try {
      const p = await api.createPerson({ id: slug, canonical_name: name,
                                         is_family: newIsFamily });
      const person = { id: p.id || slug, name };
      setNewPeople((xs) => [...xs, person]);
      if (applyTo === "faces") await assignSelected("name", person.id);
      else await decideCluster({ action: "name", cluster_ids: [id], person_id: person.id });
      setNote(`Created ${name}${newIsFamily ? " (family)" : ""} and tagged them.`);
    } catch (e) {
      setErr(`${e?.message || e}` + (String(e).includes("already exists")
        ? " — pick them from the list instead." : ""));
    } finally { setBusy(false); }
  }

  function tagCluster(id) {
    const pid = resolvePerson(naming[id]);
    if (!pid) { setErr("Pick a name from the list first."); return; }
    decideCluster({ action: "name", cluster_ids: [id], person_id: pid });
  }

  async function removeSelectedTags() {
    const pairs = audit.filter((a) => auditSel.has(a.key)).map((a) => [a.photo_id, a.person_id]);
    if (!pairs.length) return;
    setBusy(true); setErr(null);
    try {
      const r = await api.removeTinyTags(pairs);
      setNote(`Removed ${r.removed} tag(s).`);
      reloadAudit();
      await Promise.all([loadQueue(), refreshUndo()]);
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

      // ↑/↓ step through the list on whichever tab is open. preventDefault stops the
      // browser scrolling the panel out from under the selection.
      if (k === "arrowdown" || k === "arrowup") {
        const down = k === "arrowdown";
        if (tab === "suggestions" && queue?.length) {
          e.preventDefault();
          const ids = queue.map((q) => q.person_id);
          const i = ids.indexOf(person);
          setPerson(ids[i < 0 ? 0 : Math.min(Math.max(i + (down ? 1 : -1), 0), ids.length - 1)]);
        } else if (tab === "unknown" && clusters?.length) {
          e.preventDefault();
          const ids = clusters.map((c) => c.id);
          const i = ids.indexOf(activeCluster);
          setActiveCluster(ids[i < 0 ? 0 : Math.min(Math.max(i + (down ? 1 : -1), 0), ids.length - 1)]);
        }
        return;
      }

      if (tab === "unknown") {
        // `I` ignores whatever is highlighted: the selected faces inside an expanded
        // group, else the whole group under the cursor. The highlight is what makes it
        // unambiguous which one is about to go.
        if (k === "n" && activeCluster) {
          // Jump into the name box for the highlighted group. Explicit rather than
          // auto-focusing on arrow-navigation, which would swallow the next ↑/↓.
          e.preventDefault();
          document.querySelector(`.fq-nameinput[data-cluster="${activeCluster}"]`)?.focus();
          return;
        }
        if (k === "t" && activeCluster && resolvePerson(naming[activeCluster])) {
          e.preventDefault(); tagCluster(activeCluster); return;
        }
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
  }, [tab, person, sel, items, busy, activeCluster, openCluster, faceSel, queue, clusters, naming]);   // eslint-disable-line

  // Both lists scroll, so a keyboard step that lands off-screen would look like
  // nothing happened. `block: "nearest"` scrolls only when it has to, so mouse-driven
  // selection doesn't jump the list around.
  useEffect(() => {
    document.querySelector(".fq-people button.on")?.scrollIntoView({ block: "nearest" });
  }, [person]);
  useEffect(() => {
    document.querySelector(".fq-cluster.active")?.scrollIntoView({ block: "nearest" });
  }, [activeCluster]);

  // The Unknown-faces preview is click-sticky, so drop it when the context changes —
  // otherwise it lingers showing a face from a group that is no longer on screen.
  useEffect(() => { setPeek(null); }, [tab, openCluster]);

  // A <select> only type-ahead-matches from the START of an option, which is useless
  // across 116 people — typing "mae" should find every Maegley. An input + datalist is a
  // real combo box: substring matching, keyboard-driven, native dropdown. It hands back
  // TEXT though, so resolve it to a person id (case-insensitively) on commit.
  // People created from this panel are merged in locally so the combo box and the
  // name→id resolution work immediately, without waiting for a parent refetch.
  const [newPeople, setNewPeople] = useState([]);
  const allPeople = useMemo(() => [...(people || []), ...newPeople], [people, newPeople]);
  const [newIsFamily, setNewIsFamily] = useState(false);
  const [audit, setAudit] = useState([]);          // accepted, worst score first
  const [auditMax, setAuditMax] = useState(null);      // score filter, null = any
  const [auditSel, setAuditSel] = useState(() => new Set());
  const [auditSort, setAuditSort] = useState("area");
  const [auditFilter, setAuditFilter] = useState({});
  const [noFace, setNoFace] = useState(null);      // people lacking a thumbnail
  const [pick, setPick] = useState({});            // person_id -> chosen photo_id
  const [tinyOnly, setTinyOnly] = useState(true);
  const [tinyPeople, setTinyPeople] = useState(null);    // per-person counts
  const [tinyPerson, setTinyPerson] = useState(null);    // filter, null = everyone
  const [unnamed, setUnnamed] = useState([]);            // good faces with no name
  const [unnamedTotal, setUnnamedTotal] = useState(0);
  const [unnamedSel, setUnnamedSel] = useState(() => new Set());
  const [unnamedTier, setUnnamedTier] = useState("big"); // big | medium | all
  const [unnamedIgnored, setUnnamedIgnored] = useState(false);
  const [unnamedName, setUnnamedName] = useState("");
  const [inspectIdx, setInspectIdx] = useState(null);  // index into `unnamed`

  const UNNAMED_TIERS = {
    big:    { minArea: 0.01,  minScore: 0.75, label: "big + confident" },
    medium: { minArea: 0.004, minScore: 0.70, label: "medium and up" },
    all:    { minArea: 0,     minScore: 0,    label: "everything" },
  };

  async function loadUnnamed(opts = {}) {
    const tier = UNNAMED_TIERS[opts.tier ?? unnamedTier];
    const includeIgnored = opts.includeIgnored ?? unnamedIgnored;
    const append = opts.append || false;
    setBusy(true);
    try {
      const r = await api.facesUnnamed({
        ...tier, includeIgnored, limit: 300,
        offset: append ? unnamed.length : 0,
      });
      setUnnamedTotal(r.total);
      setUnnamed((xs) => (append ? [...xs, ...r.items] : r.items));
      if (!append) setUnnamedSel(new Set());
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  }

  // ---- single-face actions, driven by the inspector ----
  // These operate on one face by id rather than on the selection, because the inspector
  // is the singular tool and the grid's selection is the bulk one. Keeping them separate
  // is what stops Move fighting multiselect for the same toolbar.
  const inspected = inspectIdx == null ? null : unnamed[inspectIdx];

  // Acting on a face removes it from the list, so the index quietly advances to the next
  // one — which is the behaviour you want mid-pass. At the end of the list it would run
  // off and the inspector would vanish without explanation, so clamp instead.
  useEffect(() => {
    if (inspectIdx == null) return;
    if (!unnamed.length) setInspectIdx(null);
    else if (inspectIdx > unnamed.length - 1) setInspectIdx(unnamed.length - 1);
  }, [unnamed, inspectIdx]);

  async function afterSingleAction(msg) {
    setNote(msg);
    await Promise.all([loadUnnamed(), refreshUndo()]);
    onChanged?.();
  }

  async function moveBoxTo(personId, personName) {
    if (!inspected) return;
    setBusy(true); setErr(null);
    try {
      const [x, y, w, h] = inspected.box;
      // Deliberately no set_representative: relocating a box must not also reassign
      // whose photo is used for their thumbnail.
      await api.setFaceRegion(personId, { photo_id: inspected.photo_id, x, y, w, h });
      await afterSingleAction(`Moved ${personName}'s box onto this face.`);
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  }

  async function tagInspected(personId) {
    if (!inspected || !personId) return;
    setBusy(true); setErr(null);
    try {
      const r = await api.assignFaces({ action: "name", person_id: personId,
                                        face_ids: [inspected.face_id] });
      if ((r.blocked || []).length) {
        // Only reachable when a real box already exists; a tag with no box gets its
        // region filled server-side instead of blocking.
        setErr("They already have a face box on this photo — use Move to put it here.");
      } else {
        await afterSingleAction(r.regions_filled ? "Added their face box." : "Tagged.");
      }
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  }

  // Creating a person is a DISTINCT, labelled action here too — the button only appears
  // when the typed text matches nobody, so a mistyped "Kat" can't quietly become a new
  // person alongside Kate. Feeds `newPeople` so the name is pickable immediately after.
  async function createAndTagInspected(name, isFamily) {
    const slug = slugify(name);
    if (!inspected || !name || !slug) { setErr("Type a name first."); return; }
    setBusy(true); setErr(null);
    try {
      const p = await api.createPerson({ id: slug, canonical_name: name,
                                         is_family: isFamily });
      const person = { id: p.id || slug, name };
      setNewPeople((xs) => [...xs, person]);
      await api.assignFaces({ action: "name", person_id: person.id,
                              face_ids: [inspected.face_id] });
      await afterSingleAction(`Created ${name}${isFamily ? " (family)" : ""} and tagged them.`);
      onChanged?.();
    } catch (e) {
      setErr(`${e?.message || e}` + (String(e).includes("already exists")
        ? " — pick them from the list instead." : ""));
    } finally { setBusy(false); }
  }

  async function ignoreInspected(noCentroid = false) {
    if (!inspected) return;
    setBusy(true); setErr(null);
    try {
      await api.assignFaces({ action: "ignore", face_ids: [inspected.face_id],
                              no_centroid: noCentroid });
      await afterSingleAction(noCentroid ? "Dismissed as a duplicate." : "Ignored.");
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  }

  // Naming here writes ordinary tags via the same endpoint the cluster splitter uses,
  // so these faces become references like any other — which is the whole point: the
  // people in this list are the ones the matcher could never reach on its own.
  async function decideUnnamed(action) {
    const personId = action === "name" ? resolvePerson(unnamedName) : null;
    if (action === "name" && !personId) { setErr("Pick a name first."); return; }
    if (!unnamedSel.size) return;
    setBusy(true); setErr(null);
    try {
      const r = await api.assignFaces({ action, person_id: personId,
                                        face_ids: [...unnamedSel] });
      // A face whose person is ALREADY boxed on that photo cannot be recorded — one
      // region per (photo, person). Saying so beats the old silent skip, which looked
      // like the tag simply hadn't worked.
      const blockedPhotos = new Set((r?.blocked || []).map(([photoId]) => photoId));
      if (blockedPhotos.size) {
        setErr(`${r.tags_added} tagged · ${blockedPhotos.size} could not be: that person `
             + `already has a face box on those photos. If this really is them, the `
             + `existing box is on the wrong face — fix it in the lightbox.`);
      }
      // Blocked faces stay in the list; nothing was written for them.
      setUnnamed((xs) => xs.filter((f) => !unnamedSel.has(f.face_id)
                                          || blockedPhotos.has(f.photo_id)));
      setUnnamedTotal((n) => Math.max(0, n - (r?.tags_added ?? unnamedSel.size)));
      setUnnamedSel(new Set());
      setUnnamedName("");
      await refreshUndo();
      onChanged?.();
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  }

  const personByName = useMemo(() => {
    const map = new Map();
    allPeople.forEach((p) => map.set((p.name || "").toLowerCase(), p.id));
    return map;
  }, [allPeople]);
  const resolvePerson = (text) => personByName.get((text || "").trim().toLowerCase()) || null;

  // When the person you are naming already has a box on this photo, a second tag cannot
  // be stored (one region per photo+person) and adding one would be junk. Almost always
  // the truth is that their existing box is on the WRONG face — so move it here instead
  // of creating anything. Same undoable endpoint the lightbox uses; the prior region is
  // restored on undo, and the thumbnail is deliberately left alone.

  const auditTotal = useMemo(() => {
    if (!tinyPeople) return audit.length;
    return tinyPerson
      ? (tinyPeople.find((r) => r.person_id === tinyPerson)?.count ?? audit.length)
      : tinyPeople.reduce((n, r) => n + r.count, 0);
  }, [tinyPeople, tinyPerson, audit.length]);

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
          <button className={tab === "audit" ? "on" : ""}
                  onClick={() => { setTab("audit"); reloadAudit(); }}>
            Check accepted
          </button>
          <button className={tab === "unnamed" ? "on" : ""}
                  onClick={() => { setTab("unnamed"); loadUnnamed(); }}>
            Unnamed faces{unnamedTotal ? ` (${unnamedTotal.toLocaleString()})` : ""}
          </button>
          <button className={tab === "faces" ? "on" : ""}
                  onClick={() => { setTab("faces"); loadNoFace(); }}>
            Missing thumbnails{noFace ? ` (${noFace.length})` : ""}
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
                            disabled={busy}>Select all {items.length} <kbd>S</kbd></button>
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
                  {sel.size > 12 && (
                    <p className="warn">
                      ⚠ {sel.size} selected — the grid scrolls, so most of these are
                      off-screen. Scroll through them before accepting: confidence drops
                      toward the bottom, and that is where mis-IDs live.
                    </p>
                  )}
                  <p className="hint">
                    <kbd>↑</kbd><kbd>↓</kbd> move between people.
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

        {inspected && (
          <FaceInspector
            item={inspected} index={inspectIdx} total={unnamed.length} busy={busy}
            allPeople={allPeople} resolvePerson={resolvePerson}
            onPrev={() => setInspectIdx((i) => Math.max(0, i - 1))}
            onNext={() => setInspectIdx((i) => Math.min(unnamed.length - 1, i + 1))}
            onClose={() => setInspectIdx(null)}
            onMove={moveBoxTo} onTag={tagInspected} onIgnore={ignoreInspected}
            onCreateAndTag={createAndTagInspected}
            /* The panel's own error line sits BEHIND this modal, so a failure in here
               was completely invisible — "I click Tag and nothing happens". */
            error={err} onDismissError={() => setErr(null)} />
        )}

        {peek && (
          // Grid crops are ~96px, which is not enough to tell two dogs or two siblings
          // apart. Hovering shows a much larger crop AND the whole photo — context often
          // settles it faster than resolution does (who else is in frame, where it is).
          <div className={`fq-peek ${tab === "unknown" || tab === "unnamed" ? "sticky" : ""}`}
               onMouseLeave={() => { if (tab !== "unknown" && tab !== "unnamed") setPeek(null); }}>

        {(tab === "unknown" || tab === "unnamed") && (
              <button className="fq-peek-close" onClick={() => setPeek(null)}
                      aria-label="Close preview">✕</button>
            )}
            <div className="fq-peek-col">
              <img className="fq-peek-face" alt=""
                   src={peek.tagRef
                     ? `/api/tag-crop/${peek.tagRef[0]}/${encodeURIComponent(peek.tagRef[1])}?size=480`
                     : `/api/face-crop/${peek.faceId}?size=480`} />
              {peek.sourceFile && (
                <code className="fq-peek-name" title={peek.sourceFile}>
                  {peek.sourceFile.split("/").pop()}
                </code>
              )}
              {peek.tagged && (
                <div className="fq-peek-tags">
                  {peek.tagged.length
                    ? <>already tagged: {peek.tagged.map((t) => t.name
                        + (t.box ? "" : " (no box)")).join(" · ")}</>
                    : <>nobody tagged on this photo yet</>}
                </div>
              )}
            </div>
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
                {/* Who is ALREADY tagged here, drawn where they sit. Without this you
                    cannot tell whether the person you are about to name is in the photo
                    twice, or once with their box on the wrong face. */}
                {(peek.tagged || []).filter((t) => t.box).map((t) => (
                  <span key={t.person_id} className="fq-peek-tagbox" style={{
                    left: `${(t.box[0] - t.box[2] / 2) * 100}%`,
                    top: `${(t.box[1] - t.box[3] / 2) * 100}%`,
                    width: `${t.box[2] * 100}%`,
                    height: `${t.box[3] * 100}%`,
                  }}><i>{t.name}</i></span>
                ))}
              </span>
            )}
          </div>
        )}

        {tab === "unnamed" && (
          <div className="fq-grid-wrap">
            <p className="hint">
              Good faces carrying no name. A suggestion needs a person model to match
              against, so anyone with fewer than three reference faces — or who isn't in
              the archive at all — can never be suggested. This asks the question the
              other way round: <b>is this a good face with nobody on it?</b> Biggest and
              sharpest first. <b>Click a face</b> to open the whole photo — name it there,
              or move a wrongly-placed box onto it. <b>⌘/Ctrl-click</b> to multi-select for
              bulk Tag or Ignore.
            </p>
            <div className="fq-actions">
              <label className="muted">show{" "}
                <select value={unnamedTier} disabled={busy}
                        onChange={(e) => { setUnnamedTier(e.target.value);
                                           loadUnnamed({ tier: e.target.value }); }}>
                  {Object.entries(UNNAMED_TIERS).map(([k, v]) =>
                    <option key={k} value={k}>{v.label}</option>)}
                </select>
              </label>
              <label className="fq-fam">
                <input type="checkbox" checked={unnamedIgnored} disabled={busy}
                       onChange={(e) => { setUnnamedIgnored(e.target.checked);
                                          loadUnnamed({ includeIgnored: e.target.checked }); }} />
                include ones you ignored
              </label>
              <span className="muted">
                {unnamed.length.toLocaleString()} of {unnamedTotal.toLocaleString()}
              </span>
              <span className="spacer" />
              <button onClick={() => setUnnamedSel(new Set(unnamed.map((f) => f.face_id)))}
                      disabled={busy || !unnamed.length}>Select all {unnamed.length}</button>
              <button onClick={() => setUnnamedSel(new Set())}
                      disabled={busy || !unnamedSel.size}>Clear</button>
              {unnamed.length < unnamedTotal && (
                <button disabled={busy} onClick={() => loadUnnamed({ append: true })}>
                  Load {Math.min(300, unnamedTotal - unnamed.length).toLocaleString()} more
                </button>
              )}
            </div>
            <div className="fq-actions">
              <input className="fq-nameinput" list="pplx-unnamed" value={unnamedName}
                     disabled={busy || !unnamedSel.size}
                     placeholder={unnamedSel.size ? "Who are these?" : "select faces first"}
                     onChange={(e) => setUnnamedName(e.target.value)}
                     onKeyDown={(e) => { if (e.key === "Enter" && resolvePerson(unnamedName))
                                           decideUnnamed("name"); }} />
              <datalist id="pplx-unnamed">
                {allPeople.map((p) => <option key={p.id} value={p.name} />)}
              </datalist>
              <button className="primary"
                      disabled={busy || !unnamedSel.size || !resolvePerson(unnamedName)}
                      onClick={() => decideUnnamed("name")}>
                Tag {unnamedSel.size || ""}
              </button>
              <button disabled={busy || !unnamedSel.size}
                      onClick={() => decideUnnamed("ignore")}>
                Ignore {unnamedSel.size || ""}
              </button>
              <span className="muted fq-bulk">
                Naming here also fixes the enrolment gap — 24 people have only 1–2
                reference faces and stay invisible to matching until they have 3.
              </span>
            </div>
            <div className="fq-grid" onMouseLeave={() => setPeek(null)}>
              {unnamed.map((f, i) => (
                <Crop key={f.face_id} faceId={f.face_id} sourceFile={f.source_file}
                      box={f.box} onPeek={setPeek} hoverPeek={false}
                      tagged={f.tagged} score={f.looks_like_score}
                      /* First name only — the tile is 104px. The ⚠ means that person is
                         already boxed on this photo, so either this is a sibling the
                         model can't separate, or their existing box is on the wrong face. */
                      badge={f.ignored ? "ignored"
                             : `${(f.looks_like || "?").split(" ")[0]}${f.boxed_here ? " ⚠" : ""}`}
                      peeked={peek?.faceId === f.face_id}
                      selected={unnamedSel.has(f.face_id)}
                      /* Plain click opens the photo — judging one face needs the whole
                         frame. ⌘/Ctrl/Shift-click toggles selection for the bulk verbs,
                         the same convention the gallery already uses. */
                      onClick={(e) => {
                        if (e.metaKey || e.ctrlKey || e.shiftKey) {
                          setUnnamedSel((s) => {
                            const n = new Set(s);
                            n.has(f.face_id) ? n.delete(f.face_id) : n.add(f.face_id);
                            return n;
                          });
                        } else {
                          setPeek(null);
                          setInspectIdx(i);
                        }
                      }} />
              ))}
              {!unnamed.length && !busy && (
                <p className="muted">Nothing left at this quality level.</p>
              )}
            </div>
          </div>
        )}

        {tab === "faces" && (
          <div className="fq-grid-wrap">
            <p className="hint">
              People with no thumbnail in the People filter. The largest face box is
              pre-selected for each — bigger crops make better thumbnails — but click any
              alternative to choose it instead. Nothing is written until you press Set.
            </p>
            <div className="fq-actions">
              <span className="muted">{(noFace || []).length} without a thumbnail</span>
              <span className="spacer" />
              <button className="primary" disabled={busy || !Object.keys(pick).length}
                      onClick={() => saveRepresentatives()}>
                Set all {Object.keys(pick).length}
              </button>
            </div>
            <div className="fq-noface">
              {(noFace || []).map((p) => (
                <div className="fq-nf-row" key={p.person_id}>
                  <div className="fq-nf-name">
                    <b>{p.name}</b>
                    {!p.candidates.length && <span className="muted"> — no face boxes yet</span>}
                  </div>
                  <div className="fq-grid small">
                    {p.candidates.map((c) => (
                      <Crop key={c.photo_id} faceId={null} tagRef={[c.photo_id, p.person_id]}
                            sourceFile={c.source_file} badge={c.year || null}
                            onPeek={setPeek} hoverPeek={false}
                            selected={pick[p.person_id] === c.photo_id}
                            onClick={() => setPick((x) => ({ ...x, [p.person_id]: c.photo_id }))} />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
        
        {tab === "audit" && (
          <div className="fq-grid-wrap">
            <p className="hint">
              Every face tag with a box, from any source. Filter and sort to find the wrong
              ones: small boxes are usually background faces or reflections, and low
              confidence means the matcher was unsure. Tags marked <b>imported</b> came from
              Lightroom and have no confidence score at all — those are invisible to a
              score filter, which is why size matters. Click a face to enlarge it.
            </p>
            <div className="fq-actions">
              <label className="muted">sort{" "}
                <select value={auditSort} disabled={busy}
                        onChange={(e) => { setAuditSort(e.target.value); reloadAudit({ sort: e.target.value }); }}>
                  <option value="area">smallest face first</option>
                  <option value="score">lowest confidence first</option>
                </select>
              </label>
              <label className="muted">score ≤{" "}
                <select value={auditMax ?? ""} disabled={busy}
                        onChange={(e) => { const v = e.target.value === "" ? null : +e.target.value;
                                            setAuditMax(v); reloadAudit({ maxScore: v }); }}>
                  <option value="">any</option>
                  <option value={0.5}>0.50</option>
                  <option value={0.55}>0.55</option>
                  <option value={0.6}>0.60</option>
                </select>
              </label>
              <label className="fq-fam">
                <input type="checkbox" checked={tinyOnly} disabled={busy}
                       onChange={(e) => { setTinyOnly(e.target.checked);
                                           reloadAudit({ maxArea: e.target.checked ? 0.003 : null }); }} />
                tiny faces only
              </label>
              <span className="muted">{audit.length.toLocaleString()} of {auditTotal.toLocaleString()}</span>
              <span className="spacer" />
              <button onClick={() => setAuditSel(new Set(audit.map((a) => a.key)))}
                      disabled={busy || !audit.length}>Select all {audit.length}</button>
              <button onClick={() => setAuditSel(new Set())} disabled={busy || !auditSel.size}>Clear</button>
              {audit.length < auditTotal && (
                <button onClick={loadMoreAudit} disabled={busy}>
                  Load {Math.min(300, auditTotal - audit.length).toLocaleString()} more
                </button>
              )}
              <button className="fq-reject" disabled={busy || !auditSel.size}
                      onClick={removeSelectedTags}>
                ✕ Remove tag {auditSel.size || ""}
              </button>
            </div>
            <div className="fq-tinybody">
              <ol className="fq-people fq-tinypeople">
                <li><button className={!tinyPerson ? "on" : ""}
                            onClick={() => { setTinyPerson(null); reloadAudit({ personId: null }); }}>
                  <span className="fq-name">Everyone</span>
                  <span className="fq-count">{(tinyPeople || []).reduce((n, r) => n + r.count, 0)}</span>
                </button></li>
                {(tinyPeople || []).map((r) => (
                  <li key={r.person_id}><button className={tinyPerson === r.person_id ? "on" : ""}
                      onClick={() => { setTinyPerson(r.person_id); reloadAudit({ personId: r.person_id }); }}>
                    <span className="fq-name">{r.name}</span>
                    <span className="fq-count">{r.count}</span>
                    <span className="muted fq-sub">{r.imported} imported · smallest {r.smallest.toFixed(5)}</span>
                  </button></li>
                ))}
              </ol>
              <div className="fq-grid">
                {audit.map((a) => (
                  <Crop key={a.key} faceId={null} score={a.score}
                        badge={tinyPerson ? (a.year || a.origin) : a.person}
                        sourceFile={a.source_file} box={a.box}
                        tagRef={[a.photo_id, a.person_id]}
                        onPeek={setPeek} hoverPeek={false}
                        peeked={peek?.tagRef?.[0] === a.photo_id && peek?.tagRef?.[1] === a.person_id}
                        selected={auditSel.has(a.key)}
                        onClick={() => setAuditSel((s2) => {
                          const n = new Set(s2);
                          n.has(a.key) ? n.delete(a.key) : n.add(a.key);
                          return n;
                        })} />
                ))}
              </div>
            </div>
          </div>
        )}

        {tab === "unknown" && (
          <div className="fq-unknown">
            <p className="hint">
Click any face to enlarge it and outline it in its photo.
              <kbd>↑</kbd><kbd>↓</kbd> move between groups · <kbd>N</kbd> name it (type any part,
              e.g. "mae") · <kbd>T</kbd> or <kbd>Enter</kbd> tags them all · <kbd>I</kbd> ignores.
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
                    {c.n_faces > (c.samples || []).length && openCluster !== c.id && (
                      <span className="warn-inline">
                        only {(c.samples || []).length} shown —
                        {" "}{c.n_faces - (c.samples || []).length} hidden
                      </span>
                    )}
                    <button className="link" onClick={() => openGroup(c.id)}>
                      {openCluster === c.id ? "collapse" : "see all / split"}
                    </button>
                    <span className="spacer" />
                    <input className="fq-nameinput" list={`ppl-${c.id}`} placeholder="Name as…"
                           value={naming[c.id] || ""} disabled={busy}
                           data-cluster={c.id}
                           onFocus={() => setActiveCluster(c.id)}
                           onChange={(e) => setNaming((n) => ({ ...n, [c.id]: e.target.value }))}
                           onKeyDown={(e) => {
                             // Enter commits from inside the field — the natural gesture,
                             // and it avoids `T` fighting with typing a name containing t.
                             if (e.key === "Enter") {
                               e.preventDefault();
                               resolvePerson(naming[c.id]) ? tagCluster(c.id) : createAndTag(c.id);
                             }
                             if (e.key === "Escape") { e.preventDefault(); e.currentTarget.blur(); }
                           }} />
                    <datalist id={`ppl-${c.id}`}>
                      {allPeople.map((p) => <option key={p.id} value={p.name} />)}
                    </datalist>
                    {resolvePerson(naming[c.id]) || !(naming[c.id] || "").trim() ? (
                      <button className="primary"
                              disabled={busy || !resolvePerson(naming[c.id])
                                        || (c.n_faces > (c.samples || []).length && openCluster !== c.id)}
                              title={c.n_faces > (c.samples || []).length && openCluster !== c.id
                                ? "See all faces first — this group has more than are shown"
                                : "Tag every face in this group"}
                              onClick={() => tagCluster(c.id)}>
                        Tag all {c.n_faces} <kbd>T</kbd>
                      </button>
                    ) : (
                      <>
                        <label className="fq-fam" title="Family member rather than friend/other">
                          <input type="checkbox" checked={newIsFamily}
                                 onChange={(e) => setNewIsFamily(e.target.checked)} /> family
                        </label>
                        <button className="primary" disabled={busy}
                                onClick={() => createAndTag(c.id)}>
                          ＋ Create “{naming[c.id]}” &amp; tag
                        </button>
                      </>
                    )}
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
                        <input className="fq-nameinput" list={`pplx-${c.id}`}
                               placeholder="Tag selected as…" value={naming[c.id] || ""}
                               disabled={busy} data-cluster={c.id}
                               onChange={(e) => setNaming((n) => ({ ...n, [c.id]: e.target.value }))}
                               onKeyDown={(e) => {
                                 if (e.key === "Enter") {
                                   e.preventDefault();
                                   const pid = resolvePerson(naming[c.id]);
                                   if (pid && faceSel.size) assignSelected("name", pid);
                                 }
                                 if (e.key === "Escape") { e.preventDefault(); e.currentTarget.blur(); }
                               }} />
                        <datalist id={`pplx-${c.id}`}>
                          {allPeople.map((p) => <option key={p.id} value={p.name} />)}
                        </datalist>
                        <button className="primary"
                                disabled={busy || !faceSel.size || !resolvePerson(naming[c.id])}
                                onClick={() => assignSelected("name", resolvePerson(naming[c.id]))}>
                          Tag {faceSel.size || ""}
                        </button>
                        <button disabled={busy || !faceSel.size}
                                onClick={() => assignSelected("ignore")}>
                          Ignore {faceSel.size || ""}
                        </button>
                      </div>
                      <div className="fq-grid expanded">
                        {clusterFaces.map((f) => (
                          <Crop key={f.face_id} faceId={f.face_id}
                                sourceFile={f.source_file} box={f.box} onPeek={setPeek}
                                hoverPeek={false} peeked={peek?.faceId === f.face_id}
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
                      {(c.samples || []).map((f) => (
                        <Crop key={f.face_id} faceId={f.face_id} sourceFile={f.source_file}
                              box={f.box} onPeek={setPeek} hoverPeek={false}
                              peeked={peek?.faceId === f.face_id} />
                      ))}
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
