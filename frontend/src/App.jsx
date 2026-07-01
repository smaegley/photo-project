import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api";
import Header from "./components/Header";
import DateSlider from "./components/DateSlider";
import FilterRail from "./components/FilterRail";
import Gallery from "./components/Gallery";
import Lightbox from "./components/Lightbox";
import MapBand from "./components/MapBand";
import RollsView from "./components/RollsView";
import AdminBar from "./components/AdminBar";
import EventsAdmin from "./components/EventsAdmin";
import PlacesAdmin from "./components/PlacesAdmin";
import UsersAdmin from "./components/UsersAdmin";
import UsageAdmin from "./components/UsageAdmin";

export const YEAR_MIN = 1962;
export const YEAR_MAX = 1976;

const EMPTY = { people: [], events: [], places: [], bbox: null, magazineId: null };

export default function App() {
  const [years, setYears] = useState([YEAR_MIN, YEAR_MAX]);
  const [sel, setSel] = useState(EMPTY); // people/events/places/bbox selections
  const [view, setView] = useState("gallery");

  // reference data (loaded once)
  const [people, setPeople] = useState([]);
  const [events, setEvents] = useState([]);       // photos-only, for the filter rail
  const [allEvents, setAllEvents] = useState([]); // incl. empty events, for admin
  const [places, setPlaces] = useState([]);       // mappable-only, for the map
  const [allPlaces, setAllPlaces] = useState([]); // all places, for admin/assign
  const [magazines, setMagazines] = useState([]);

  // gallery result
  const [result, setResult] = useState({ total: 0, photos: [], people_counts: [], event_counts: [] });
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);

  const [lightboxIdx, setLightboxIdx] = useState(null);
  const [mapOpen, setMapOpen] = useState(false);
  const [mapPinned, setMapPinned] = useState(false);

  // current viewer role (SPEC §6.3, §10.5) — gates the editing UI
  const [role, setRole] = useState("viewer");
  const canEdit = role === "admin" || role === "contributor";
  const isAdmin = role === "admin";

  // theme (per-user setting, SPEC §7): raw pref light|dark|system + resolved bool
  const [theme, setTheme] = useState("system");
  const [dark, setDark] = useState(false);

  // admin / bulk-editing (SPEC §3.5)
  const [admin, setAdmin] = useState(false);
  const [showEvents, setShowEvents] = useState(false);
  const [showPlaces, setShowPlaces] = useState(false);
  const [showUsers, setShowUsers] = useState(false);
  const [showUsage, setShowUsage] = useState(false);
  const [selectedIds, setSelectedIds] = useState(() => new Set());
  const [undoInfo, setUndoInfo] = useState({ available: false });

  const filters = useMemo(() => {
    const full = years[0] === YEAR_MIN && years[1] === YEAR_MAX;
    return {
      ...sel,
      dateStart: full ? null : `${years[0]}-01-01`,
      dateEnd: full ? null : `${years[1]}-12-31`,
    };
  }, [sel, years]);

  const hasFilters =
    sel.people.length || sel.events.length || sel.places.length || sel.bbox || sel.magazineId ||
    years[0] !== YEAR_MIN || years[1] !== YEAR_MAX;

  const activeRoll = sel.magazineId ? magazines.find((m) => m.id === sel.magazineId) : null;

  useEffect(() => {
    api.me().then((me) => { setRole(me.role); setTheme(me.theme || "system"); })
      .catch(() => setRole("viewer"));
    api.people().then(setPeople);
    api.events().then(setEvents);
    api.events(false).then(setAllEvents);
    api.places(true).then(setPlaces);
    api.places(false).then(setAllPlaces);
    api.magazines().then(setMagazines);
  }, []);

  // keep the undo button in sync when admin mode turns on (undo is admin-only)
  useEffect(() => {
    if (admin && isAdmin) api.undoPeek().then(setUndoInfo);
  }, [admin, isAdmin]);

  // apply theme to <html data-theme>; resolve "system" via the OS preference
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => {
      const d = theme === "system" ? mq.matches : theme === "dark";
      setDark(d);
      document.documentElement.dataset.theme = d ? "dark" : "light";
    };
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, [theme]);

  const toggleTheme = useCallback(() => {
    const next = dark ? "light" : "dark";
    setTheme(next);
    api.updateMe({ theme: next }).catch(() => {});
  }, [dark]);

  // usage beacon: log a "search" once filters settle (debounced; SPEC hybrid tracking)
  useEffect(() => {
    if (!hasFilters) return;
    const t = setTimeout(() => {
      const parts = [];
      if (sel.people.length) parts.push(`people:${sel.people.length}`);
      if (sel.events.length) parts.push(`events:${sel.events.length}`);
      if (sel.places.length) parts.push(`places:${sel.places.length}`);
      if (sel.bbox) parts.push("map");
      if (sel.magazineId) parts.push(`roll:${sel.magazineId}`);
      if (years[0] !== YEAR_MIN || years[1] !== YEAR_MAX) parts.push(`${years[0]}-${years[1]}`);
      api.logUsage("search", parts.join(" "));
    }, 1500);
    return () => clearTimeout(t);
  }, [filters, hasFilters]); // eslint-disable-line

  // refetch page 1 whenever filters change
  useEffect(() => {
    let alive = true;
    setLoading(true);
    setPage(1);
    setSelectedIds(new Set()); // selection is tied to the current filter view
    api.photos(filters, 1).then((r) => {
      if (alive) { setResult(r); setLoading(false); }
    });
    return () => { alive = false; };
  }, [filters]);

  // after an admin edit: reload reference lists (counts change) and re-fetch the
  // pages currently loaded, so the gallery keeps its scroll extent instead of
  // snapping back to page 1 — only the edited photos change.
  const refresh = useCallback(async () => {
    const [ev, aev, pl, apl, pe] = await Promise.all([
      api.events(), api.events(false), api.places(true), api.places(false),
      api.people()]);
    setEvents(ev); setAllEvents(aev); setPlaces(pl); setAllPlaces(apl);
    setPeople(pe);
    if (isAdmin) api.undoPeek().then(setUndoInfo);
    const pages = await Promise.all(
      Array.from({ length: page }, (_, i) => api.photos(filters, i + 1))
    );
    setResult({ ...pages[0], photos: pages.flatMap((r) => r.photos) });
    setSelectedIds(new Set());
  }, [filters, page, isAdmin]);

  const onUndo = useCallback(async () => {
    await api.undo();
    await refresh();
  }, [refresh]);

  const selectAll = useCallback(async () => {
    const ids = await api.photoIds(filters);
    setSelectedIds(new Set(ids));
  }, [filters]);

  const toggleSelect = (id) =>
    setSelectedIds((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });

  const loadMore = useCallback(() => {
    if (loading || result.photos.length >= result.total) return;
    const next = page + 1;
    setLoading(true);
    api.photos(filters, next).then((r) => {
      setResult((prev) => ({ ...r, photos: [...prev.photos, ...r.photos] }));
      setPage(next);
      setLoading(false);
    });
  }, [filters, page, loading, result.photos.length, result.total]);

  const toggle = (key, id) =>
    setSel((s) => ({
      ...s,
      [key]: s[key].includes(id) ? s[key].filter((x) => x !== id) : [...s[key], id],
    }));

  const reset = () => { setSel(EMPTY); setYears([YEAR_MIN, YEAR_MAX]); };
  const setBbox = (bbox) => setSel((s) => ({ ...s, bbox }));

  return (
    <div className="app">
      <Header
        view={view}
        total={result.total}
        hasFilters={hasFilters}
        onView={setView}
        onReset={reset}
        mapOpen={mapOpen}
        onToggleMap={() => setMapOpen((o) => !o)}
        admin={admin}
        canEdit={canEdit}
        isAdmin={isAdmin}
        onToggleAdmin={() => { setAdmin((a) => !a); setSelectedIds(new Set()); }}
        onManageEvents={() => setShowEvents(true)}
        onManagePlaces={() => setShowPlaces(true)}
        onManageUsers={() => setShowUsers(true)}
        onShowUsage={() => setShowUsage(true)}
        undoInfo={undoInfo}
        onUndo={onUndo}
        dark={dark}
        onToggleTheme={toggleTheme}
      />

      <DateSlider years={years} onChange={setYears} />

      {mapOpen && (
        <MapBand
          places={places}
          selectedPlaces={sel.places}
          bbox={sel.bbox}
          pinned={mapPinned}
          dark={dark}
          onTogglePin={() => setMapPinned((p) => !p)}
          onClose={() => setMapOpen(false)}
          onSelectPlace={(id) => toggle("places", id)}
          onBbox={setBbox}
        />
      )}

      <div className={`body ${view === "gallery" ? "" : "no-rail"}`}>
        {/* The filter rail only applies to the gallery — hide it in Rolls view */}
        {view === "gallery" && (
          <FilterRail
            people={people}
            events={events}
            places={allPlaces}
            peopleCounts={result.people_counts}
            eventCounts={result.event_counts}
            sel={sel}
            onToggle={toggle}
            onReset={reset}
            hasFilters={hasFilters}
          />
        )}
        <main className="gallery-pane">
          {view === "gallery" ? (
            <>
              {activeRoll && (
                <div className="roll-banner">
                  <span>📼 <b>Roll {activeRoll.id}</b> — {activeRoll.title}</span>
                  <button onClick={() => setSel((s) => ({ ...s, magazineId: null }))}>✕ Clear roll</button>
                </div>
              )}
              {admin && (
                <AdminBar
                  selectedIds={[...selectedIds]}
                  total={result.total}
                  filterEventId={sel.events.length === 1 ? sel.events[0] : null}
                  events={allEvents}
                  people={people}
                  places={allPlaces}
                  onSelectAll={selectAll}
                  onClear={() => setSelectedIds(new Set())}
                  onApplied={refresh}
                />
              )}
              <Gallery
                photos={result.photos}
                total={result.total}
                loading={loading}
                onOpen={(i) => { setLightboxIdx(i); const p = result.photos[i]; if (p) api.logUsage("view", p.source_file); }}
                onLoadMore={loadMore}
                selectable={admin}
                selectedIds={selectedIds}
                onToggleSelect={toggleSelect}
              />
            </>
          ) : (
            <RollsView
              magazines={magazines}
              onViewInGallery={(id) => { setSel({ ...EMPTY, magazineId: id }); setView("gallery"); }}
            />
          )}
        </main>
      </div>

      {lightboxIdx !== null && (
        <Lightbox
          photos={result.photos}
          index={lightboxIdx}
          onClose={() => setLightboxIdx(null)}
          onNav={(i) => setLightboxIdx(i)}
          onLoadMore={loadMore}
          admin={admin}
          isAdmin={isAdmin}
          events={allEvents}
          people={people}
          places={allPlaces}
          magazines={magazines}
          onChanged={refresh}
        />
      )}

      {showEvents && (
        <EventsAdmin
          events={allEvents}
          onClose={() => setShowEvents(false)}
          onChanged={refresh}
        />
      )}

      {showPlaces && (
        <PlacesAdmin
          places={allPlaces}
          onClose={() => setShowPlaces(false)}
          onChanged={refresh}
        />
      )}

      {showUsers && (
        <UsersAdmin onClose={() => setShowUsers(false)} />
      )}

      {showUsage && (
        <UsageAdmin onClose={() => setShowUsage(false)} />
      )}
    </div>
  );
}
