export default function Header({ view, total, hasFilters, onView, onReset, mapOpen, onToggleMap,
                                admin, onToggleAdmin, onManageEvents, onManagePlaces,
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
        {admin && (
          <button className="ghost" onClick={onManageEvents} title="Rename / merge / delete events">
            Manage events
          </button>
        )}
        {admin && (
          <button className="ghost" onClick={onManagePlaces} title="Rename / merge / delete places">
            Manage places
          </button>
        )}
        {admin && (
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
        <button className={`ghost ${admin ? "on" : ""}`} onClick={onToggleAdmin}
                title="Toggle admin editing">
          ⚙ Admin
        </button>
      </div>
    </header>
  );
}
