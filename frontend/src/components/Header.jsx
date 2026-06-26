export default function Header({ view, total, hasFilters, onView, onReset, mapOpen, onToggleMap,
                                admin, canEdit, isAdmin, onToggleAdmin,
                                onManageEvents, onManagePlaces, onManageUsers,
                                undoInfo, onUndo }) {
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
          <button className={view === "gallery" ? "on" : ""} onClick={() => onView("gallery")}>
            Gallery
          </button>
          <button className={view === "rolls" ? "on" : ""} onClick={() => onView("rolls")}>
            Rolls
          </button>
        </div>
        <span className="count">{total.toLocaleString()} photos</span>
      </div>

      <div className="header-right">
        {/* Vocabulary management + undo are admin-only and cascade across many
            photos (SPEC §10.5); contributors only get per-photo/bulk tagging. */}
        {admin && isAdmin && (
          <button className="ghost" onClick={onManageEvents} title="Rename / merge / delete events">
            Manage events
          </button>
        )}
        {admin && isAdmin && (
          <button className="ghost" onClick={onManagePlaces} title="Rename / merge / delete places">
            Manage places
          </button>
        )}
        {admin && isAdmin && (
          <button className="ghost" onClick={onManageUsers} title="Invite / manage users">
            Manage users
          </button>
        )}
        {admin && isAdmin && (
          <button className="ghost" onClick={onUndo} disabled={!undoInfo?.available} title={undoLabel}>
            ↶ Undo
          </button>
        )}
        <button className={`ghost ${mapOpen ? "on" : ""}`} onClick={onToggleMap} title="Toggle map">
          🗺 Map
        </button>
        {hasFilters && (
          <button className="ghost" onClick={onReset}>Reset</button>
        )}
        {canEdit && (
          <button className={`ghost ${admin ? "on" : ""}`} onClick={onToggleAdmin}
                  title={isAdmin ? "Toggle admin editing" : "Toggle tagging mode"}>
            ⚙ {isAdmin ? "Admin" : "Edit"}
          </button>
        )}
      </div>
    </header>
  );
}
