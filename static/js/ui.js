import { api } from "./api.js";

const GENRES = {
  "Dream pop": ["#12343a", "#8ef0d0"],
  "Electrónica": ["#1a1033", "#ff4f92"],
  Ambient: ["#243036", "#d5e2ea"],
  "Ritmo latino": ["#3a220c", "#ffb15a"],
  Jazz: ["#160e28", "#b7a6ff"],
  Alternativa: ["#241612", "#ff6a4a"],
};

const known = new Map();
const UPLOAD_EXT = [".mp3", ".m4a", ".mp4", ".aac", ".flac", ".ogg", ".oga", ".opus", ".wav"];
const UPLOAD_ACCEPT = `audio/*,${UPLOAD_EXT.join(",")}`;
const TRASH_ICON = `<svg viewBox="0 0 24 24"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>`;

export function createUI(player) {
  const view = document.getElementById("view");
  const side = document.getElementById("side-playlists");
  const searchInput = document.getElementById("search-input");
  const drawer = document.getElementById("drawer");
  const drawerBody = document.getElementById("drawer-body");
  const drawerTitle = document.getElementById("drawer-title");
  const menu = document.getElementById("menu");
  const modal = document.getElementById("modal");
  const toast = document.getElementById("toast");
  let drawerMode = null;
  let toastTimer = 0;
  let searchTimer = 0;
  let seeking = false;
  let modalSubmit = null;
  let menuTrack = null;
  let pendingLibrary = null;
  let menuPlaylistId = null;
  let uploading = false;

  function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[char]));
  }

  function albumSub(item) {
    return item.year ? `${item.artist} · ${item.year}` : item.artist;
  }

  function sizeLabel(bytes) {
    if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  }

  function fmt(ms) {
    const total = Math.max(0, Math.round((ms || 0) / 1000));
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
  }

  function remember(tracks) {
    (tracks || []).forEach((track) => known.set(track.id, track));
  }

  function showToast(message) {
    toast.textContent = message;
    toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      toast.hidden = true;
    }, 2400);
  }

  function parts() {
    const raw = (location.hash.replace(/^#/, "") || "/").split("/").filter(Boolean);
    return raw;
  }

  function go(hash) {
    if (location.hash === hash) route();
    else location.hash = hash;
  }

  async function paintSide() {
    const lists = await api.playlists();
    const mine = lists.filter((item) => !item.editorial);
    const editorial = lists.filter((item) => item.editorial);
    const current = parts();
    const activeId = current[0] === "playlist" ? current[1] : "";
    const likesOn = current[0] === "likes";
    side.innerHTML = `
      <a class="side-item ${likesOn ? "is-active" : ""}" href="#/likes"><span class="dot heart">♡</span>Me gusta</a>
      ${mine.map((item) => sideItem(item, activeId)).join("")}
      ${editorial.length ? `<p class="side-label">Editorial</p>` : ""}
      ${editorial.map((item) => sideItem(item, activeId)).join("")}
    `;
  }

  function sideItem(item, activeId) {
    return `<a class="side-item ${String(item.id) === activeId ? "is-active" : ""}" href="#/playlist/${item.id}">
      <img src="${esc(item.cover_url)}" alt="">
      <span>${esc(item.name)}</span>
    </a>`;
  }

  function markNav() {
    const name = parts()[0] || "home";
    const key = name === "likes" || name === "playlist" ? "library" : name;
    document.querySelectorAll(".nav-link").forEach((link) => {
      link.classList.toggle("is-active", link.dataset.nav === (key || "home"));
    });
  }

  async function show(builder) {
    view.innerHTML = `<p class="loading">Cargando el catálogo…</p>`;
    try {
      view.innerHTML = await builder();
      view.scrollTop = 0;
      markPlaying();
    } catch (error) {
      view.innerHTML = `<p class="empty">${esc(error.message)}</p>`;
    }
  }

  function card(href, cover, title, subtitle, action, id) {
    return `<article class="card">
      <div class="art">
        <a href="${href}"><img src="${esc(cover)}" alt=""></a>
        <button type="button" class="fab" data-action="${action}" data-id="${id}" aria-label="Reproducir ${esc(title)}">▶</button>
      </div>
      <a class="card-title" href="${href}">${esc(title)}</a>
      <p>${esc(subtitle)}</p>
    </article>`;
  }

  function trackFromRow(row) {
    const raw = row?.dataset?.id;
    if (raw == null) return null;
    if (known.has(raw)) return known.get(raw);
    const numeric = Number(raw);
    if (Number.isInteger(numeric) && known.has(numeric)) return known.get(numeric);
    return null;
  }

  function equipoHash(rel) {
    if (!rel) return "#/equipo";
    return `#/equipo/${rel.split("/").filter(Boolean).map(encodeURIComponent).join("/")}`;
  }

  function trackRow(track, index, options) {
    remember([track]);
    const id = esc(track.id);
    const device = Boolean(options.device) || track.source === "device";
    const album = device
      ? `<span class="track-album">${esc(track.album)}</span>`
      : options.album
        ? `<a class="track-album" href="#/album/${track.album_id}">${esc(track.album)}</a>`
        : "";
    const artist = device
      ? `<span>${esc(track.artist)}</span>`
      : `<a href="#/artist/${track.artist_id}">${esc(track.artist)}</a>`;
    const like = `<button type="button" class="icon-btn heart ${track.liked ? "is-on" : ""}" data-action="like" data-like="${id}" aria-label="Me gusta">${track.liked ? "♥" : "♡"}</button>`;
    const rowClass = device ? "device" : options.album ? "" : "no-album";
    return `<div class="track ${rowClass}" data-id="${id}" data-song="${esc(track.song_key || "")}" data-playlist="${options.playlistId || ""}">
      <button type="button" class="track-index" data-action="play-track" data-id="${id}" data-context="${options.context || ""}" data-context-id="${esc(options.contextId || "")}" data-index="${index}" aria-label="Reproducir">
        <span class="num">${index + 1}</span>
        <span class="hover-play">▶</span>
        <span class="eq" aria-hidden="true"><i></i><i></i><i></i></span>
      </button>
      <div class="track-main">
        <button type="button" class="track-title" data-action="play-track" data-id="${id}" data-context="${options.context || ""}" data-context-id="${esc(options.contextId || "")}" data-index="${index}">${esc(track.title)}</button>
        ${artist}
      </div>
      ${album}
      ${like}
      <span class="time">${fmt(track.duration_ms)}</span>
      <button type="button" class="icon-btn more" data-action="menu" data-id="${id}" data-playlist="${options.playlistId || ""}" aria-label="Más opciones">···</button>
    </div>`;
  }

  function collectionHead(item, kindLabel, action, id, extra) {
    return `<header class="sheet" style="--a:${item.accent_a};--b:${item.accent_b}">
      <img src="${esc(item.cover_url)}" alt="">
      <div>
        <p class="kicker">${esc(kindLabel)}</p>
        <h1>${esc(item.title || item.name)}</h1>
        <p class="sheet-sub">${extra}</p>
        <div class="sheet-actions">
          <button type="button" class="btn solid" data-action="${action}" data-id="${id}">Reproducir</button>
          ${item.editorial === false ? `<button type="button" class="btn ghost" data-action="edit-playlist" data-id="${id}">Editar</button>
            <button type="button" class="btn ghost danger" data-action="delete-playlist" data-id="${id}">Eliminar</button>` : ""}
        </div>
      </div>
    </header>`;
  }

  async function renderHome() {
    const data = await api.home();
    remember(data.recent);
    remember(data.popular);
    if (data.featured) remember(data.featured.tracks);
    const featured = data.featured;
    return `
      <p class="date-label">${esc(data.date_label)}</p>
      <h1 class="hello">${esc(data.greeting)}</h1>
      ${featured ? `<section class="hero" style="--a:${featured.accent_a};--b:${featured.accent_b}">
        <img src="${esc(featured.cover_url)}" alt="">
        <div>
          <p class="kicker">Lista editorial</p>
          <h2>${esc(featured.name)}</h2>
          <p>${esc(featured.description)}</p>
          <button type="button" class="btn solid" data-action="play-playlist" data-id="${featured.id}">Reproducir</button>
        </div>
      </section>` : ""}
      ${data.recent.length ? rowSection("Escuchado en este equipo", data.recent.map((track, index) => trackRow(track, index, { album: true, context: "recent", contextId: "recent" })).join(""), true) : ""}
      ${data.popular.length ? rowSection("Lo que más sonó aquí", data.popular.map((track, index) => trackRow(track, index, { album: true })).join(""), true) : ""}
      ${section("Listas", data.playlists.map((item) => card(`#/playlist/${item.id}`, item.cover_url, item.name, item.editorial ? "Editorial" : "Tu lista", "play-playlist", item.id)).join(""))}
      ${section("Álbumes", data.albums.map((item) => card(`#/album/${item.id}`, item.cover_url, item.title, albumSub(item), "play-album", item.id)).join(""))}
      ${section("Artistas", data.artists.map((item) => card(`#/artist/${item.id}`, item.cover_url, item.name, `${item.genre} · ${item.origin}`, "play-artist", item.id)).join(""))}
      <p class="catalog-note">Catálogo original compuesto para SpotyOFO. No es Spotify y no usa su música.</p>
    `;
  }

  function section(title, cards) {
    return `<section class="block"><div class="block-head"><h2>${esc(title)}</h2></div><div class="hscroll">${cards}</div></section>`;
  }

  function rowSection(title, rows, table) {
    return `<section class="block"><div class="block-head"><h2>${esc(title)}</h2></div>${table ? `<div class="tracklist">${rows}</div>` : rows}</section>`;
  }

  async function renderSearch(query) {
    const data = await api.search(query);
    remember(data.tracks);
    if (!query) {
      const genres = data.genres.map((genre) => {
        const colors = GENRES[genre] || ["#1b1e27", "#2ef2c0"];
        return `<button type="button" class="genre" style="--a:${colors[0]};--b:${colors[1]}" data-action="genre" data-genre="${esc(genre)}">${esc(genre)}</button>`;
      }).join("");
      return `<h1 class="hello">Buscar</h1><p class="lead">Explora el catálogo original por género o escribe un nombre.</p><div class="genre-grid">${genres}</div>`;
    }
    const empty = !data.tracks.length && !data.albums.length && !data.artists.length && !data.playlists.length;
    if (empty) return `<h1 class="hello">Sin resultados</h1><p class="lead">No hay coincidencias para «${esc(query)}».</p>`;
    return `
      <h1 class="hello">Resultados</h1>
      <p class="lead">«${esc(query)}»</p>
      ${data.tracks.length ? `<section class="block"><div class="block-head"><h2>Canciones</h2></div><div class="tracklist">${data.tracks.map((track, index) => trackRow(track, index, { album: true, context: "search", contextId: query })).join("")}</div></section>` : ""}
      ${data.artists.length ? section("Artistas", data.artists.map((item) => card(`#/artist/${item.id}`, item.cover_url, item.name, `${item.genre} · ${item.origin}`, "play-artist", item.id)).join("")) : ""}
      ${data.albums.length ? section("Álbumes", data.albums.map((item) => card(`#/album/${item.id}`, item.cover_url, item.title, albumSub(item), "play-album", item.id)).join("")) : ""}
      ${data.playlists.length ? section("Listas", data.playlists.map((item) => card(`#/playlist/${item.id}`, item.cover_url, item.name, item.editorial ? "Editorial" : "Tu lista", "play-playlist", item.id)).join("")) : ""}
    `;
  }

  async function renderLibrary() {
    const [lists, likes] = await Promise.all([api.playlists(), api.likes()]);
    remember(likes);
    const mine = lists.filter((item) => !item.editorial);
    const editorial = lists.filter((item) => item.editorial);
    return `
      <h1 class="hello">Biblioteca</h1>
      <p class="lead">Sin cuentas: Me gusta y las listas que crees viven en este equipo.</p>
      <div class="library-grid">
        <a class="lib-card likes" href="#/likes"><strong>Me gusta</strong><span>${likes.length} ${likes.length === 1 ? "canción" : "canciones"}</span></a>
        <button type="button" class="lib-card create" data-action="create-playlist"><strong>Nueva lista</strong><span>Armar una selección</span></button>
      </div>
      ${mine.length ? section("Tus listas", mine.map((item) => card(`#/playlist/${item.id}`, item.cover_url, item.name, item.description || "Lista personal", "play-playlist", item.id)).join("")) : ""}
      ${section("Editorial", editorial.map((item) => card(`#/playlist/${item.id}`, item.cover_url, item.name, item.description, "play-playlist", item.id)).join(""))}
    `;
  }

  async function renderLikes() {
    const tracks = await api.likes();
    remember(tracks);
    const duration = tracks.reduce((sum, track) => sum + track.duration_ms, 0);
    return `
      <header class="sheet likes-sheet">
        <div class="likes-art">♥</div>
        <div>
          <p class="kicker">Playlist</p>
          <h1>Me gusta</h1>
          <p class="sheet-sub">${tracks.length} canciones · ${fmt(duration)}</p>
          ${tracks.length ? `<div class="sheet-actions">
            <button type="button" class="btn solid" data-action="play-likes">Reproducir</button>
            <button type="button" class="btn ghost shuffle" data-action="play-likes-shuffle">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M16 4h5v5M4 18l5.5-5.5M14 14l6 6M4 6l6 6M16 20h5v-5"/></svg>
              Reproducir aleatoria
            </button>
          </div>` : ""}
        </div>
      </header>
      ${tracks.length ? `<div class="tracklist">${tracks.map((track, index) => trackRow(track, index, { album: true, context: "likes", contextId: "likes" })).join("")}</div>` : `<p class="empty">Todavía no marcaste canciones. El corazón de cada fila las guarda aquí.</p>`}
    `;
  }

  async function renderPlaylist(id) {
    const item = await api.playlist(id);
    remember(item.tracks);
    const meta = `${item.tracks.length} canciones · ${fmt(item.duration_ms)}${item.editorial ? " · Editorial" : ""}`;
    return `
      ${collectionHead({ ...item, title: item.name }, item.editorial ? "Lista editorial" : "Tu lista", "play-playlist", item.id, `${esc(item.description)}<br>${esc(meta)}`)}
      ${item.tracks.length ? `<div class="tracklist">${item.tracks.map((track, index) => trackRow(track, index, { album: true, context: "playlist", contextId: item.id, playlistId: item.editorial ? "" : item.id })).join("")}</div>` : `<p class="empty">Esta lista está vacía. Agrega canciones desde el menú ··· de cualquier pista.</p>`}
    `;
  }

  async function renderAlbum(id) {
    const item = await api.album(id);
    remember(item.tracks);
    const meta = `<a href="#/artist/${item.artist_id}">${esc(item.artist)}</a>${item.year ? ` · ${esc(item.year)}` : ""} · ${item.tracks.length} canciones · ${fmt(item.duration_ms)}<br>${esc(item.credit)}`;
    return `
      ${collectionHead(item, "Álbum", "play-album", item.id, meta)}
      <div class="tracklist">${item.tracks.map((track, index) => trackRow(track, index, { album: false, context: "album", contextId: item.id })).join("")}</div>
    `;
  }

  async function renderArtist(id) {
    const item = await api.artist(id);
    remember(item.top);
    return `
      <header class="artist-hero" style="--a:${item.accent_a};--b:${item.accent_b}">
        <img src="${esc(item.cover_url)}" alt="">
        <div>
          <p class="kicker">${esc(item.genre)} · ${esc(item.origin)}</p>
          <h1>${esc(item.name)}</h1>
          <p>${esc(item.bio)}</p>
          <button type="button" class="btn solid" data-action="play-artist" data-id="${item.id}">Reproducir</button>
        </div>
      </header>
      <section class="block"><div class="block-head"><h2>Popular</h2></div><div class="tracklist">${item.top.map((track, index) => trackRow(track, index, { album: true, context: "artist", contextId: item.id })).join("")}</div></section>
      ${section("Discos", item.albums.map((album) => card(`#/album/${album.id}`, album.cover_url, album.title, album.year ? String(album.year) : "Sin año", "play-album", album.id)).join(""))}
    `;
  }

  function uploadRow(track, index, admin) {
    remember([track]);
    return `<div class="track up-row" data-id="${track.id}" data-song="${esc(track.song_key || "")}">
      <button type="button" class="up-cover" data-action="play-track" data-id="${track.id}" data-context="uploads" data-context-id="uploads" data-index="${index}" aria-label="Reproducir ${esc(track.title)}">
        <img src="${esc(track.cover_url)}" alt="">
      </button>
      <div class="track-main">
        <button type="button" class="track-title" data-action="play-track" data-id="${track.id}" data-context="uploads" data-context-id="uploads" data-index="${index}">${esc(track.title)}</button>
        <a href="#/album/${track.album_id}">${esc(track.artist)} · ${esc(track.album)}</a>
      </div>
      <span class="time">${fmt(track.duration_ms)}</span>
      <button type="button" class="icon-btn heart ${track.liked ? "is-on" : ""}" data-action="like" data-like="${track.id}" aria-label="Me gusta">${track.liked ? "♥" : "♡"}</button>
      <button type="button" class="icon-btn up-delete" data-action="delete-upload" data-id="${track.id}" aria-label="Eliminar ${esc(track.title)}" title="${admin ? "Eliminar" : "Eliminar (pide PIN)"}">${TRASH_ICON}</button>
    </div>`;
  }

  function uploadListHtml(data) {
    if (!data.tracks.length) {
      return `<p class="empty">Aún no subiste canciones. Aparecerán aquí, en Buscar, en tus listas y en Me gusta.</p>`;
    }
    const total = data.tracks.reduce((sum, track) => sum + track.duration_ms, 0);
    return `<p class="fine">${data.tracks.length} ${data.tracks.length === 1 ? "canción" : "canciones"} · ${fmt(total)}</p>
      <div class="tracklist">${data.tracks.map((track, index) => uploadRow(track, index, data.auth.authenticated)).join("")}</div>`;
  }

  async function renderUploads() {
    const data = await api.uploads();
    const auth = data.auth;
    let panel;
    if (!auth.configured) {
      panel = `<div class="auth-card">
        <strong>Protege tus subidas con un PIN</strong>
        <p>Antes de subir o eliminar canciones, crea un PIN numérico (4 a 12 dígitos). Solo se guarda cifrado en este equipo.</p>
        <button type="button" class="btn solid" data-action="pin-setup">Crear PIN</button>
      </div>`;
    } else if (!auth.authenticated) {
      panel = `<div class="auth-card">
        <strong>Subir y eliminar están protegidos</strong>
        <p>Ingresa tu PIN para desbloquear en este navegador por 30 días.</p>
        <button type="button" class="btn solid" data-action="pin-login">Ingresar PIN</button>
      </div>`;
    } else {
      panel = `<div class="upload-panel">
        <label class="upload-drop" for="upload-input">
          <input type="file" id="upload-input" multiple accept="${UPLOAD_ACCEPT}">
          <strong>Elegir archivos</strong>
          <span>MP3, M4A/AAC, FLAC, OGG u WAV · hasta ${esc(data.max_mb)} MB cada uno · puedes elegir varios</span>
        </label>
        <div id="upload-queue" class="upload-queue"></div>
        <div class="sheet-actions">
          <button type="button" class="btn ghost" data-action="pin-change">Cambiar PIN</button>
          <button type="button" class="btn ghost" data-action="pin-logout">Bloquear</button>
        </div>
      </div>`;
    }
    return `
      <h1 class="hello">Subir música</h1>
      <p class="lead">Sube tus propios archivos desde el teléfono o este equipo. Se leen título, artista, álbum, número de pista, duración y portada.</p>
      ${panel}
      <section class="block"><div class="block-head"><h2>Tus archivos</h2></div><div id="upload-list">${uploadListHtml(data)}</div></section>
    `;
  }

  async function refreshUploadList() {
    const box = document.getElementById("upload-list");
    if (!box) return;
    const data = await api.uploads();
    box.innerHTML = uploadListHtml(data);
    markPlaying();
  }

  function pinField(name, label, autocomplete) {
    return `<label>${label}<input name="${name}" type="password" inputmode="numeric" pattern="[0-9]{4,12}" minlength="4" maxlength="12" autocomplete="${autocomplete}" required></label>`;
  }

  function pinForm(mode, after) {
    const setup = mode === "setup";
    const change = mode === "change";
    openModal({
      title: setup ? "Crea tu PIN" : change ? "Cambiar PIN" : "Ingresa tu PIN",
      confirm: setup ? "Crear PIN" : change ? "Guardar" : "Entrar",
      body: `
        ${setup ? `<p class="lead">Protege subir y eliminar canciones. Usa entre 4 y 12 dígitos. No se puede recuperar: si lo olvidas hay que borrarlo en el equipo.</p>` : ""}
        ${change ? pinField("current", "PIN actual", "current-password") : ""}
        ${setup || change ? `${pinField("pin", change ? "PIN nuevo" : "PIN", "new-password")}${pinField("confirm", "Repite el PIN", "new-password")}` : pinField("pin", "PIN", "current-password")}
      `,
      onSubmit: async (form) => {
        const values = new FormData(form);
        const pin = String(values.get("pin") || "").trim();
        if (!/^\d{4,12}$/.test(pin)) throw new Error("El PIN debe tener entre 4 y 12 dígitos.");
        if ((setup || change) && pin !== String(values.get("confirm") || "").trim()) throw new Error("Los PIN no coinciden.");
        if (setup) await api.authSetup(pin, String(values.get("confirm")).trim());
        else if (change) await api.authChange(String(values.get("current") || "").trim(), pin, String(values.get("confirm")).trim());
        else await api.authLogin(pin);
        closeModal();
        showToast(setup ? "PIN creado" : change ? "PIN actualizado" : "Desbloqueado");
        if (after) await after();
        else if (parts()[0] === "uploads") route();
      },
    });
  }

  async function withAdmin(action) {
    const auth = await api.auth();
    if (auth.authenticated) return action();
    pinForm(auth.configured ? "login" : "setup", action);
    return null;
  }

  async function adminCall(action) {
    try {
      return await action();
    } catch (error) {
      if (error.status === 401) {
        pinForm(error.data?.needs === "setup" ? "setup" : "login", () => adminCall(action));
        return null;
      }
      throw error;
    }
  }

  function confirmDelete(track) {
    openModal({
      title: "Eliminar canción",
      confirm: "Eliminar",
      danger: true,
      body: `<p>Se eliminará «${esc(track.title)}» de ${esc(track.artist)}, con su archivo. También sale de tus listas y de Me gusta.</p>`,
      onSubmit: async () => {
        closeModal();
        await adminCall(async () => {
          await api.deleteTrack(track.id);
          known.delete(track.id);
          const snap = player.snapshot();
          for (let i = snap.queue.length - 1; i >= 0; i -= 1) {
            if (snap.queue[i].id === track.id && i !== snap.index) player.removeQueued(i);
          }
          showToast("Canción eliminada");
          if (parts()[0] === "uploads") await refreshUploadList();
          else route();
        });
      },
    });
  }

  function queueItem(file) {
    const box = document.getElementById("upload-queue");
    const item = document.createElement("div");
    item.className = "up-item";
    item.innerHTML = `<div class="up-item-head"><strong>${esc(file.name)}</strong><span>${esc(sizeLabel(file.size))}</span></div>
      <div class="up-bar"><i></i></div>
      <p class="up-status">En espera</p>`;
    if (box) box.prepend(item);
    return {
      progress(ratio) {
        item.querySelector(".up-bar i").style.width = `${Math.round(ratio * 100)}%`;
        item.querySelector(".up-status").textContent = ratio >= 1 ? "Procesando etiquetas…" : `Subiendo… ${Math.round(ratio * 100)}%`;
      },
      done(state, message) {
        item.classList.add(`is-${state}`);
        if (state !== "ok") item.querySelector(".up-bar i").style.width = "100%";
        item.querySelector(".up-status").textContent = message;
      },
    };
  }

  async function uploadFiles(files) {
    const maxBytes = 200 * 1024 * 1024;
    const items = files.map((file) => ({ file, row: queueItem(file) }));
    uploading = true;
    let added = 0;
    try {
      for (let i = 0; i < items.length; i += 1) {
        const { file, row } = items[i];
        const ext = `.${file.name.split(".").pop().toLowerCase()}`;
        if (!UPLOAD_EXT.includes(ext) && !(file.type || "").startsWith("audio/")) {
          row.done("error", "Formato no admitido");
          continue;
        }
        if (file.size > maxBytes) {
          row.done("error", "Supera 200 MB");
          continue;
        }
        try {
          const track = await api.upload(file, (ratio) => row.progress(ratio));
          remember([track]);
          added += 1;
          row.done("ok", `Lista: ${track.title} · ${track.artist}`);
        } catch (error) {
          if (error.status === 409) row.done("dup", error.message);
          else if (error.status === 401) {
            row.done("error", "Se pidió el PIN de nuevo");
            items.slice(i + 1).forEach((rest) => rest.row.done("error", "Pendiente: vuelve a elegir el archivo"));
            pinForm(error.data?.needs === "setup" ? "setup" : "login");
            break;
          } else row.done("error", error.message);
        }
      }
    } finally {
      uploading = false;
    }
    if (added) {
      showToast(added === 1 ? "1 canción agregada" : `${added} canciones agregadas`);
      await refreshUploadList().catch(() => {});
    }
  }

  function folderCard(folder) {
    const detail = folder.audio_count
      ? `${folder.audio_count} ${folder.audio_count === 1 ? "pista" : "pistas"}`
      : folder.folder_count
        ? `${folder.folder_count} ${folder.folder_count === 1 ? "carpeta" : "carpetas"}`
        : "Carpeta";
    return `<a class="folder-card" href="${equipoHash(folder.path)}">
      <span class="folder-ico" aria-hidden="true"></span>
      <strong>${esc(folder.name)}</strong>
      <span>${detail}</span>
    </a>`;
  }

  async function renderEquipo(rel) {
    const data = await api.equipo(rel);
    remember(data.tracks);
    const crumbs = [`<a href="#/equipo">Música</a>`, ...data.crumbs.map((crumb) => `<a href="${equipoHash(crumb.path)}">${esc(crumb.name)}</a>`)].join(" / ");
    const summary = `${data.folders.length} carpetas · ${data.tracks.length} pistas en esta carpeta`;
    return `
      <div class="crumbs">${crumbs}</div>
      <header class="sheet" style="--a:#10243a;--b:#2ef2c0">
        <img src="${esc(data.cover_url)}" alt="">
        <div>
          <p class="kicker">Archivo de Equipo</p>
          <h1>${esc(data.name)}</h1>
          <p class="sheet-sub">${esc(summary)}<br>${esc(data.location)}</p>
          ${data.tracks.length ? `<div class="sheet-actions">
            <button type="button" class="btn solid" data-action="play-equipo">Reproducir</button>
            <button type="button" class="btn ghost heart ${data.liked ? "is-on" : ""}" data-action="like-equipo" data-path="${esc(data.path)}" aria-pressed="${data.liked ? "true" : "false"}">${data.liked ? "♥" : "♡"} Me gusta</button>
            <button type="button" class="btn ghost" data-action="add-equipo" data-path="${esc(data.path)}" data-name="${esc(data.name)}" data-count="${data.tracks.length}">Agregar a mi lista</button>
          </div>` : ""}
        </div>
      </header>
      ${data.folders.length ? `<div class="folder-grid">${data.folders.map(folderCard).join("")}</div>` : ""}
      ${data.tracks.length ? `<div class="tracklist">${data.tracks.map((track, index) => trackRow(track, index, { device: true, album: true, context: "equipo", contextId: rel })).join("")}</div>` : ""}
      ${!data.folders.length && !data.tracks.length ? `<p class="empty">Esta carpeta no tiene música.</p>` : ""}
    `;
  }

  async function route() {
    const [name, arg] = parts();
    if (name !== "search" && document.activeElement !== searchInput) searchInput.value = "";
    markNav();
    paintSide().catch(() => {});
    closeMenu();
    if (!name) return show(renderHome);
    if (name === "search") {
      const query = arg ? decodeURIComponent(parts().slice(1).join("/")) : "";
      if (document.activeElement !== searchInput) searchInput.value = query;
      return show(() => renderSearch(query));
    }
    if (name === "library") return show(renderLibrary);
    if (name === "uploads") return show(renderUploads);
    if (name === "equipo") {
      const rel = parts().slice(1).map(decodeURIComponent).join("/");
      return show(() => renderEquipo(rel));
    }
    if (name === "likes") return show(renderLikes);
    if (name === "playlist" && arg) return show(() => renderPlaylist(arg));
    if (name === "album" && arg) return show(() => renderAlbum(arg));
    if (name === "artist" && arg) return show(() => renderArtist(arg));
    return show(renderHome);
  }

  async function playAction(action, id, index = 0) {
    let tracks = [];
    if (action === "play-album") tracks = (await api.album(id)).tracks;
    if (action === "play-playlist") tracks = (await api.playlist(id)).tracks;
    if (action === "play-artist") tracks = (await api.artist(id)).top;
    if (action === "play-likes") tracks = await api.likes();
    if (action === "play-track") {
      const context = menuTrack;
      tracks = context || [];
    }
    remember(tracks);
    if (!tracks.length) {
      showToast("No hay pistas para reproducir");
      return;
    }
    await player.playContext(tracks, index);
  }

  async function playFromRow(button) {
    const context = button.dataset.context;
    const contextId = button.dataset.contextId;
    const index = Number(button.dataset.index || 0);
    if (context === "album") return playAction("play-album", contextId, index);
    if (context === "playlist") return playAction("play-playlist", contextId, index);
    if (context === "artist") return playAction("play-artist", contextId, index);
    if (context === "likes") return playAction("play-likes", null, index);
    if (context === "search" || context === "recent" || context === "uploads" || context === "equipo") {
      const rows = [...view.querySelectorAll(".track")].map((row) => trackFromRow(row)).filter(Boolean);
      remember(rows);
      await player.playContext(rows, index);
      return;
    }
    const track = trackFromRow(button);
    if (track) await player.playContext([track], 0);
  }

  function markPlaying() {
    const id = player.current()?.id;
    document.querySelectorAll(".track").forEach((row) => {
      row.classList.toggle("is-current", String(row.dataset.id) === String(id));
    });
  }

  function syncPlayer(snap) {
    const track = snap.track;
    const title = document.getElementById("p-title");
    const artist = document.getElementById("p-artist");
    const cover = document.getElementById("p-cover");
    const like = document.getElementById("p-like");
    document.body.classList.toggle("is-playing", snap.playing);
    const playBtn = document.getElementById("p-play");
    playBtn.innerHTML = snap.playing
      ? `<svg viewBox="0 0 24 24"><path d="M7 5h4v14H7zM13 5h4v14h-4z"/></svg>`
      : `<svg viewBox="0 0 24 24"><path d="M8 5v14l12-7z"/></svg>`;
    playBtn.setAttribute("aria-label", snap.playing ? "Pausar" : "Reproducir");
    document.getElementById("p-shuffle").classList.toggle("is-on", snap.shuffle);
    const repeatBtn = document.getElementById("p-repeat");
    repeatBtn.classList.toggle("is-on", snap.repeat !== "off");
    repeatBtn.dataset.mode = snap.repeat;
    repeatBtn.setAttribute("aria-label", snap.repeat === "one" ? "Repetir una" : snap.repeat === "all" ? "Repetir lista" : "Repetir");
    const volume = document.getElementById("p-volume");
    volume.value = String(Math.round((snap.muted ? 0 : snap.volume) * 100));
    paintRange(volume);
    if (!track) {
      title.textContent = "Elige una pista";
      artist.textContent = "";
      cover.removeAttribute("src");
      like.disabled = true;
      document.title = "SpotyOFO";
      return;
    }
    title.textContent = track.title;
    artist.textContent = track.artist;
    cover.src = track.cover_url;
    cover.alt = track.album;
    like.disabled = false;
    like.classList.toggle("is-on", Boolean(track.liked));
    document.title = `${track.title} · ${track.artist} — SpotyOFO`;
    const nowTitle = document.getElementById("now-title");
    nowTitle.textContent = track.title;
    document.getElementById("now-artist").textContent = track.artist;
    document.getElementById("now-kicker").textContent = track.album;
    document.getElementById("now-meta").textContent = [track.bpm ? `${track.bpm} BPM` : "", track.musical_key, track.genre].filter(Boolean).join(" · ");
    document.getElementById("now-cover").src = track.cover_url;
    document.getElementById("now-bg").style.backgroundImage = `url("${track.cover_url}")`;
    markPlaying();
    if (drawerMode === "queue") renderQueue(snap);
  }

  function syncTime(time) {
    if (seeking) return;
    const seek = document.getElementById("p-seek");
    const ratio = time.duration ? time.current / time.duration : 0;
    seek.value = String(Math.round(ratio * 1000));
    paintRange(seek);
    document.getElementById("p-elapsed").textContent = fmt(time.current * 1000);
    document.getElementById("p-duration").textContent = fmt((time.duration || 0) * 1000);
  }

  function paintRange(input) {
    const max = Number(input.max) || 100;
    const pct = (Number(input.value) / max) * 100;
    input.style.background = `linear-gradient(90deg, var(--aqua) ${pct}%, rgba(255,255,255,.16) ${pct}%)`;
  }

  function openDrawer(mode) {
    drawerMode = drawer.hidden || drawerMode !== mode ? mode : null;
    if (!drawerMode) {
      drawer.hidden = true;
      document.querySelector(".app").classList.remove("with-drawer");
      return;
    }
    drawer.hidden = false;
    document.querySelector(".app").classList.add("with-drawer");
    drawerTitle.textContent = mode === "queue" ? "Cola" : "Estudio";
    if (mode === "queue") renderQueue(player.snapshot());
    else renderStudio(player.snapshot());
  }

  function renderQueue(snap) {
    if (!snap.queue.length) {
      drawerBody.innerHTML = `<p class="empty">La cola está vacía. Reproduce un disco o usa «Agregar a la cola».</p>`;
      return;
    }
    drawerBody.innerHTML = `<ol class="queue">${snap.queue.map((track, index) => `
      <li class="${index === snap.index ? "is-current" : ""}">
        <button type="button" data-action="queue-play" data-index="${index}">
          <img src="${esc(track.cover_url)}" alt="">
          <span><strong>${esc(track.title)}</strong><small>${esc(track.artist)}</small></span>
        </button>
        ${index === snap.index ? "" : `<button type="button" class="icon-btn" data-action="queue-remove" data-index="${index}" aria-label="Quitar">×</button>`}
      </li>`).join("")}</ol>`;
  }

  function renderStudio(snap) {
    const [bass, mid, treble] = snap.eq;
    drawerBody.innerHTML = `
      <p class="lead">Cadena: archivo → graves → medios → agudos → volumen → visualizador.</p>
      <div class="presets">
        ${Object.keys(player.presets).map((name) => `<button type="button" class="chip" data-action="preset" data-preset="${name}">${esc(name)}</button>`).join("")}
      </div>
      ${slider("Graves", "bass", bass, "120 Hz")}
      ${slider("Medios", "mid", mid, "1.1 kHz")}
      ${slider("Agudos", "treble", treble, "5.2 kHz")}
      <label class="check"><input type="checkbox" id="autoplay" ${snap.autoplay ? "checked" : ""}> Sigue sonando con pistas del mismo género</label>
      <p class="fine">Atajos: espacio reproduce, flechas izquierda y derecha mueven 5 s, arriba y abajo cambian el volumen.</p>
    `;
    drawerBody.querySelectorAll('input[type="range"]').forEach(paintRange);
  }

  function slider(label, key, value, hint) {
    return `<label class="eq-slider"><span>${label}<small>${hint}</small></span>
      <input type="range" min="-12" max="12" step="0.5" value="${value}" data-eq="${key}" aria-label="${label}">
      <em data-eq-readout="${key}">${Number(value).toFixed(1)}</em>
    </label>`;
  }

  function closeMenu() {
    menu.hidden = true;
    menuTrack = null;
  }

  function openMenu(button, track) {
    menuTrack = track;
    menuPlaylistId = button.dataset.playlist || "";
    const id = esc(track.id);
    const remove = menuPlaylistId
      ? `<button type="button" data-action="remove-from-list" data-id="${id}" data-playlist="${menuPlaylistId}">Quitar de esta lista</button>`
      : "";
    const libraryLinks = track.source === "device"
      ? `<button type="button" data-action="add-to-playlist" data-id="${id}">Agregar a una lista</button>
      ${remove}
      <a href="${equipoHash(track.folder || "")}">Abrir carpeta</a>`
      : `<button type="button" data-action="add-to-playlist" data-id="${id}">Agregar a una lista</button>
      ${remove}
      ${track.source === "user" ? `<button type="button" data-action="delete-upload" data-id="${id}">Eliminar archivo</button>` : ""}
      <a href="#/album/${track.album_id}">Ir al álbum</a>
      <a href="#/artist/${track.artist_id}">Ir al artista</a>`;
    menu.innerHTML = `
      <button type="button" data-action="play-now" data-id="${id}">Reproducir</button>
      <button type="button" data-action="play-next" data-id="${id}">Reproducir a continuación</button>
      <button type="button" data-action="enqueue" data-id="${id}">Agregar a la cola</button>
      ${libraryLinks}
    `;
    menu.hidden = false;
    const rect = button.getBoundingClientRect();
    const width = 240;
    const left = Math.min(rect.left, window.innerWidth - width - 12);
    const top = Math.min(rect.bottom + 6, window.innerHeight - 280);
    menu.style.left = `${Math.max(8, left)}px`;
    menu.style.top = `${Math.max(8, top)}px`;
  }

  function openModal({ title, body, confirm, danger, onSubmit }) {
    document.getElementById("modal-title").textContent = title;
    document.getElementById("modal-body").innerHTML = body;
    const ok = document.getElementById("modal-ok");
    ok.textContent = confirm;
    ok.classList.toggle("danger", Boolean(danger));
    modal.hidden = false;
    modalSubmit = onSubmit;
    const field = modal.querySelector("input, textarea, button");
    if (field) field.focus();
  }

  function closeModal() {
    modal.hidden = true;
    modalSubmit = null;
    pendingLibrary = null;
  }

  function playlistForm(item, options = {}) {
    const attach = options.attach || null;
    openModal({
      title: item ? "Editar lista" : "Nueva lista",
      confirm: "Guardar",
      body: `
        <label>Nombre<input name="name" maxlength="80" required value="${esc(item?.name || "")}"></label>
        <label>Descripción<textarea name="description" maxlength="280" rows="3">${esc(item?.description || "")}</textarea></label>
      `,
      onSubmit: async (form) => {
        const payload = {
          name: new FormData(form).get("name"),
          description: new FormData(form).get("description"),
        };
        const saved = item
          ? await api.updatePlaylist(item.id, payload)
          : await api.createPlaylist(payload);
        const message = !item && attach ? await attach(saved) : (item ? "Lista actualizada" : "Lista creada");
        closeModal();
        showToast(message);
        await paintSide();
        go(`#/playlist/${saved.id}`);
      },
    });
  }

  function playlistToast(result) {
    const name = result.playlist_name;
    if (result.added && result.skipped) return `${result.added} agregadas a ${name}. ${result.skipped} ya estaban.`;
    if (result.added === 1) return `1 canción agregada a ${name}`;
    if (result.added) return `${result.added} canciones agregadas a ${name}`;
    return `Esas pistas ya están en ${name}`;
  }

  async function saveToPlaylist(playlistId, pending) {
    if (pending.kind === "folder" || pending.track?.source === "device") {
      const path = pending.kind === "folder" ? pending.path : pending.track.id;
      return playlistToast(await api.equipoPlaylist(path, playlistId));
    }
    await api.addTrack(playlistId, pending.track.id);
    return "Agregada a la lista";
  }

  async function openLibraryModal(pending) {
    pendingLibrary = pending;
    const lists = (await api.playlists()).filter((item) => !item.editorial);
    const lead = pending.kind === "folder"
      ? `${pending.count} ${pending.count === 1 ? "pista" : "pistas"} de «${esc(pending.name)}»`
      : `«${esc(pending.track.title)}»`;
    openModal({
      title: "Agregar a mi lista",
      confirm: "Cerrar",
      body: `
        <p class="lead">${lead}</p>
        <div class="pick-list">
          ${lists.map((item) => `<button type="button" data-action="pick-playlist" data-id="${item.id}">${esc(item.name)}</button>`).join("") || `<p class="empty">Aún no tienes listas. Crea una y se agregan ahí.</p>`}
        </div>
        <button type="button" class="btn ghost" data-action="create-playlist">Crear lista</button>
      `,
      onSubmit: async () => closeModal(),
    });
  }

  async function onViewClick(event) {
    const target = event.target.closest("[data-action]");
    if (!target || !view.contains(target)) return;
    const action = target.dataset.action;
    if (action === "play-track") {
      event.preventDefault();
      await playFromRow(target);
      return;
    }
    if (action === "like") {
      event.preventDefault();
      await toggleLike(target.dataset.like);
      return;
    }
    if (action === "like-equipo") {
      target.disabled = true;
      try {
        const result = await api.equipoLike(target.dataset.path, target.getAttribute("aria-pressed") === "true");
        remember(result.tracks);
        result.tracks.forEach((track) => paintSong(track.song_key, track.liked));
        paintFolderLike(result.liked);
        showToast(folderLikeToast(result));
      } finally {
        target.disabled = false;
      }
      return;
    }
    if (action === "add-equipo") {
      await openLibraryModal({
        kind: "folder",
        path: target.dataset.path,
        name: target.dataset.name,
        count: Number(target.dataset.count || 0),
      });
      return;
    }
    if (action === "menu") {
      const track = trackFromRow(target);
      if (track) openMenu(target, track);
      return;
    }
    if (action === "genre") {
      go(`#/search/${encodeURIComponent(target.dataset.genre)}`);
      return;
    }
    if (action === "create-playlist") {
      playlistForm(null);
      return;
    }
    if (action === "pin-setup" || action === "pin-login" || action === "pin-change") {
      pinForm(action.slice(4));
      return;
    }
    if (action === "pin-logout") {
      await api.authLogout();
      showToast("Subidas bloqueadas");
      route();
      return;
    }
    if (action === "delete-upload") {
      const track = trackFromRow(target);
      if (track) await withAdmin(() => confirmDelete(track));
      return;
    }
    if (action === "play-equipo") {
      const rows = [...view.querySelectorAll(".track")].map((row) => trackFromRow(row)).filter(Boolean);
      if (!rows.length) {
        showToast("Esta carpeta no tiene pistas para reproducir");
        return;
      }
      await player.playContext(rows, 0);
      return;
    }
    if (action === "play-likes" || action === "play-likes-shuffle") {
      const tracks = await api.likes();
      remember(tracks);
      if (!tracks.length) {
        showToast("No hay pistas para reproducir");
        return;
      }
      if (action === "play-likes-shuffle") await player.playShuffled(tracks);
      else await player.playContext(tracks, 0, { shuffle: false });
      return;
    }
    if (["play-album", "play-playlist", "play-artist"].includes(action)) {
      await playAction(action, target.dataset.id, 0);
      return;
    }
    if (action === "edit-playlist") {
      const item = await api.playlist(target.dataset.id);
      playlistForm(item);
      return;
    }
    if (action === "delete-playlist") {
      const item = await api.playlist(target.dataset.id);
      openModal({
        title: "Eliminar lista",
        confirm: "Eliminar",
        danger: true,
        body: `<p>Se eliminará «${esc(item.name)}». Las canciones siguen en el catálogo.</p>`,
        onSubmit: async () => {
          await api.deletePlaylist(item.id);
          closeModal();
          showToast("Lista eliminada");
          await paintSide();
          go("#/library");
        },
      });
    }
  }

  function paintHearts(id, liked) {
    document.querySelectorAll("[data-like]").forEach((button) => {
      if (button.dataset.like !== String(id)) return;
      button.classList.toggle("is-on", liked);
      button.textContent = liked ? "♥" : "♡";
    });
  }

  function paintSong(songKey, liked) {
    if (!songKey) return;
    known.forEach((track, id) => {
      if (track.song_key !== songKey) return;
      track.liked = liked;
      player.setLiked(id, liked);
      paintHearts(id, liked);
    });
    if (parts()[0] === "likes" && !liked) {
      document.querySelectorAll(".track").forEach((row) => {
        if (row.dataset.song === songKey) row.remove();
      });
      const sub = view.querySelector(".likes-sheet .sheet-sub");
      if (sub) {
        const left = view.querySelectorAll(".tracklist .track").length;
        sub.textContent = sub.textContent.replace(/^\d+/, String(left));
      }
    }
    refreshFolderLike();
    syncPlayer(player.snapshot());
  }

  function folderLikeToast(result) {
    if (!result.liked) return result.tracks.length === 1 ? "Quitada de Me gusta" : "Quitadas de Me gusta";
    const added = Number(result.added || 0);
    const skipped = Number(result.skipped || 0);
    if (!added && skipped) {
      return skipped === 1 ? "Esa canción ya está en Me gusta" : "Esas canciones ya están en Me gusta";
    }
    const noun = added === 1 ? "canción" : "canciones";
    if (skipped) return `${added} ${noun} en Me gusta. ${skipped} ya ${skipped === 1 ? "estaba" : "estaban"}.`;
    return `${added} ${noun} en Me gusta`;
  }

  function likeToast(result) {
    if (result.duplicate) return "Esa canción ya está en Me gusta";
    return result.liked ? "Guardada en Me gusta" : "Quitada de Me gusta";
  }

  function paintFolderLike(liked) {
    const button = view.querySelector("[data-action='like-equipo']");
    if (!button) return;
    button.classList.toggle("is-on", liked);
    button.setAttribute("aria-pressed", liked ? "true" : "false");
    button.textContent = `${liked ? "♥" : "♡"} Me gusta`;
  }

  function refreshFolderLike() {
    const rows = [...view.querySelectorAll(".track")].map((row) => trackFromRow(row)).filter(Boolean);
    if (!rows.length) return;
    paintFolderLike(rows.every((track) => track.liked));
  }

  async function toggleLike(id) {
    const current = trackFromRow({ dataset: { id: String(id) } }) || known.get(id) || known.get(Number(id));
    const shown = Boolean(current?.liked);
    if (current?.source === "device") {
      const result = await api.equipoLike(current.id, shown);
      const track = result.tracks?.[0];
      if (track) remember([track]);
      paintSong(result.song_key || track?.song_key || current.song_key, result.liked);
      showToast(likeToast(result));
      return;
    }
    const result = await api.toggleLike(id, shown);
    if (current) current.liked = result.liked;
    paintSong(result.song_key || current?.song_key, result.liked);
    showToast(likeToast(result));
  }

  function boot() {
    document.getElementById("go-back").addEventListener("click", () => history.back());
    document.getElementById("go-forward").addEventListener("click", () => history.forward());
    document.getElementById("new-playlist").addEventListener("click", () => playlistForm(null));
    document.getElementById("search-form").addEventListener("submit", (event) => {
      event.preventDefault();
      go(`#/search/${encodeURIComponent(searchInput.value.trim())}`);
    });
    searchInput.addEventListener("input", () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        go(`#/search/${encodeURIComponent(searchInput.value.trim())}`);
      }, 180);
    });
    view.addEventListener("click", (event) => {
      onViewClick(event).catch((error) => showToast(error.message));
    });
    view.addEventListener("contextmenu", (event) => {
      const row = event.target.closest(".track");
      if (!row) return;
      event.preventDefault();
      const track = trackFromRow(row);
      if (track) openMenu(row.querySelector("[data-action='menu']") || row, track);
    });
    document.addEventListener("click", (event) => {
      if (!menu.hidden && !menu.contains(event.target) && !event.target.closest("[data-action='menu']")) closeMenu();
    });
    menu.addEventListener("click", async (event) => {
      const target = event.target.closest("[data-action], a");
      if (!target) return;
      const action = target.dataset.action;
      const track = menuTrack;
      closeMenu();
      if (!action || !track) return;
      if (action === "play-now") await player.playContext([track], 0);
      if (action === "play-next") {
        player.playNext(track);
        showToast("Sonará a continuación");
      }
      if (action === "enqueue") {
        player.enqueue(track);
        showToast("Agregada a la cola");
      }
      if (action === "add-to-playlist") {
        await openLibraryModal({ kind: "track", track });
      }
      if (action === "remove-from-list") {
        if (track.source === "device") await api.removeDeviceTrack(target.dataset.playlist, track.id);
        else await api.removeTrack(target.dataset.playlist, track.id);
        showToast("Quitada de la lista");
        route();
      }
      if (action === "delete-upload") {
        await withAdmin(() => confirmDelete(track)).catch((error) => showToast(error.message));
      }
    });
    view.addEventListener("change", (event) => {
      if (event.target.id !== "upload-input") return;
      const files = [...event.target.files];
      event.target.value = "";
      if (files.length) uploadFiles(files).catch((error) => showToast(error.message));
    });
    window.addEventListener("beforeunload", (event) => {
      if (!uploading) return;
      event.preventDefault();
      event.returnValue = "";
    });
    document.getElementById("modal-cancel").addEventListener("click", closeModal);
    modal.addEventListener("click", async (event) => {
      if (event.target === modal) closeModal();
      const pick = event.target.closest("[data-action='pick-playlist']");
      if (pick && pendingLibrary) {
        const message = await saveToPlaylist(pick.dataset.id, pendingLibrary);
        showToast(message);
        closeModal();
      }
      if (event.target.closest("[data-action='create-playlist']")) {
        const pending = pendingLibrary;
        pendingLibrary = null;
        playlistForm(null, { attach: pending ? (saved) => saveToPlaylist(saved.id, pending) : null });
      }
    });
    document.getElementById("modal-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!modalSubmit) return;
      try {
        await modalSubmit(event.currentTarget);
      } catch (error) {
        showToast(error.message);
      }
    });
    document.getElementById("p-play").addEventListener("click", () => player.toggle());
    document.getElementById("p-prev").addEventListener("click", () => player.previous());
    document.getElementById("p-next").addEventListener("click", () => player.next());
    document.getElementById("p-shuffle").addEventListener("click", () => player.toggleShuffle());
    document.getElementById("p-repeat").addEventListener("click", () => player.cycleRepeat());
    document.getElementById("p-like").addEventListener("click", () => {
      const track = player.current();
      if (track) toggleLike(track.id).catch((error) => showToast(error.message));
    });
    document.getElementById("p-title").addEventListener("click", () => {
      const track = player.current();
      if (!track) return;
      if (track.source === "device") go(equipoHash(track.folder || ""));
      else if (track.album_id) go(`#/album/${track.album_id}`);
    });
    document.getElementById("p-artist").addEventListener("click", () => {
      const track = player.current();
      if (!track) return;
      if (track.source === "device") {
        const folder = track.folder || "";
        const parent = folder.includes("/") ? folder.slice(0, folder.lastIndexOf("/")) : "";
        go(equipoHash(parent));
      } else if (track.artist_id) go(`#/artist/${track.artist_id}`);
    });
    document.getElementById("np-open").addEventListener("click", () => {
      if (!player.current()) return;
      document.getElementById("now").hidden = false;
    });
    document.getElementById("now-close").addEventListener("click", () => {
      document.getElementById("now").hidden = true;
    });
    document.getElementById("p-queue").addEventListener("click", () => openDrawer("queue"));
    document.getElementById("p-studio").addEventListener("click", () => openDrawer("studio"));
    document.getElementById("drawer-close").addEventListener("click", () => openDrawer(drawerMode));
    drawerBody.addEventListener("input", (event) => {
      const eq = event.target.dataset.eq;
      if (eq) {
        const snap = player.snapshot();
        const values = { bass: snap.eq[0], mid: snap.eq[1], treble: snap.eq[2] };
        values[eq] = Number(event.target.value);
        player.applyEq(values.bass, values.mid, values.treble);
        const readout = drawerBody.querySelector(`[data-eq-readout="${eq}"]`);
        if (readout) readout.textContent = Number(event.target.value).toFixed(1);
        paintRange(event.target);
      }
    });
    drawerBody.addEventListener("click", (event) => {
      const preset = event.target.closest("[data-action='preset']");
      if (preset) {
        player.applyPreset(preset.dataset.preset);
        renderStudio(player.snapshot());
      }
      const play = event.target.closest("[data-action='queue-play']");
      if (play) player.playQueueIndex(Number(play.dataset.index));
      const remove = event.target.closest("[data-action='queue-remove']");
      if (remove) player.removeQueued(Number(remove.dataset.index));
    });
    drawerBody.addEventListener("change", (event) => {
      if (event.target.id === "autoplay") player.setAutoplay(event.target.checked);
    });
    const seek = document.getElementById("p-seek");
    seek.addEventListener("pointerdown", () => {
      seeking = true;
    });
    seek.addEventListener("input", () => paintRange(seek));
    seek.addEventListener("change", () => {
      player.seekRatio(Number(seek.value) / 1000);
      seeking = false;
    });
    const volume = document.getElementById("p-volume");
    volume.addEventListener("input", () => {
      player.setVolume(Number(volume.value) / 100);
      paintRange(volume);
    });
    document.getElementById("p-mute").addEventListener("click", () => player.toggleMute());
    window.addEventListener("hashchange", () => route());
    window.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        document.getElementById("now").hidden = true;
        closeMenu();
        closeModal();
      }
      const tag = document.activeElement?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      if (event.code === "Space") {
        event.preventDefault();
        player.toggle();
      } else if (event.code === "ArrowRight") player.seekBy(5);
      else if (event.code === "ArrowLeft") player.seekBy(-5);
      else if (event.code === "ArrowUp") {
        event.preventDefault();
        player.setVolume(player.snapshot().volume + 0.05);
      } else if (event.code === "ArrowDown") {
        event.preventDefault();
        player.setVolume(player.snapshot().volume - 0.05);
      }
    });
    player.on("state", syncPlayer);
    player.on("time", syncTime);
    const snap = player.snapshot();
    volume.value = String(Math.round(snap.volume * 100));
    paintRange(volume);
    paintRange(seek);
    syncPlayer(snap);
    if (!location.hash) location.hash = "#/";
    else route();
  }

  return { boot };
}
