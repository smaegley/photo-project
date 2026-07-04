// Thin API client. Same-origin in dev via Vite proxy; same-origin in prod via Caddy.

function qs(filters, extra = {}) {
  const p = new URLSearchParams();
  if (filters.dateStart) p.set("date_start", filters.dateStart);
  if (filters.dateEnd) p.set("date_end", filters.dateEnd);
  (filters.people || []).forEach((id) => p.append("people", id));
  (filters.events || []).forEach((id) => p.append("events", id));
  (filters.places || []).forEach((id) => p.append("places", id));
  if (filters.bbox) p.set("bbox", filters.bbox.join(","));
  if (filters.magazineId) p.set("magazine_id", filters.magazineId);
  Object.entries(extra).forEach(([k, v]) => p.set(k, v));
  return p.toString();
}

async function get(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return r.json();
}

async function send(method, url, body) {
  const r = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) {
    let detail = `${r.status}`;
    try { detail = (await r.json()).detail || detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  return r.json();
}

export const api = {
  photos: (filters, page = 1, pageSize = 80) =>
    get(`/api/photos?${qs(filters, { page, page_size: pageSize })}`),
  photoIds: (filters) => get(`/api/photos/ids?${qs(filters)}`),
  photo: (id) => get(`/api/photos/${id}`),
  people: (withPhotosOnly = true) =>
    get(`/api/people${withPhotosOnly ? "" : "?with_photos_only=false"}`),
  events: (withPhotosOnly = true) =>
    get(`/api/events${withPhotosOnly ? "" : "?with_photos_only=false"}`),
  places: (mappableOnly = false) =>
    get(`/api/places${mappableOnly ? "?mappable_only=true" : ""}`),
  magazines: () => get("/api/magazines"),
  me: () => get("/api/me"),
  updateMe: (body) => send("PATCH", "/api/me", body),
  logUsage: (event_type, target) =>
    // fire-and-forget beacon; never let tracking break the UI
    fetch("/api/usage", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ event_type, target }) }).catch(() => {}),

  // ---- admin (SPEC §3.5) ----
  createEvent: (name) => send("POST", "/api/admin/events", { name }),
  renameEvent: (id, name) => send("PATCH", `/api/admin/events/${id}`, { name }),
  mergeEvent: (id, intoId) => send("POST", `/api/admin/events/${id}/merge`, { into_id: intoId }),
  deleteEvent: (id) => send("DELETE", `/api/admin/events/${id}`),
  bulkEvent: (photoIds, eventId, op) =>
    send("POST", "/api/admin/photos/event", { photo_ids: photoIds, event_id: eventId, op }),
  bulkPerson: (photoIds, personId, op) =>
    send("POST", "/api/admin/photos/person", { photo_ids: photoIds, person_id: personId, op }),
  bulkPlace: (photoIds, placeId) =>
    send("POST", "/api/admin/photos/place", { photo_ids: photoIds, place_id: placeId }),
  createPlace: (body) => send("POST", "/api/admin/places", body),
  updatePlace: (id, body) => send("PATCH", `/api/admin/places/${id}`, body),
  mergePlace: (id, intoId) => send("POST", `/api/admin/places/${id}/merge`, { into_id: intoId }),
  deletePlace: (id) => send("DELETE", `/api/admin/places/${id}`),
  rotatePhoto: (id, degrees) => send("POST", `/api/admin/photos/${id}/rotate`, { degrees }),
  editCaption: (id, caption) => send("POST", `/api/admin/photos/${id}/caption`, { caption }),
  setRepresentative: (personId, photoId) =>
    send("POST", `/api/admin/people/${personId}/representative`, { photo_id: photoId }),
  setFaceRegion: (personId, body) =>
    send("POST", `/api/admin/people/${personId}/face-region`, body),
  usageStats: () => get("/api/admin/usage/stats"),
  geocode: (q) => get(`/api/admin/geocode?q=${encodeURIComponent(q)}`),
  undo: () => send("POST", "/api/admin/undo"),
  undoPeek: () => get("/api/admin/undo/peek"),

  // ---- dev-only user switcher ----
  devUsers: () => get("/api/dev/users"),

  // ---- users / invites (SPEC §6.3, §10.5; admin-only) ----
  users: () => get("/api/admin/users"),
  createUser: (body) => send("POST", "/api/admin/users", body),
  updateUser: (id, body) => send("PATCH", `/api/admin/users/${id}`, body),
  deleteUser: (id) => send("DELETE", `/api/admin/users/${id}`),
};
