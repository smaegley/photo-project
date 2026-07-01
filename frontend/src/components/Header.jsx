import { useState } from "react";

// Small dropdown: a ghost button that reveals a panel; a full-screen backdrop
// closes it on outside-click, and clicking any item closes it too.
function Menu({ label, title, children }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="menu">
      <button className={`ghost ${open ? "on" : ""}`} title={title} onClick={() => setOpen((o) => !o)}>
        {label}
      </button>
      {open && (
        <>
          <div className="menu-backdrop" onClick={() => setOpen(false)} />
          <div className="menu-panel" onClick={() => setOpen(false)}>{children}</div>
        </>
      )}
    </div>
  );
}

export default function Header({ view, total, hasFilters, onView, onReset, mapOpen, onToggleMap,
                                admin, canEdit, isAdmin, onToggleAdmin,
                                onManageEvents, onManagePlaces, onManageUsers, onShowUsage,
                                undoInfo, onUndo, dark, onToggleTheme }) {
  const undoLabel = undoInfo?.available
    ? `Undo: ${undoInfo.field} ${undoInfo.old ?? ""}${undoInfo.old && undoInfo.new ? " → " : ""}${undoInfo.new ?? ""}`.trim()
    : "Nothing to undo";
  return (
    <header className="header">
      <div className="brand">
        <span className="brand-mark">◳</span>
        <h1>Maegley Photo Album</h1>
      </div>

      <div className="header-center">
        <div className="seg">
          <button className={view === "gallery" ? "on" : ""} onClick={() => onView("gallery")}>Gallery</button>
          <button className={view === "rolls" ? "on" : ""} onClick={() => onView("rolls")}>Rolls</button>
        </div>
        <span className="count">{total.toLocaleString()} photos</span>
      </div>

      <div className="header-right">
        <button className={`ghost ${mapOpen ? "on" : ""}`} onClick={onToggleMap} title="Toggle map">🗺 Map</button>
        {hasFilters && <button className="ghost" onClick={onReset}>Reset</button>}
        {canEdit && (
          <button className={`ghost ${admin ? "on" : ""}`} onClick={onToggleAdmin}
                  title={isAdmin ? "Toggle admin editing" : "Toggle tagging mode"}>
            ⚙ {isAdmin ? "Admin" : "Edit"}
          </button>
        )}
        {/* Vocabulary/users/usage/undo cascade across many photos (SPEC §10.5) —
            admin-only, grouped in one menu to keep the header uncluttered. */}
        {admin && isAdmin && (
          <Menu label="Manage ▾" title="Vocabulary, users, usage, undo">
            <div className="menu-label">Vocabulary</div>
            <button className="menu-item" onClick={onManageEvents}>Manage events</button>
            <button className="menu-item" onClick={onManagePlaces}>Manage places</button>
            <div className="menu-sep" />
            <div className="menu-label">Admin</div>
            <button className="menu-item" onClick={onManageUsers}>Manage users</button>
            <button className="menu-item" onClick={onShowUsage}>Usage stats</button>
            <button className="menu-item" onClick={onUndo} disabled={!undoInfo?.available} title={undoLabel}>
              ↶ Undo
            </button>
          </Menu>
        )}
        <button className="ghost" onClick={onToggleTheme} title={dark ? "Switch to light mode" : "Switch to dark mode"}>
          {dark ? "☀" : "☾"}
        </button>
      </div>
    </header>
  );
}
