import { useEffect, useRef, useState } from "react";

// Full-photo view for judging ONE unnamed face (SPEC §14).
//
// The grid is for bulk verbs — tag forty, ignore forty. Moving a box is neither: it is
// singular and spatial, and it needs the whole photo to be answerable at all. Cramming
// it onto the grid toolbar meant typing a name and hoping it collided with an existing
// tag. Here every person already boxed on the photo gets their own "move here" button,
// so the choice is made by looking rather than by typing.
export default function FaceInspector({ item, index, total, busy, allPeople,
                                        resolvePerson, onPrev, onNext, onClose,
                                        onMove, onTag, onIgnore, onCreateAndTag,
                                        error, onDismissError }) {
  const [name, setName] = useState("");
  const [isFamily, setIsFamily] = useState(false);
  const nameRef = useRef(null);
  useEffect(() => { setName(""); }, [item?.face_id]);

  // A second face of someone already tagged here — a collage, or a photo of a photo.
  // The data model holds one region per (photo, person), so there is nothing to record;
  // dismissing it must NOT store a centroid shaped like that person (see assign_faces).
  // Declared above the key handler that reads it: a const referenced from a closure
  // created earlier in the body is exactly the TDZ crash we already shipped once.
  const dupOf = (item?.tagged || []).find((t) => t.person_id === item?.looks_like_id);

  useEffect(() => {
    const onKey = (e) => {
      if (e.target.tagName === "INPUT") {
        if (e.key === "Escape") { e.target.blur(); e.stopPropagation(); }
        return;
      }
      if (e.key === "Escape") { onClose(); }
      else if (e.key === "ArrowLeft") { onPrev(); }
      else if (e.key === "ArrowRight") { onNext(); }
      else if (e.key.toLowerCase() === "i") { onIgnore(!!dupOf); }
      else if (e.key.toLowerCase() === "n") { e.preventDefault(); nameRef.current?.focus(); }
      else return;
      e.stopPropagation();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [item?.face_id, dupOf, onPrev, onNext, onClose, onIgnore]);

  if (!item) return null;
  const boxed = (item.tagged || []).filter((t) => t.box);
  const unboxed = (item.tagged || []).filter((t) => !t.box);
  const pid = resolvePerson(name);
  // Typing someone already boxed here is the move case, not the tag case — a second
  // region for the same (photo, person) cannot be stored.
  const typedIsBoxed = pid && boxed.some((t) => t.person_id === pid);
  // Offer creation only when the text matches nobody, so a mistyped "Kat" cannot
  // quietly become a second person alongside Kate.
  const canCreate = !pid && name.trim().length > 1;

  const pct = (b) => ({
    left: `${(b[0] - b[2] / 2) * 100}%`, top: `${(b[1] - b[3] / 2) * 100}%`,
    width: `${b[2] * 100}%`, height: `${b[3] * 100}%`,
  });

  return (
    <div className="modal-backdrop fi-backdrop" onClick={onClose}>
      <div className="modal fi-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>
            <code className="fi-file" title={item.source_file}>
              {item.source_file.split("/").pop()}
            </code>
            <span className="muted fi-sub">
              {item.year || item.origin}
              {item.hires && " · found at high-res"}
            </span>
          </h2>
          <span className="fi-nav">
            <button disabled={busy || index <= 0} onClick={onPrev} title="Previous (←)">‹</button>
            <span className="muted">{index + 1} / {total}</span>
            <button disabled={busy || index >= total - 1} onClick={onNext} title="Next (→)">›</button>
            <button className="lb-close" onClick={onClose}>✕</button>
          </span>
        </div>

        <div className="fi-stage">
          <span className="fi-wrap">
            <img className="fi-photo" alt=""
                 src={`/api/display/${encodeURIComponent(item.source_file)}`} />
            {boxed.map((t) => (
              <span key={t.person_id} className="fi-tagbox" style={pct(t.box)}>
                <i>{t.name}</i>
              </span>
            ))}
            {/* Drawn last so the face under review is never hidden by a tag box. */}
            <span className="fi-facebox" style={pct(item.box)}><i>this face</i></span>
          </span>
        </div>

        <div className="fi-info">
          <span>
            looks like <b>{item.looks_like}</b> · {item.looks_like_score?.toFixed(2)}
            {item.boxed_here && (
              <span className="warn-inline"> — already boxed on this photo</span>
            )}
          </span>
          {unboxed.length > 0 && (
            <span className="muted">
              tagged with no box: {unboxed.map((t) => t.name).join(", ")}
            </span>
          )}
        </div>

        {boxed.length > 0 && (
          <div className="fi-moves">
            <span className="muted">Their box is wrong? Put it on this face:</span>
            {boxed.map((t) => (
              <button key={t.person_id} className="ghost fi-move" disabled={busy}
                      onClick={() => onMove(t.person_id, t.name)}>
                ⤿ Move {t.name} here
              </button>
            ))}
          </div>
        )}

        {error && (
          <div className="error fi-error" onClick={onDismissError} title="Dismiss">
            {error}
          </div>
        )}

        <div className="fi-actions">
          <input ref={nameRef} className="fq-nameinput" list="fi-people" value={name}
                 disabled={busy} placeholder="Name this face…"
                 onChange={(e) => setName(e.target.value)}
                 onKeyDown={(e) => {
                   if (e.key !== "Enter") return;
                   e.preventDefault();
                   if (typedIsBoxed) onMove(pid, name);
                   else if (pid) onTag(pid);
                   else if (canCreate) onCreateAndTag(name.trim(), isFamily);
                 }} />
          <datalist id="fi-people">
            {allPeople.map((p) => <option key={p.id} value={p.name} />)}
          </datalist>
          {typedIsBoxed ? (
            <button className="primary" disabled={busy} onClick={() => onMove(pid, name)}
                    title="They already have a box here — move it onto this face">
              ⤿ Move their box here
            </button>
          ) : canCreate ? (
            <>
              <label className="fq-fam">
                <input type="checkbox" checked={isFamily} disabled={busy}
                       onChange={(e) => setIsFamily(e.target.checked)} /> family
              </label>
              <button className="primary" disabled={busy}
                      onClick={() => onCreateAndTag(name.trim(), isFamily)}>
                ＋ Create &ldquo;{name.trim()}&rdquo; &amp; tag
              </button>
            </>
          ) : (
            <button className="primary" disabled={busy || !pid} onClick={() => onTag(pid)}>
              Tag as this person
            </button>
          )}
          {dupOf ? (
            <button disabled={busy} onClick={() => onIgnore(true)}
                    title="Already tagged on this photo — dismiss without teaching the matcher to suppress them">
              Dismiss 2nd face of {dupOf.name} <kbd>I</kbd>
            </button>
          ) : (
            <button disabled={busy} onClick={() => onIgnore(false)}>Ignore <kbd>I</kbd></button>
          )}
          <span className="spacer" />
          <span className="muted fq-bulk">
            <kbd>←</kbd><kbd>→</kbd> move · <kbd>N</kbd> name · <kbd>I</kbd> ignore · <kbd>Esc</kbd> close
          </span>
        </div>
      </div>
    </div>
  );
}
