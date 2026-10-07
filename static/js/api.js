// Token CSRF de la sesión (lo entrega /api/me). Va en toda petición que cambia datos.
let csrf = "";
let onSessionEnd = null;

export function setCsrf(token) {
  csrf = String(token || "");
}

export function onSessionExpired(handler) {
  onSessionEnd = handler;
}

function sessionEnded(status, data) {
  if (status === 401 && (data?.needs === "login" || data?.needs === "setup") && onSessionEnd) onSessionEnd(data.needs);
  if (status === 403 && data?.needs === "password") location.assign("/account/password");
}

async function request(url, options = {}) {
  const response = await fetch(url, {
    credentials: "same-origin",
    ...options,
    headers: {
      Accept: "application/json",
      "X-Requested-With": "SpotyOFO",
      ...(csrf ? { "X-CSRF-Token": csrf } : {}),
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    sessionEnded(response.status, data);
    const error = new Error(data.error || "No se pudo completar la acción");
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

function uploadFile(file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/uploads");
    xhr.setRequestHeader("Accept", "application/json");
    xhr.setRequestHeader("X-Requested-With", "SpotyOFO");
    if (csrf) xhr.setRequestHeader("X-CSRF-Token", csrf);
    xhr.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable && onProgress) onProgress(event.loaded / event.total);
    });
    xhr.addEventListener("load", () => {
      let data = {};
      try {
        data = JSON.parse(xhr.responseText || "{}");
      } catch {
        data = {};
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(data);
        return;
      }
      sessionEnded(xhr.status, data);
      const error = new Error(data.error || `No se pudo subir (${xhr.status})`);
      error.status = xhr.status;
      error.data = data;
      reject(error);
    });
    xhr.addEventListener("error", () => {
      const error = new Error("Se cortó la conexión con el equipo. Revisa el Wi-Fi o el tamaño del archivo.");
      error.status = 0;
      reject(error);
    });
    xhr.addEventListener("abort", () => {
      const error = new Error("Subida cancelada");
      error.status = 0;
      reject(error);
    });
    const form = new FormData();
    form.append("file", file, file.name);
    xhr.send(form);
  });
}

// Portada de álbum (multipart): sin «Content-Type» propio, el navegador pone el límite del formulario.
async function uploadCover(albumId, file) {
  const form = new FormData();
  form.append("file", file, file.name);
  const response = await fetch(`/api/albums/${albumId}/cover`, {
    method: "POST",
    credentials: "same-origin",
    headers: { Accept: "application/json", "X-Requested-With": "SpotyOFO", ...(csrf ? { "X-CSRF-Token": csrf } : {}) },
    body: form,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    sessionEnded(response.status, data);
    const error = new Error(data.error || "No se pudo cambiar la portada");
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

export const api = {
  home: () => request("/api/home"),
  playlists: () => request("/api/playlists"),
  playlist: (id) => request(`/api/playlists/${id}`),
  createPlaylist: (body) => request("/api/playlists", { method: "POST", body: JSON.stringify(body) }),
  updatePlaylist: (id, body) => request(`/api/playlists/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deletePlaylist: (id) => request(`/api/playlists/${id}`, { method: "DELETE" }),
  addTrack: (id, trackId) => request(`/api/playlists/${id}/tracks`, { method: "POST", body: JSON.stringify({ track_id: trackId }) }),
  removeTrack: (id, trackId) => request(`/api/playlists/${id}/tracks/${trackId}`, { method: "DELETE" }),
  myPlaylists: (params = {}) => request(`/api/playlists/mine?${new URLSearchParams(params)}`),
  addToPlaylist: (id, body) => request(`/api/playlists/${id}/tracks`, { method: "POST", body: JSON.stringify(body) }),
  removeFromPlaylist: (id, keys) => request(`/api/playlists/${id}/tracks/remove`, { method: "POST", body: JSON.stringify({ keys }) }),
  reorderPlaylist: (id, keys) => request(`/api/playlists/${id}/order`, { method: "PUT", body: JSON.stringify({ keys }) }),
  album: (id) => request(`/api/albums/${id}`),
  artist: (id) => request(`/api/artists/${id}`),
  likes: () => request("/api/likes"),
  toggleLike: (id, shownLiked = false) => request(`/api/likes/${id}`, { method: "POST", body: JSON.stringify({ shown_liked: Boolean(shownLiked) }) }),
  play: (trackId) => request("/api/plays", { method: "POST", body: JSON.stringify({ track_id: trackId }) }),
  related: (id) => request(`/api/tracks/${id}/related`),
  genres: () => request("/api/genres"),
  genre: (slug) => request(`/api/genres/${encodeURIComponent(slug)}`),
  search: (q, all = false) => request(`/api/search?q=${encodeURIComponent(q)}${all ? "&all=1" : ""}`),
  me: () => request("/api/me"),
  logout: () => request("/api/auth/logout", { method: "POST" }),
  adminUsers: () => request("/api/admin/users"),
  adminAction: (id, action) => request(`/api/admin/users/${id}/${action}`, { method: "POST" }),
  adminDelete: (id) => request(`/api/admin/users/${id}`, { method: "DELETE" }),
  equipo: (path = "") => request(`/api/equipo?path=${encodeURIComponent(path)}`),
  equipoLike: (path, shownLiked = false) => request("/api/equipo/likes", { method: "POST", body: JSON.stringify({ path, shown_liked: Boolean(shownLiked) }) }),
  equipoPlaylist: (path, playlistId) => request("/api/equipo/playlists", { method: "POST", body: JSON.stringify({ path, playlist_id: Number(playlistId) }) }),
  removeDeviceTrack: (id, path) => request(`/api/playlists/${id}/device`, { method: "DELETE", body: JSON.stringify({ path }) }),
  uploads: () => request("/api/uploads"),
  // Página de subidas en formato liviano (limit 0 = todo lo que falta desde offset).
  uploadsPage: (offset = 0, limit = 0) => request(`/api/uploads?lite=1&offset=${offset}${limit ? `&limit=${limit}` : ""}`),
  upload: uploadFile,
  deleteTrack: (id) => request(`/api/tracks/${id}`, { method: "DELETE" }),
  // Edición de canciones (solo administradores).
  editTrack: (id, changes) => request(`/api/tracks/${id}`, { method: "PATCH", body: JSON.stringify(changes) }),
  editTracks: (ids, changes) => request("/api/tracks/batch", { method: "PATCH", body: JSON.stringify({ ids, changes }) }),
  albumCover: uploadCover,
};
