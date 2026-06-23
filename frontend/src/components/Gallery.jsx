import { useEffect, useRef } from "react";

export default function Gallery({ photos, total, loading, onOpen, onLoadMore,
                                 selectable = false, selectedIds, onToggleSelect }) {
  const sentinel = useRef(null);

  useEffect(() => {
    if (!sentinel.current) return;
    const io = new IntersectionObserver(
      (entries) => entries[0].isIntersecting && onLoadMore(),
      { rootMargin: "600px" }
    );
    io.observe(sentinel.current);
    return () => io.disconnect();
  }, [onLoadMore]);

  if (!loading && photos.length === 0) {
    return <div className="empty">No photos match these filters.</div>;
  }

  return (
    <div className="gallery-scroll">
      <div className="grid">
        {photos.map((p, i) => {
          const checked = selectable && selectedIds.has(p.id);
          return (
            <div key={p.id} className={`tile ${selectable ? "selectable" : ""} ${checked ? "selected" : ""}`}>
              <button className="tile-img" onClick={() => onOpen(i)} title={p.caption || ""}>
                <img src={p.thumb_url} alt={p.caption || p.source_file} loading="lazy" />
              </button>
              {selectable && (
                <button className={`tile-check ${checked ? "on" : ""}`}
                        onClick={() => onToggleSelect(p.id)}
                        title={checked ? "Deselect" : "Select"}>
                  {checked ? "✓" : ""}
                </button>
              )}
            </div>
          );
        })}
      </div>
      <div ref={sentinel} className="sentinel">
        {loading ? "Loading…" : photos.length < total ? "Scroll for more" : `${total.toLocaleString()} photos`}
      </div>
    </div>
  );
}
