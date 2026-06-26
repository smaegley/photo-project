import { useEffect, useMemo, useState } from "react";
import { api } from "../api";

const ROLES = ["viewer", "contributor", "admin"];

// Users / invites (SPEC §6.3, §10.5; admin-only). "Invite" = register a Cloudflare
// Access email here with a role and an optional person link (per-viewer People
// rooting). The email must also be added to the Access allowlist in Cloudflare.
export default function UsersAdmin({ onClose }) {
  const [users, setUsers] = useState([]);
  const [persons, setPersons] = useState([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("viewer");
  const [personId, setPersonId] = useState("");

  const personOptions = useMemo(
    () => [...persons].sort((a, b) => a.name.localeCompare(b.name)), [persons]);
  const personName = (id) => persons.find((p) => p.id === id)?.name || "—";

  async function load() {
    const [u, p] = await Promise.all([api.users(), api.people(false)]);
    setUsers(u); setPersons(p);
  }
  useEffect(() => { load(); }, []);

  async function run(label, fn) {
    setBusy(true); setMsg(null);
    try { await fn(); await load(); setMsg(`✓ ${label}`); }
    catch (e) { setMsg(`⚠ ${e.message}`); }
    finally { setBusy(false); }
  }

  const invite = () => {
    const e = email.trim().toLowerCase();
    if (!e) return;
    run(`invited ${e}`, () =>
      api.createUser({ email: e, role, person_id: personId || null }))
      .then(() => { setEmail(""); setRole("viewer"); setPersonId(""); });
  };
  const changeRole = (u, r) => run(`${u.email} → ${r}`, () => api.updateUser(u.id, { role: r }));
  const changePerson = (u, pid) =>
    run(`${u.email} linked to ${pid ? personName(pid) : "no one"}`,
        () => api.updateUser(u.id, { person_id: pid || null }));
  const del = (u) => {
    if (window.confirm(`Remove ${u.email}? They keep Cloudflare access until removed from the Access policy too.`))
      run(`removed ${u.email}`, () => api.deleteUser(u.id));
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Manage users <span className="modal-sub">{users.length}</span></h2>
          <button className="lb-close" onClick={onClose}>✕</button>
        </div>

        <div className="event-admin-new">
          <input value={email} placeholder="Invite email…" type="email"
                 onChange={(e) => setEmail(e.target.value)}
                 onKeyDown={(e) => e.key === "Enter" && invite()} />
          <select value={role} onChange={(e) => setRole(e.target.value)} disabled={busy}>
            {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
          <select value={personId} onChange={(e) => setPersonId(e.target.value)} disabled={busy}>
            <option value="">— link person (optional) —</option>
            {personOptions.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <button className="ghost" onClick={invite} disabled={busy || !email.trim()}>Invite</button>
        </div>

        <div className="event-admin-list">
          {users.map((u) => (
            <div className="event-admin-row" key={u.id}>
              <span className="event-admin-name">
                {u.email}
                {u.last_login && <span className="modal-sub"> · seen {u.last_login.slice(0, 10)}</span>}
              </span>
              <span className="event-admin-actions">
                <select value={u.role} disabled={busy}
                        onChange={(e) => changeRole(u, e.target.value)} title="Role">
                  {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
                <select value={u.person_id || ""} disabled={busy}
                        onChange={(e) => changePerson(u, e.target.value)} title="Linked person (tree root)">
                  <option value="">— no person —</option>
                  {personOptions.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
                <button className="link danger" onClick={() => del(u)} disabled={busy}>remove</button>
              </span>
            </div>
          ))}
          {users.length === 0 && <div className="modal-msg">No users yet.</div>}
        </div>

        {msg && <div className="modal-msg">{msg}</div>}
        <div className="modal-msg" style={{ opacity: 0.7 }}>
          Inviting also requires adding the email to the Cloudflare Access policy. Removing here
          does not revoke Cloudflare access on its own.
        </div>
      </div>
    </div>
  );
}
