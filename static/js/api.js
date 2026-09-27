async function request(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      Accept: "application/json",
      "X-Requested-With": "SpotyOFO",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
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
  search: (q) => request(`/api/search?q=${encodeURIComponent(q)}`),
  auth: () => request("/api/auth"),
  authSetup: (pin, confirm) => request("/api/auth/setup", { method: "POST", body: JSON.stringify({ pin, confirm }) }),
  authLogin: (pin) => request("/api/auth/login", { method: "POST", body: JSON.stringify({ pin }) }),
  authChange: (current, pin, confirm) => request("/api/auth/change", { method: "POST", body: JSON.stringify({ current, pin, confirm }) }),
  authLogout: () => request("/api/auth/logout", { method: "POST" }),
  equipo: (path = "") => request(`/api/equipo?path=${encodeURIComponent(path)}`),
  equipoLike: (path, shownLiked = false) => request("/api/equipo/likes", { method: "POST", body: JSON.stringify({ path, shown_liked: Boolean(shownLiked) }) }),
  equipoPlaylist: (path, playlistId) => request("/api/equipo/playlists", { method: "POST", body: JSON.stringify({ path, playlist_id: Number(playlistId) }) }),
  removeDeviceTrack: (id, path) => request(`/api/playlists/${id}/device`, { method: "DELETE", body: JSON.stringify({ path }) }),
  uploads: () => request("/api/uploads"),
  upload: uploadFile,
  deleteTrack: (id) => request(`/api/tracks/${id}`, { method: "DELETE" }),
};
