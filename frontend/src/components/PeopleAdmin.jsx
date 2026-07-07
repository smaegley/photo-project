import { useEffect, useMemo, useState } from "react";
import { api } from "../api";

function slugify(name) {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
}

export default function PeopleAdmin({ onClose, onChanged }) {
  const [persons, setPersons] = useState([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [q, setQ] = useState("");

  // new-person form
  const [newName, setNewName] = useState("");
  const [newId, setNewId] = useState("");
  const [idTouched, setIdTouched] = useState(false);
  const [newFather, setNewFather] = useState("");
  const [newMother, setNewMother] = useState("");
  const [newSpouse, setNewSpouse] = useState("");

  async function load() {
    const p = await api.people(false);
    setPersons(p.sort((a, b) => a.name.localeCompare(b.name)));
  }
  useEffect(() => { load(); }, []);

  const list = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return persons.filter((p) => !needle || p.name.toLowerCase().includes(needle));
  }, [persons, q]);

  // keep slug in sync with name unless user has manually edited it
  function handleNameChange(v) {
    setNewName(v);
    if (!idTouched) setNewId(slugify(v));
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

  const create = async () => {
    const name = newName.trim();
    const id = newId.trim();
    if (!name || !id) return;
    const ok = await run(`added ${name}`, () =>
      api.createPerson({
        id,
        canonical_name: name,
        father_id: newFather || null,
        mother_id: newMother || null,
        spouse_id: newSpouse || null,
      })
    );
    if (ok) {
      setNewName(""); setNewId(""); setIdTouched(false);
      setNewFather(""); setNewMother(""); setNewSpouse("");
    }
  };

  const personOptions = persons;

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
            <div className="event-admin-row" key={p.id}>
              <span className="event-admin-name">
                {p.name}
                <span className="modal-sub"> · {p.photo_count} photo{p.photo_count !== 1 ? "s" : ""}</span>
              </span>
              <span className="event-admin-actions">
                <button className="link" onClick={() => rename(p)} disabled={busy}>rename</button>
              </span>
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
            <select value={newFather} onChange={(e) => setNewFather(e.target.value)} disabled={busy}>
              <option value="">— father —</option>
              {personOptions.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            <select value={newMother} onChange={(e) => setNewMother(e.target.value)} disabled={busy}>
              <option value="">— mother —</option>
              {personOptions.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            <select value={newSpouse} onChange={(e) => setNewSpouse(e.target.value)} disabled={busy}>
              <option value="">— spouse —</option>
              {personOptions.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
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
