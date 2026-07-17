import { useEffect, useMemo, useState } from "react";
import { api } from "../api";

function slugify(name) {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
}

function nameOf(persons, id) {
  return persons.find((p) => p.id === id)?.name ?? "—";
}

export default function PeopleAdmin({ onClose, onChanged }) {
  const [persons, setPersons] = useState([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [q, setQ] = useState("");
  const [editingLinks, setEditingLinks] = useState(null); // person id being link-edited

  // link editor state (populated when editingLinks is set)
  const [lFather, setLFather] = useState("");
  const [lMother, setLMother] = useState("");
  const [lSpouse, setLSpouse] = useState("");

  // new-person form
  const [newName, setNewName] = useState("");
  const [newId, setNewId] = useState("");
  const [idTouched, setIdTouched] = useState(false);
  const [newFather, setNewFather] = useState("");
  const [newMother, setNewMother] = useState("");
  const [newSpouse, setNewSpouse] = useState("");
  const [newNonFamily, setNewNonFamily] = useState(false);

  async function load() {
    const p = await api.people(false);
    setPersons(p.sort((a, b) => a.name.localeCompare(b.name)));
  }
  useEffect(() => { load(); }, []);

  const list = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return persons.filter((p) => !needle || p.name.toLowerCase().includes(needle));
  }, [persons, q]);

  function handleNameChange(v) {
    setNewName(v);
    if (!idTouched) setNewId(slugify(v));
  }

  function startEditLinks(p) {
    setEditingLinks(p.id);
    setLFather(p.father_id ?? "");
    setLMother(p.mother_id ?? "");
    setLSpouse(p.spouse_id ?? "");
    setMsg(null);
  }

  function cancelEditLinks() {
    setEditingLinks(null);
  }

  async function run(label, fn) {
    setBusy(true); setMsg(null);
    try {
      await fn();
      await load();
      await onChanged();
      setMsg(`✓ ${label}`);
      return true;
    } catch (e) {
      setMsg(`⚠ ${e.message}`);
      return false;
    } finally {
      setBusy(false);
    }
  }

  const rename = (p) => {
    const name = window.prompt(`Rename "${p.name}" to:`, p.name);
    if (name && name.trim() && name.trim() !== p.name)
      run(`renamed to ${name.trim()}`, () => api.renamePerson(p.id, name.trim()));
  };

  const toggleFamily = (p) =>
    run(`${p.name} is now ${p.is_family ? "non-family" : "family"}`,
        () => api.renamePerson(p.id, p.name, !p.is_family));

  const saveLinks = async (p) => {
    const ok = await run(`updated ${p.name}'s family links`, () =>
      api.updatePersonLinks(p.id, lFather || null, lMother || null, lSpouse || null)
    );
    if (ok) setEditingLinks(null);
  };

  const create = async () => {
    const name = newName.trim();
    const id = newId.trim();
    if (!name || !id) return;
    const ok = await run(`added ${name}`, () =>
      api.createPerson({
        id,
        canonical_name: name,
        father_id: newNonFamily ? null : (newFather || null),
        mother_id: newNonFamily ? null : (newMother || null),
        spouse_id: newNonFamily ? null : (newSpouse || null),
        is_family: !newNonFamily,
      })
    );
    if (ok) {
      setNewName(""); setNewId(""); setIdTouched(false);
      setNewFather(""); setNewMother(""); setNewSpouse(""); setNewNonFamily(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal modal-wide" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Manage people <span className="modal-sub">{persons.length}</span></h2>
          <button className="lb-close" onClick={onClose}>✕</button>
        </div>

        <div className="modal-search">
          <input autoFocus value={q} placeholder="Search people…"
                 onChange={(e) => setQ(e.target.value)} />
        </div>

        <div className="event-admin-list">
          {list.map((p) => (
            <div key={p.id}>
              <div className="event-admin-row">
                <span className="event-admin-name">
                  {p.name}
                  {!p.is_family && <span className="lb-origin-badge" style={{ marginLeft: 6 }}>friend / other</span>}
                  <span className="modal-sub"> · {p.photo_count} photo{p.photo_count !== 1 ? "s" : ""}</span>
                </span>
                <span className="event-admin-actions">
                  <button className="link" onClick={() => rename(p)} disabled={busy}>rename</button>
                  <button className="link" onClick={() => toggleFamily(p)} disabled={busy}
                          title={p.is_family ? "Move to Friends & others" : "Move back into the family tree"}>
                    {p.is_family ? "mark friend" : "mark family"}
                  </button>
                  {p.is_family && (
                    <button className="link" onClick={() => editingLinks === p.id ? cancelEditLinks() : startEditLinks(p)}
                            disabled={busy}>
                      {editingLinks === p.id ? "cancel" : "edit links"}
                    </button>
                  )}
                </span>
              </div>
              {editingLinks === p.id && (
                <div className="people-link-editor">
                  <div className="people-admin-row">
                    <label>Father</label>
                    <select value={lFather} onChange={(e) => setLFather(e.target.value)} disabled={busy}>
                      <option value="">— none —</option>
                      {persons.filter((x) => x.id !== p.id).map((x) =>
                        <option key={x.id} value={x.id}>{x.name}</option>)}
                    </select>
                    <label>Mother</label>
                    <select value={lMother} onChange={(e) => setLMother(e.target.value)} disabled={busy}>
                      <option value="">— none —</option>
                      {persons.filter((x) => x.id !== p.id).map((x) =>
                        <option key={x.id} value={x.id}>{x.name}</option>)}
                    </select>
                    <label>Spouse</label>
                    <select value={lSpouse} onChange={(e) => setLSpouse(e.target.value)} disabled={busy}>
                      <option value="">— none —</option>
                      {persons.filter((x) => x.id !== p.id).map((x) =>
                        <option key={x.id} value={x.id}>{x.name}</option>)}
                    </select>
                    <button className="ghost" onClick={() => saveLinks(p)} disabled={busy}>Save</button>
                  </div>
                </div>
              )}
            </div>
          ))}
          {list.length === 0 && <div className="modal-msg">No people match "{q}".</div>}
        </div>

        <div className="people-admin-new">
          <div className="people-admin-row">
            <input value={newName} placeholder="Full name…"
                   onChange={(e) => handleNameChange(e.target.value)}
                   onKeyDown={(e) => e.key === "Enter" && create()} />
            <input value={newId} placeholder="id (auto)" style={{ maxWidth: 180 }}
                   onChange={(e) => { setNewId(e.target.value); setIdTouched(true); }}
                   title="Unique slug identifier (auto-generated from name)" />
          </div>
          <div className="people-admin-row">
            <select value={newFather} onChange={(e) => setNewFather(e.target.value)} disabled={busy || newNonFamily}>
              <option value="">— father —</option>
              {persons.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            <select value={newMother} onChange={(e) => setNewMother(e.target.value)} disabled={busy || newNonFamily}>
              <option value="">— mother —</option>
              {persons.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            <select value={newSpouse} onChange={(e) => setNewSpouse(e.target.value)} disabled={busy || newNonFamily}>
              <option value="">— spouse —</option>
              {persons.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </div>
          <div className="people-admin-row">
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, opacity: 0.85 }}>
              <input type="checkbox" checked={newNonFamily}
                     onChange={(e) => setNewNonFamily(e.target.checked)} disabled={busy} />
              Non-family (friend / other) — no family-tree links
            </label>
          </div>
          <div className="people-admin-row" style={{ justifyContent: "flex-end" }}>
            <button className="ghost" onClick={create}
                    disabled={busy || !newName.trim() || !newId.trim()}>Add person</button>
          </div>
        </div>

        {msg && <div className="modal-msg">{msg}</div>}
      </div>
    </div>
  );
}
