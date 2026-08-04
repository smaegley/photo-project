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
  if (filters.origin) p.set("origin", filters.origin);
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
  photosMeta: () => get("/api/photos/meta"),
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
  // Photos with GPS but no named place, clustered server-side (SPEC §3.4 / §13.9).
  unresolvedLocations: () => get("/api/admin/unresolved-locations"),
  // radius defaults to the cluster radius so a claim matches the pin you named;
  // widen it only if you want the importer's 25km proximity behaviour.
  claimNearby: (id, radiusKm = 15) =>
    send("POST", `/api/admin/places/${id}/claim-nearby?radius_km=${radiusKm}`, {}),

  // ---- face matching queue (SPEC §14.7) ----
  faceQueue: () => get("/api/admin/face-queue"),
  faceQueuePerson: (personId, limit = 300) =>
    get(`/api/admin/face-queue/${encodeURIComponent(personId)}?limit=${limit}`),
  acceptedFaces: (maxScore = 1.0, limit = 400) =>
    get(`/api/admin/face-queue/accepted?max_score=${maxScore}&limit=${limit}`),
  acceptedSummary: () => get("/api/admin/face-queue/accepted/summary"),
  faceTags: ({ personId = null, maxScore = null, maxArea = null,
               sort = "area", limit = 300, offset = 0 } = {}) =>
    get("/api/admin/face-tags?" + new URLSearchParams(Object.entries({
      person_id: personId, max_score: maxScore, max_area: maxArea, sort, limit, offset,
    }).filter(([, v]) => v !== null && v !== "")).toString()),
  peopleWithoutFace: (limit = 200) =>
    get(`/api/admin/people/without-face?limit=${limit}`),
  setRepresentatives: (picks) =>
    send("POST", "/api/admin/people/set-representatives", { picks }),
  faceTagsByPerson: ({ maxScore = null, maxArea = null } = {}) =>
    get("/api/admin/face-tags/by-person?" + new URLSearchParams(Object.entries({
      max_score: maxScore, max_area: maxArea,
    }).filter(([, v]) => v !== null && v !== "")).toString()),
  removeTinyTags: (pairs) =>
    send("POST", "/api/admin/face-queue/tiny-tags/remove", { pairs }),
  decideFaces: (suggestionIds, action) =>
    send("POST", "/api/admin/face-suggestions/decide", { suggestion_ids: suggestionIds, action }),
  faceClusters: (minFaces = 2, limit = 200) =>
    get(`/api/admin/face-clusters?min_faces=${minFaces}&limit=${limit}`),
  faceClusterSummary: () => get("/api/admin/face-clusters/summary"),
  decideClusters: (body) => send("POST", "/api/admin/face-clusters/decide", body),
  clusterFaces: (id, limit = 400) => get(`/api/admin/face-clusters/${id}/faces?limit=${limit}`),
  assignFaces: (body) => send("POST", "/api/admin/faces/assign", body),
  facesUnnamed: ({ minArea = 0.004, minScore = 0.7, includeIgnored = false,
                   limit = 300, offset = 0 } = {}) =>
    get(`/api/admin/faces/unnamed?min_area=${minArea}&min_score=${minScore}`
        + `&include_ignored=${includeIgnored}&limit=${limit}&offset=${offset}`),
  rotatePhoto: (id, degrees) => send("POST", `/api/admin/photos/${id}/rotate`, { degrees }),
  editCaption: (id, caption) => send("POST", `/api/admin/photos/${id}/caption`, { caption }),
  editNotes:   (id, notes)   => send("POST", `/api/admin/photos/${id}/notes`,   { notes }),
  createPerson: (body) => send("POST", "/api/admin/people", body),
  deletePerson: (id) => send("DELETE", `/api/admin/people/${encodeURIComponent(id)}`),
  mergePerson: (id, intoId) =>
    send("POST", `/api/admin/people/${encodeURIComponent(id)}/merge`, { into_id: intoId }),
  renamePerson: (id, canonical_name, is_family) =>
    send("PATCH", `/api/admin/people/${id}`,
      is_family === undefined ? { canonical_name } : { canonical_name, is_family }),
  updatePersonLinks: (id, father_id, mother_id, spouse_id) =>
    send("PATCH", `/api/admin/people/${id}/links`, { father_id, mother_id, spouse_id }),
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
