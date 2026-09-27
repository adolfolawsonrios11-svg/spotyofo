import { api } from "./api.js?v=20260927e";
import { EQ_FREQS, EQ_LABELS, EQ_PRESETS } from "./player.js?v=20260927e";

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
const UI_KEY = "spotyofo-ui";
const PLAY_ICON = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14l12-7z"/></svg>`;
const PAUSE_ICON = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5h4v14H7zM13 5h4v14h-4z"/></svg>`;
const SHORTCUTS = [
  ["Espacio", "Reproducir / pausar"],
  ["← / →", "Retroceder / adelantar 5 s"],
  ["Mayús + ← / →", "Pista anterior / siguiente"],
  ["↑ / ↓", "Subir / bajar volumen"],
  ["M", "Silenciar"],
  ["S", "Aleatorio"],
  ["R", "Repetir (lista, una, apagado)"],
  ["L", "Me gusta en la pista actual"],
  ["N / P", "Siguiente / anterior"],
  ["F", "Abrir «Ahora suena»"],
  ["/", "Buscar"],
  ["?", "Ver estos atajos"],
  ["Esc", "Cerrar ventanas y paneles"],
];
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
  const picker = document.getElementById("picker");
  const pickerList = document.getElementById("picker-list");
  const pickerSearch = document.getElementById("picker-search");
  const pickerNote = document.getElementById("picker-note");
  const pickerCreate = document.getElementById("picker-create");
  const pickerNew = document.getElementById("picker-new");
  const srStatus = document.getElementById("sr-status");
  const coarseQuery = window.matchMedia("(pointer: coarse)");
  let pickerState = null;
  let pickerFocus = null;
  let menuOpener = null;
  let orderTimer = 0;
  let orderPlaylist = null;
  let drawerMode = null;
  let toastTimer = 0;
  let searchTimer = 0;
  let seeking = false;
  let modalSubmit = null;
  let menuTrack = null;
  let menuPlaylistId = null;
  let uploading = false;
  let nowSeeking = false;
  let lastFocus = null;
  let modalFocus = null;
  let installPrompt = null;
  let outputsLoaded = false;
  const now = document.getElementById("now");
  const appEl = document.getElementById("app");
  const desktopQuery = window.matchMedia("(min-width: 1101px)");
  const phoneQuery = window.matchMedia("(max-width: 700px)");

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

  function hideToast() {
    clearTimeout(toastTimer);
    toast.hidden = true;
  }

  // Aviso breve. Con acciones («Ver», «Deshacer») dura más y se pausa al pasar el mouse o enfocarlo.
  function showToast(message, actions = []) {
    clearTimeout(toastTimer);
    const text = document.createElement("span");
    text.textContent = message;
    toast.replaceChildren(text);
    actions.forEach((action) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "toast-act";
      button.textContent = action.label;
      button.addEventListener("click", () => {
        hideToast();
        Promise.resolve()
          .then(action.run)
          .catch((error) => showToast(error.message));
      });
      toast.append(button);
    });
    toast.classList.toggle("has-actions", actions.length > 0);
    toast.hidden = false;
    toastTimer = setTimeout(hideToast, actions.length ? 6000 : 2400);
  }

  function announce(message) {
    srStatus.textContent = "";
    requestAnimationFrame(() => {
      srStatus.textContent = message;
    });
  }

  function plural(count, one, many) {
    return `${count} ${count === 1 ? one : many}`;
  }

  function trackKey(track) {
    return track.source === "device" ? `d:${track.id}` : `t:${track.id}`;
  }

  function fold(text) {
    return String(text || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
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
      <a class="side-item ${likesOn ? "is-active" : ""}" href="#/likes"><span class="dot heart" aria-hidden="true">♡</span><span>Me gusta</span></a>
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
      const active = link.dataset.nav === (key || "home");
      link.classList.toggle("is-active", active);
      if (active) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
  }

  async function show(builder) {
    view.innerHTML = `<p class="loading">Cargando el catálogo…</p>`;
    try {
      view.innerHTML = await builder();
      view.scrollTop = 0;
      window.scrollTo(0, 0);
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
      <button type="button" class="icon-btn more" data-action="menu" data-id="${id}" data-playlist="${options.playlistId || ""}" aria-label="Más opciones para ${esc(track.title)}" aria-haspopup="menu">···</button>
      ${options.sortable ? `<span class="sort-btns">
        <button type="button" class="icon-btn" data-action="move-up" aria-label="Subir ${esc(track.title)}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 14 6-6 6 6"/></svg></button>
        <button type="button" class="icon-btn" data-action="move-down" aria-label="Bajar ${esc(track.title)}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 10 6 6 6-6"/></svg></button>
      </span>` : ""}
    </div>`;
  }

  const ADD_ICON = `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 8v8M8 12h8"/></svg>`;

  function addButton(kind, id) {
    return `<button type="button" class="btn ghost add-to" data-action="add-collection" data-kind="${kind}" data-id="${esc(id ?? "")}">${ADD_ICON}<span>Agregar a playlist</span></button>`;
  }

  function collectionHead(item, kindLabel, action, id, extra, tools = "") {
    return `<header class="sheet" style="--a:${item.accent_a};--b:${item.accent_b}">
      <img src="${esc(item.cover_url)}" alt="">
      <div>
        <p class="kicker">${esc(kindLabel)}</p>
        <h1>${esc(item.title || item.name)}</h1>
        <p class="sheet-sub">${extra}</p>
        <div class="sheet-actions">
          <button type="button" class="btn solid" data-action="${action}" data-id="${id}">Reproducir</button>
          ${tools}
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
            ${addButton("likes", "")}
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
      ${collectionHead({ ...item, title: item.name }, item.editorial ? "Lista editorial" : "Tu lista", "play-playlist", item.id, `${esc(item.description)}<br>${esc(meta)}`, `${item.tracks.length ? addButton("playlist", item.id) : ""}${!item.editorial && item.tracks.length > 1 ? `<button type="button" class="btn ghost" data-action="sort-playlist" data-id="${item.id}" aria-pressed="false">Ordenar</button>` : ""}`)}
      ${item.tracks.length ? `<div class="tracklist" data-playlist="${item.editorial ? "" : item.id}">${item.tracks.map((track, index) => trackRow(track, index, { album: true, context: "playlist", contextId: item.id, playlistId: item.editorial ? "" : item.id, sortable: !item.editorial })).join("")}</div>` : `<p class="empty">Esta lista está vacía. Agrega canciones con «Agregar a playlist» en el menú ··· de cualquier canción.</p>`}
    `;
  }

  async function renderAlbum(id) {
    const item = await api.album(id);
    remember(item.tracks);
    const meta = `<a href="#/artist/${item.artist_id}">${esc(item.artist)}</a>${item.year ? ` · ${esc(item.year)}` : ""} · ${item.tracks.length} canciones · ${fmt(item.duration_ms)}<br>${esc(item.credit)}`;
    return `
      ${collectionHead(item, "Álbum", "play-album", item.id, meta, item.tracks.length ? addButton("album", item.id) : "")}
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
      <button type="button" class="icon-btn more" data-action="menu" data-id="${track.id}" data-playlist="" aria-label="Más opciones para ${esc(track.title)}" aria-haspopup="menu">···</button>
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
    if (orderTimer) flushOrder();
    closePicker();
    const [name, arg] = parts();
    if (name !== "search" && document.activeElement !== searchInput) searchInput.value = "";
    markNav();
    paintSide().catch(() => {});
    closeMenu();
    closeSide();
    if (!name) return show(renderHome);
    if (name === "audio") return show(renderAudio).then(afterAudio);
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
    if (orderTimer) await flushOrder();
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
    const playLabel = snap.playing ? "Pausar" : "Reproducir";
    const repeatLabel = snap.repeat === "one" ? "Repetir una" : snap.repeat === "all" ? "Repetir lista" : "Repetir";
    [playBtn, now.querySelector("[data-now='play']")].forEach((button) => {
      if (!button) return;
      const icon = snap.playing ? "pause" : "play";
      if (button.dataset.icon !== icon) {
        button.innerHTML = snap.playing ? PAUSE_ICON : PLAY_ICON;
        button.dataset.icon = icon;
      }
      button.setAttribute("aria-label", playLabel);
    });
    [document.getElementById("p-shuffle"), now.querySelector("[data-now='shuffle']")].forEach((button) => {
      button.classList.toggle("is-on", snap.shuffle);
      button.setAttribute("aria-pressed", String(snap.shuffle));
    });
    [document.getElementById("p-repeat"), now.querySelector("[data-now='repeat']")].forEach((button) => {
      button.classList.toggle("is-on", snap.repeat !== "off");
      button.dataset.mode = snap.repeat;
      button.setAttribute("aria-label", repeatLabel);
      button.setAttribute("aria-pressed", String(snap.repeat !== "off"));
    });
    const muteBtn = document.getElementById("p-mute");
    muteBtn.classList.toggle("is-on", snap.muted);
    muteBtn.setAttribute("aria-pressed", String(snap.muted));
    muteBtn.setAttribute("aria-label", snap.muted ? "Quitar silencio" : "Silenciar");
    const nowLike = now.querySelector("[data-now='like']");
    const volume = document.getElementById("p-volume");
    volume.value = String(Math.round((snap.muted ? 0 : snap.volume) * 100));
    paintRange(volume);
    if (!track) {
      title.textContent = "Elige una pista";
      artist.textContent = "";
      cover.removeAttribute("src");
      like.disabled = true;
      nowLike.disabled = true;
      document.getElementById("p-add").disabled = true;
      document.body.classList.remove("has-track");
      document.title = "SpotyOFO";
      return;
    }
    title.textContent = track.title;
    artist.textContent = track.artist;
    cover.src = track.cover_url;
    cover.alt = track.album;
    document.body.classList.add("has-track");
    like.disabled = false;
    nowLike.disabled = false;
    document.getElementById("p-add").disabled = false;
    [like, nowLike].forEach((button) => {
      button.classList.toggle("is-on", Boolean(track.liked));
      button.setAttribute("aria-pressed", String(Boolean(track.liked)));
    });
    document.getElementById("np-open").setAttribute("aria-label", `Abrir ahora suena: ${track.title}`);
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
    const ratio = time.duration ? Math.min(1, time.current / time.duration) : 0;
    document.getElementById("p-mini-bar").style.transform = `scaleX(${ratio})`;
    const elapsed = fmt(time.current * 1000);
    const duration = fmt((time.duration || 0) * 1000);
    if (!seeking) {
      const seek = document.getElementById("p-seek");
      seek.value = String(Math.round(ratio * 1000));
      seek.setAttribute("aria-valuetext", `${elapsed} de ${duration}`);
      paintRange(seek);
      document.getElementById("p-elapsed").textContent = elapsed;
      document.getElementById("p-duration").textContent = duration;
    }
    if (!nowSeeking && !now.hidden) {
      const nowSeek = document.getElementById("now-seek");
      nowSeek.value = String(Math.round(ratio * 1000));
      nowSeek.setAttribute("aria-valuetext", `${elapsed} de ${duration}`);
      paintRange(nowSeek);
      document.getElementById("now-elapsed").textContent = elapsed;
      document.getElementById("now-duration").textContent = duration;
    }
  }

  function paintRange(input) {
    const min = Number(input.min) || 0;
    const max = Number(input.max) || 100;
    const pct = Math.max(0, Math.min(100, ((Number(input.value) - min) / (max - min || 1)) * 100));
    input.style.background = `linear-gradient(90deg, var(--aqua) ${pct}%, rgba(255,255,255,.16) ${pct}%)`;
  }

  function openDrawer(mode) {
    drawerMode = drawer.hidden || drawerMode !== mode ? mode : null;
    if (!drawerMode) {
      drawer.hidden = true;
      appEl.classList.remove("with-drawer");
      document.getElementById("p-queue").setAttribute("aria-expanded", "false");
      return;
    }
    drawer.hidden = false;
    appEl.classList.add("with-drawer");
    document.getElementById("p-queue").setAttribute("aria-expanded", "true");
    drawerTitle.textContent = "Cola";
    renderQueue(player.snapshot());
    document.getElementById("drawer-close").focus({ preventScroll: true });
  }

  function renderQueue(snap) {
    const autoplay = `<label class="check"><input type="checkbox" id="autoplay" ${snap.autoplay ? "checked" : ""}> Al terminar, seguir con pistas parecidas</label>`;
    if (!snap.queue.length) {
      drawerBody.innerHTML = `<p class="empty">La cola está vacía. Reproduce un disco o usa «Agregar a la cola».</p>${autoplay}`;
      return;
    }
    drawerBody.innerHTML = `${autoplay}<ol class="queue">${snap.queue.map((track, index) => `
      <li class="${index === snap.index ? "is-current" : ""}">
        <button type="button" data-action="queue-play" data-index="${index}" ${index === snap.index ? 'aria-current="true"' : ""}>
          <img src="${esc(track.cover_url)}" alt="">
          <span><strong>${esc(track.title)}</strong><small>${esc(track.artist)}</small></span>
        </button>
        <button type="button" class="icon-btn more" data-action="queue-menu" data-index="${index}" aria-label="Más opciones para ${esc(track.title)}" aria-haspopup="menu">···</button>
        ${index === snap.index ? "" : `<button type="button" class="icon-btn" data-action="queue-remove" data-index="${index}" aria-label="Quitar ${esc(track.title)} de la cola">×</button>`}
      </li>`).join("")}</ol>`;
  }

  // ---------- Configuración de audio ----------

  function hz(freq) {
    return freq >= 1000 ? `${freq / 1000} kHz` : `${freq} Hz`;
  }

  function signed(value, unit = "dB") {
    const n = Number(value) || 0;
    return `${n > 0 ? "+" : ""}${n.toFixed(1)} ${unit}`;
  }

  function audioReadout(key, cfg) {
    if (key === "preamp") return signed(cfg.preamp);
    if (key === "crossfade") return cfg.crossfade ? `${cfg.crossfade} s` : "Desactivado";
    if (key === "speed") return `${Number(cfg.speed).toFixed(2)}×`;
    return "";
  }

  function toggleRow(key, label, hint, checked, disabled) {
    return `<label class="set-row switch-row ${disabled ? "is-disabled" : ""}">
      <span class="set-text"><strong>${label}</strong>${hint ? `<small>${hint}</small>` : ""}</span>
      <input type="checkbox" class="switch" data-audio-key="${key}" ${checked ? "checked" : ""} ${disabled ? "disabled" : ""}>
    </label>`;
  }

  function presetChips(cfg) {
    const builtIn = Object.keys(EQ_PRESETS).map((name) => `<button type="button" class="chip ${cfg.preset === name ? "is-on" : ""}" data-audio="preset" data-name="${esc(name)}" aria-pressed="${cfg.preset === name}">${esc(name)}</button>`).join("");
    const custom = Object.keys(cfg.custom).map((name) => `<span class="chip-pair">
        <button type="button" class="chip mine ${cfg.preset === name ? "is-on" : ""}" data-audio="preset" data-name="${esc(name)}" aria-pressed="${cfg.preset === name}">${esc(name)}</button>
        <button type="button" class="chip-x" data-audio="del-preset" data-name="${esc(name)}" aria-label="Borrar ajuste ${esc(name)}">×</button>
      </span>`).join("");
    return builtIn + custom;
  }

  async function renderAudio() {
    const cfg = player.audioSettings();
    const caps = player.capabilities();
    const snap = player.snapshot();
    const fx = caps.effects;
    const off = fx ? "" : "disabled";
    const bands = EQ_FREQS.map((freq, i) => `
      <div class="eq-band">
        <output data-band-readout="${i}">${signed(cfg.eq[i], "")}</output>
        <div class="eq-slot"><input type="range" min="-12" max="12" step="0.5" value="${cfg.eq[i]}" data-band="${i}" aria-label="Banda ${hz(freq)}" aria-valuetext="${signed(cfg.eq[i])}" aria-orientation="vertical" ${off}></div>
        <span>${EQ_LABELS[i]}</span>
      </div>`).join("");
    const outputBlock = caps.sink
      ? `<div class="set-row">
          <span class="set-text"><strong>Salida de audio</strong><small>Parlantes, audífonos o interfaz donde suena SpotyOFO.</small></span>
          <div class="set-control">
            <select id="audio-output" aria-label="Dispositivo de salida"><option value="">Predeterminada del sistema</option></select>
            <button type="button" class="btn ghost small" data-audio="output-access">${caps.selectOutput ? "Elegir…" : "Mostrar nombres"}</button>
          </div>
        </div>`
      : `<p class="fine">Elegir la salida de audio requiere Chrome o Edge en una conexión segura (HTTPS o http://localhost). En esta conexión se usa la salida predeterminada del equipo.</p>`;
    return `
      <section class="audio-page" aria-labelledby="audio-title">
        <header class="page-head">
          <p class="kicker">Ajustes</p>
          <h1 class="hello" id="audio-title">Configuración de audio</h1>
          <p class="lead">Se guarda en este navegador y se aplica al instante.</p>
        </header>

        <div class="set-card">
          <h2>Motor de reproducción</h2>
          <div class="segmented" role="radiogroup" aria-label="Motor de reproducción">
            <label><input type="radio" name="audio-engine" value="auto" ${cfg.engine === "auto" ? "checked" : ""}><span>Automático<small>${caps.mobile ? "Recomendado: sigue sonando con la pantalla apagada" : "Recomendado: Completo en este equipo"}</small></span></label>
            <label><input type="radio" name="audio-engine" value="webaudio" ${cfg.engine === "webaudio" ? "checked" : ""}><span>Completo<small>Ecualizador y efectos</small></span></label>
            <label><input type="radio" name="audio-engine" value="native" ${cfg.engine === "native" ? "checked" : ""}><span>Básico<small>Máxima compatibilidad</small></span></label>
          </div>
          ${caps.mobile && cfg.engine === "webaudio" ? `<p class="notice">En teléfonos, el motor Completo puede detenerse al apagar la pantalla o cambiar de app. Para escuchar en segundo plano usa Automático o Básico.</p>` : ""}
          ${caps.pendingEngine !== caps.engine ? `<p class="notice">El cambio de motor se aplica al recargar. <button type="button" class="btn solid small" data-audio="reload">Recargar ahora</button></p>` : ""}
          ${fx ? "" : `<p class="notice">${cfg.engine === "auto" ? "En este teléfono se usa el motor Básico para que la música siga con la pantalla apagada: ecualizador, normalización y mono no están disponibles." : "Motor básico activo: ecualizador, normalización y mono no están disponibles."}</p>`}
        </div>

        <div class="set-card">
          <div class="set-card-head">
            <h2>Ecualizador de 10 bandas</h2>
            <label class="switch-inline"><span>Activo</span><input type="checkbox" class="switch" data-audio-key="eqOn" ${cfg.eqOn ? "checked" : ""} ${off} aria-label="Ecualizador activo"></label>
          </div>
          <div class="presets" id="eq-presets" role="group" aria-label="Ajustes del ecualizador">${presetChips(cfg)}</div>
          <div class="eq-bands ${cfg.eqOn ? "" : "is-off"}" id="eq-bands">${bands}</div>
          <div class="set-row">
            <span class="set-text"><strong>Preamplificador</strong><small>Baja la ganancia si el ecualizador satura.</small></span>
            <div class="set-control range-control">
              <input type="range" min="-12" max="12" step="0.5" value="${cfg.preamp}" data-audio-key="preamp" aria-label="Preamplificador" ${off}>
              <output data-readout="preamp">${audioReadout("preamp", cfg)}</output>
            </div>
          </div>
          <form class="preset-save" data-audio-form="save-preset">
            <label class="sr-only" for="preset-name">Nombre del ajuste</label>
            <input id="preset-name" name="name" maxlength="24" placeholder="Nombre para guardar este ajuste" autocomplete="off" ${off}>
            <button type="submit" class="btn ghost small" ${off}>Guardar ajuste</button>
          </form>
        </div>

        <div class="set-card">
          <h2>Reproducción</h2>
          <div class="set-row">
            <span class="set-text"><strong>Fundido entre pistas</strong><small>Mezcla el final de una canción con el inicio de la siguiente.</small></span>
            <div class="set-control range-control">
              <input type="range" min="0" max="12" step="1" value="${cfg.crossfade}" data-audio-key="crossfade" aria-label="Fundido entre pistas en segundos" ${caps.crossfade ? "" : "disabled"}>
              <output data-readout="crossfade">${audioReadout("crossfade", cfg)}</output>
            </div>
          </div>
          <div class="set-row">
            <span class="set-text"><strong>Velocidad</strong><small>De 0,5× a 2×.</small></span>
            <div class="set-control range-control">
              <input type="range" min="0.5" max="2" step="0.05" value="${cfg.speed}" data-audio-key="speed" aria-label="Velocidad de reproducción">
              <output data-readout="speed">${audioReadout("speed", cfg)}</output>
            </div>
          </div>
          <div class="presets speed-chips" role="group" aria-label="Velocidades rápidas">
            ${[0.75, 1, 1.25, 1.5].map((v) => `<button type="button" class="chip" data-audio="speed" data-value="${v}">${v}×</button>`).join("")}
          </div>
          ${toggleRow("preservePitch", "Mantener el tono", "Al cambiar la velocidad la voz no suena más aguda ni más grave.", cfg.preservePitch, !caps.pitch)}
          ${toggleRow("normalize", "Normalizar volumen", "Iguala canciones que suenan muy fuerte o muy bajo.", cfg.normalize, !fx)}
          ${toggleRow("mono", "Sonido mono", "Mezcla ambos canales; útil con un solo audífono.", cfg.mono, !fx)}
          ${toggleRow("preload", "Precargar la siguiente pista", "Transiciones sin silencios. Usa algo más de datos.", cfg.preload, false)}
          <label class="set-row switch-row">
            <span class="set-text"><strong>Reproducción automática</strong><small>Al terminar la cola sigue con pistas parecidas.</small></span>
            <input type="checkbox" class="switch" id="autoplay" ${snap.autoplay ? "checked" : ""}>
          </label>
        </div>

        <div class="set-card">
          <h2>Salida</h2>
          ${outputBlock}
        </div>

        <div class="set-card">
          <h2>App</h2>
          <div class="set-row">
            <span class="set-text"><strong>Instalar SpotyOFO</strong><small>${window.isSecureContext ? "Ábrela como una app, con su propio ícono." : "Para instalar se necesita HTTPS; en el teléfono puedes usar «Agregar a pantalla de inicio»."}</small></span>
            <button type="button" class="btn ghost small" data-audio="install" ${installPrompt ? "" : "hidden"}>Instalar</button>
          </div>
          <div class="set-row">
            <span class="set-text"><strong>Restablecer audio</strong><small>Vuelve a los valores de fábrica. Tus ajustes guardados se conservan.</small></span>
            <button type="button" class="btn ghost small danger-text" data-audio="reset">Restablecer</button>
          </div>
        </div>

        <div class="set-card keys-card">
          <h2>Atajos de teclado</h2>
          ${shortcutsHtml()}
        </div>
      </section>`;
  }

  function shortcutsHtml() {
    return `<dl class="shortcuts">${SHORTCUTS.map(([key, what]) => `<div><dt><kbd>${esc(key)}</kbd></dt><dd>${esc(what)}</dd></div>`).join("")}</dl>`;
  }

  function afterAudio() {
    if (parts()[0] !== "audio") return;
    view.querySelectorAll('.audio-page input[type="range"]').forEach(paintRange);
    outputsLoaded = false;
    loadOutputs().catch(() => {});
  }

  async function loadOutputs() {
    const select = view.querySelector("#audio-output");
    if (!select) return;
    const cfg = player.audioSettings();
    const outputs = await player.listOutputs();
    const options = outputs.filter((item) => item.id && item.id !== "default");
    select.innerHTML = `<option value="">Predeterminada del sistema</option>${options.map((item) => `<option value="${esc(item.id)}">${esc(item.label)}</option>`).join("")}`;
    if (cfg.sinkId && !options.some((item) => item.id === cfg.sinkId)) {
      select.insertAdjacentHTML("beforeend", `<option value="${esc(cfg.sinkId)}">Dispositivo guardado</option>`);
    }
    select.value = cfg.sinkId && cfg.sinkId !== "default" ? cfg.sinkId : "";
    outputsLoaded = true;
  }

  function syncAudioView() {
    if (parts()[0] !== "audio" || !view.querySelector(".audio-page")) return;
    const cfg = player.audioSettings();
    const active = document.activeElement;
    view.querySelectorAll("[data-band]").forEach((input) => {
      const i = Number(input.dataset.band);
      if (input !== active) input.value = String(cfg.eq[i]);
      input.setAttribute("aria-valuetext", signed(cfg.eq[i]));
      paintRange(input);
      const out = view.querySelector(`[data-band-readout="${i}"]`);
      if (out) out.textContent = signed(cfg.eq[i], "");
    });
    view.querySelectorAll("[data-audio-key]").forEach((input) => {
      const key = input.dataset.audioKey;
      if (input.type === "checkbox") input.checked = Boolean(cfg[key]);
      else if (input !== active) input.value = String(cfg[key]);
      if (input.type === "range") paintRange(input);
      const out = view.querySelector(`[data-readout="${key}"]`);
      if (out) out.textContent = audioReadout(key, cfg);
    });
    const bandsBox = view.querySelector("#eq-bands");
    if (bandsBox) bandsBox.classList.toggle("is-off", !cfg.eqOn);
    const chips = view.querySelector("#eq-presets");
    if (chips && !chips.contains(active)) chips.innerHTML = presetChips(cfg);
    else if (chips) {
      chips.querySelectorAll("[data-audio='preset']").forEach((chip) => {
        const on = chip.dataset.name === cfg.preset;
        chip.classList.toggle("is-on", on);
        chip.setAttribute("aria-pressed", String(on));
      });
    }
  }

  function onAudioInput(event) {
    const input = event.target;
    if (!view.querySelector(".audio-page")) return;
    if (input.dataset.band != null) {
      player.setBand(Number(input.dataset.band), Number(input.value));
      return;
    }
    const key = input.dataset.audioKey;
    if (!key || input.type === "checkbox") return;
    player.setAudio({ [key]: Number(input.value) });
    paintRange(input);
  }

  async function onAudioChange(event) {
    const input = event.target;
    if (!view.querySelector(".audio-page")) return;
    if (input.name === "audio-engine") {
      player.setEngine(input.value);
      show(renderAudio).then(afterAudio);
      return;
    }
    if (input.id === "autoplay") {
      player.setAutoplay(input.checked);
      return;
    }
    if (input.id === "audio-output") {
      try {
        await player.setOutput(input.value);
        showToast("Salida de audio cambiada");
      } catch (error) {
        showToast(`No se pudo usar esa salida: ${error.message}`);
        loadOutputs().catch(() => {});
      }
      return;
    }
    const key = input.dataset.audioKey;
    if (key && input.type === "checkbox") player.setAudio({ [key]: input.checked });
  }

  async function onAudioClick(event) {
    const target = event.target.closest("[data-audio]");
    if (!target || !view.contains(target)) return;
    const action = target.dataset.audio;
    if (action === "preset") player.applyEqPreset(target.dataset.name);
    if (action === "del-preset") {
      player.deleteCustomPreset(target.dataset.name);
      showToast("Ajuste borrado");
    }
    if (action === "speed") player.setAudio({ speed: Number(target.dataset.value) });
    if (action === "reload") location.reload();
    if (action === "reset") {
      player.resetAudio();
      showToast("Audio restablecido");
    }
    if (action === "install") await promptInstall();
    if (action === "output-access") {
      try {
        await player.requestOutputAccess();
        await loadOutputs();
      } catch (error) {
        showToast(error.name === "NotAllowedError" ? "Permiso denegado por el navegador" : error.message);
      }
    }
  }

  // ---------- Ahora suena (pantalla completa) ----------

  function openNow() {
    if (!player.current() || !now.hidden) return;
    lastFocus = document.activeElement;
    now.hidden = false;
    now.style.transform = "";
    document.body.classList.add("now-open");
    document.getElementById("np-open").setAttribute("aria-expanded", "true");
    document.getElementById("now-close").focus({ preventScroll: true });
  }

  function closeNow() {
    if (now.hidden) return;
    now.hidden = true;
    now.style.transform = "";
    document.body.classList.remove("now-open");
    document.getElementById("np-open").setAttribute("aria-expanded", "false");
    if (lastFocus && document.contains(lastFocus)) lastFocus.focus({ preventScroll: true });
    lastFocus = null;
  }

  function trapFocus(event, container) {
    if (event.key !== "Tab") return;
    const items = [...container.querySelectorAll("button:not([disabled]), input:not([disabled]), select, a[href], [tabindex]:not([tabindex='-1'])")]
      .filter((el) => el.offsetParent !== null);
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function bindNowSwipe() {
    const cover = document.getElementById("now-cover");
    let startX = 0;
    let startY = 0;
    let startT = 0;
    let mode = null;
    let onCover = false;
    now.addEventListener("touchstart", (event) => {
      if (event.touches.length !== 1 || event.target.closest("input, select")) {
        mode = "ignore";
        return;
      }
      const touch = event.touches[0];
      startX = touch.clientX;
      startY = touch.clientY;
      startT = performance.now();
      mode = null;
      onCover = event.target === cover;
    }, { passive: true });
    now.addEventListener("touchmove", (event) => {
      if (mode === "ignore") return;
      const touch = event.touches[0];
      const dx = touch.clientX - startX;
      const dy = touch.clientY - startY;
      if (!mode && Math.hypot(dx, dy) > 12) {
        const body = document.getElementById("now-body");
        if (Math.abs(dy) > Math.abs(dx) && dy > 0 && body.scrollTop <= 0) mode = "close";
        else if (Math.abs(dx) > Math.abs(dy) && onCover) mode = "skip";
        else mode = "ignore";
      }
      if (mode === "close") {
        now.classList.add("is-dragging");
        now.style.transform = `translateY(${Math.max(0, dy)}px)`;
      } else if (mode === "skip") {
        cover.style.transform = `translateX(${dx * 0.6}px) rotate(${dx / 40}deg)`;
      }
    }, { passive: true });
    now.addEventListener("touchend", (event) => {
      const touch = event.changedTouches[0];
      const dx = touch.clientX - startX;
      const dy = touch.clientY - startY;
      const fast = performance.now() - startT < 260;
      now.classList.remove("is-dragging");
      if (mode === "close") {
        if (dy > 120 || (fast && dy > 50)) closeNow();
        else now.style.transform = "";
      } else if (mode === "skip") {
        cover.style.transform = "";
        if (dx < -70 || (fast && dx < -35)) player.next();
        else if (dx > 70 || (fast && dx > 35)) player.previous();
      }
      mode = null;
    });
    now.addEventListener("touchcancel", () => {
      now.classList.remove("is-dragging");
      now.style.transform = "";
      cover.style.transform = "";
      mode = null;
    });
  }

  // ---------- Barra lateral ----------

  function loadUiPrefs() {
    try {
      return JSON.parse(localStorage.getItem(UI_KEY) || "{}") || {};
    } catch {
      return {};
    }
  }

  function saveUiPrefs(patch) {
    try {
      localStorage.setItem(UI_KEY, JSON.stringify({ ...loadUiPrefs(), ...patch }));
    } catch {
      /* almacenamiento lleno o bloqueado */
    }
  }

  function syncSideButtons() {
    const collapsed = document.body.classList.contains("side-collapsed");
    const open = document.body.classList.contains("side-open");
    const toggle = document.getElementById("side-toggle");
    const expanded = desktopQuery.matches ? !collapsed : open;
    toggle.setAttribute("aria-expanded", String(expanded));
    toggle.setAttribute("aria-label", desktopQuery.matches ? (collapsed ? "Expandir menú" : "Contraer menú") : "Cerrar menú");
    document.getElementById("menu-btn").setAttribute("aria-expanded", String(open));
  }

  function openSide() {
    document.body.classList.add("side-open");
    document.getElementById("side-scrim").hidden = false;
    syncSideButtons();
    const first = document.querySelector("#sidebar .nav-link");
    if (first) first.focus({ preventScroll: true });
  }

  function closeSide() {
    if (!document.body.classList.contains("side-open")) return;
    document.body.classList.remove("side-open");
    document.getElementById("side-scrim").hidden = true;
    syncSideButtons();
  }

  function toggleSide() {
    if (desktopQuery.matches) {
      const collapsed = document.body.classList.toggle("side-collapsed");
      saveUiPrefs({ collapsed });
      syncSideButtons();
    } else if (document.body.classList.contains("side-open")) closeSide();
    else openSide();
  }

  // ---------- Instalación (PWA) ----------

  function showInstall(available) {
    document.getElementById("install-app").hidden = !available;
    view.querySelectorAll("[data-audio='install']").forEach((button) => {
      button.hidden = !available;
    });
  }

  async function promptInstall() {
    if (!installPrompt) return;
    const event = installPrompt;
    installPrompt = null;
    event.prompt();
    try {
      await event.userChoice;
    } catch {
      /* cancelado */
    }
    showInstall(false);
  }

  // ---------- Atajos ----------

  function isTyping(target) {
    if (!target || !(target instanceof Element)) return false;
    if (target.isContentEditable) return true;
    return Boolean(target.closest("input, textarea, select, [contenteditable='true']"));
  }

  function showShortcuts() {
    openModal({
      title: "Atajos de teclado",
      body: shortcutsHtml(),
      confirm: "Listo",
      noCancel: true,
      onSubmit: async () => closeModal(),
    });
  }

  function onKey(event) {
    if (!picker.hidden) {
      onPickerKey(event);
      return;
    }
    if (!menu.hidden && onMenuKey(event)) return;
    if (event.key === "Escape") {
      if (!modal.hidden) closeModal();
      else if (!menu.hidden) closeMenu(true);
      else if (!now.hidden) closeNow();
      else if (document.body.classList.contains("side-open")) closeSide();
      else if (!drawer.hidden) openDrawer(drawerMode);
      return;
    }
    if (!now.hidden) trapFocus(event, now);
    if (!modal.hidden) {
      trapFocus(event, modal);
      return;
    }
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
    if (isTyping(event.target)) return;
    const onControl = event.target instanceof Element && event.target.closest("button, a[href], summary, [role='button']");
    const key = event.key;
    if (key === " " || event.code === "Space") {
      if (onControl) return; // el espacio activa el botón enfocado
      event.preventDefault();
      player.toggle();
      return;
    }
    if (key === "ArrowRight" || key === "ArrowLeft") {
      event.preventDefault();
      const forward = key === "ArrowRight";
      if (event.shiftKey) forward ? player.next() : player.previous();
      else player.seekBy(forward ? 5 : -5);
      return;
    }
    if (key === "ArrowUp" || key === "ArrowDown") {
      event.preventDefault();
      const snap = player.snapshot();
      const base = snap.muted ? 0 : snap.volume;
      player.setVolume(Math.min(1, Math.max(0, base + (key === "ArrowUp" ? 0.05 : -0.05))));
      return;
    }
    if (key === "?") {
      event.preventDefault();
      showShortcuts();
      return;
    }
    if (key === "/") {
      event.preventDefault();
      searchInput.focus();
      searchInput.select();
      return;
    }
    const lower = key.length === 1 ? key.toLowerCase() : "";
    if (event.shiftKey && lower !== "") return;
    if (lower === "m") player.toggleMute();
    else if (lower === "s") player.toggleShuffle();
    else if (lower === "r") player.cycleRepeat();
    else if (lower === "n") player.next();
    else if (lower === "p") player.previous();
    else if (lower === "f") (now.hidden ? openNow() : closeNow());
    else if (lower === "l") {
      const track = player.current();
      if (track) toggleLike(track.id).catch((error) => showToast(error.message));
    }
  }

  function closeMenu(restoreFocus = false) {
    if (menu.hidden) return;
    menu.hidden = true;
    menuTrack = null;
    if (restoreFocus && menuOpener && document.contains(menuOpener)) menuOpener.focus({ preventScroll: true });
    menuOpener = null;
  }

  function menuItems() {
    return [...menu.querySelectorAll("[role='menuitem']")];
  }

  // Flechas, Inicio/Fin y Tab dentro del menú ···. Devuelve true si atendió la tecla.
  function onMenuKey(event) {
    const items = menuItems();
    const index = items.indexOf(document.activeElement);
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      items[(index + step + items.length) % items.length]?.focus();
      return true;
    }
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      (event.key === "Home" ? items[0] : items[items.length - 1])?.focus();
      return true;
    }
    if (event.key === "Tab") {
      closeMenu(true);
      return false;
    }
    return false;
  }

  function openMenu(button, track) {
    menuTrack = track;
    menuOpener = button;
    menuPlaylistId = button.dataset.playlist || "";
    const id = esc(track.id);
    const item = (action, label) => `<button type="button" role="menuitem" data-action="${action}" data-id="${id}">${label}</button>`;
    const remove = menuPlaylistId
      ? `<button type="button" role="menuitem" data-action="remove-from-list" data-id="${id}" data-playlist="${esc(menuPlaylistId)}">Quitar de esta playlist</button>`
      : "";
    const libraryLinks = track.source === "device"
      ? `${item("add-to-playlist", `${ADD_ICON}Agregar a playlist`)}
      ${remove}
      <a role="menuitem" href="${equipoHash(track.folder || "")}">Abrir carpeta</a>`
      : `${item("add-to-playlist", `${ADD_ICON}Agregar a playlist`)}
      ${remove}
      ${track.source === "user" ? item("delete-upload", "Eliminar archivo") : ""}
      <a role="menuitem" href="#/album/${track.album_id}">Ir al álbum</a>
      <a role="menuitem" href="#/artist/${track.artist_id}">Ir al artista</a>`;
    menu.innerHTML = `
      ${item("play-now", "Reproducir")}
      ${item("play-next", "Reproducir a continuación")}
      ${item("enqueue", "Agregar a la cola")}
      ${libraryLinks}
    `;
    menu.setAttribute("aria-label", `Opciones de ${track.title}`);
    menu.hidden = false;
    menuItems()[0]?.focus({ preventScroll: true });
    const rect = button.getBoundingClientRect();
    const width = 240;
    const left = Math.min(rect.left, window.innerWidth - width - 12);
    const height = menu.offsetHeight || 280;
    const top = rect.bottom + 6 + height > window.innerHeight - 12 ? rect.top - height - 6 : rect.bottom + 6;
    menu.style.left = `${Math.max(8, left)}px`;
    menu.style.top = `${Math.max(8, top)}px`;
  }

  function openModal({ title, body, confirm, danger, onSubmit, noCancel }) {
    if (modal.hidden) modalFocus = document.activeElement;
    document.getElementById("modal-cancel").hidden = Boolean(noCancel);
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
    const wasOpen = !modal.hidden;
    modal.hidden = true;
    modalSubmit = null;
    if (wasOpen && modalFocus && document.contains(modalFocus)) modalFocus.focus({ preventScroll: true });
    modalFocus = null;
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

  // ---------- Agregar a playlist ----------

  function pickerBody(state) {
    if (state.source) return { source: state.source };
    if (state.paths) return { paths: state.paths };
    return { keys: state.keys };
  }

  // Abre el selector. state: { subtitle, keys | paths | source, single?: pista, exclude?: id de lista }
  async function openPicker(state) {
    closeMenu();
    hideToast();
    if (picker.hidden) pickerFocus = menuOpener && document.contains(menuOpener) ? menuOpener : document.activeElement;
    const token = {};
    pickerState = { ...state, lists: [], token };
    document.getElementById("picker-sub").textContent = state.subtitle;
    pickerSearch.value = "";
    pickerNote.hidden = true;
    pickerCreate.hidden = true;
    pickerNew.hidden = false;
    pickerList.innerHTML = `<li class="picker-empty">Cargando tus playlists…</li>`;
    picker.hidden = false;
    document.body.classList.add("picker-open");
    requestAnimationFrame(() => picker.classList.add("is-open"));
    if (coarseQuery.matches) pickerNew.focus({ preventScroll: true });
    else pickerSearch.focus({ preventScroll: true });
    try {
      const params = {};
      if (state.single?.source === "device") params.path = state.single.id;
      else if (state.single) params.track_id = state.single.id;
      const lists = await api.myPlaylists(params);
      if (!pickerState || pickerState.token !== token) return;
      pickerState.lists = lists.filter((list) => String(list.id) !== String(state.exclude ?? ""));
      paintPicker();
    } catch (error) {
      if (pickerState?.token === token) pickerList.innerHTML = `<li class="picker-empty">${esc(error.message)}</li>`;
    }
  }

  function paintPicker() {
    if (!pickerState) return;
    const raw = pickerSearch.value.trim();
    const query = fold(raw);
    const all = pickerState.lists;
    const lists = all.filter((list) => !query || fold(list.name).includes(query));
    if (!all.length) {
      pickerList.innerHTML = `<li class="picker-empty">Aún no tienes playlists. Crea la primera con «Nueva playlist».</li>`;
      return;
    }
    if (!lists.length) {
      pickerList.innerHTML = `<li class="picker-empty">Ninguna playlist se llama «${esc(raw)}».</li>`;
      return;
    }
    pickerList.innerHTML = lists.map((list) => {
      const has = Boolean(pickerState.single && list.contains);
      return `<li>
        <button type="button" class="picker-item ${has ? "has-it" : ""}" data-pick="${list.id}">
          <img src="${esc(list.cover_url)}" alt="" loading="lazy">
          <span class="picker-text">
            <strong>${esc(list.name)}</strong>
            <small>${plural(list.track_count, "canción", "canciones")}${has ? " · Ya está aquí" : ""}</small>
          </span>
          ${has ? `<span class="picker-check" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="m5 12 5 5 9-10"/></svg></span>` : ""}
        </button>
      </li>`;
    }).join("");
  }

  function closePicker() {
    if (picker.hidden) return;
    picker.classList.remove("is-open");
    picker.hidden = true;
    document.body.classList.remove("picker-open");
    pickerState = null;
    const back = pickerFocus;
    pickerFocus = null;
    if (back && document.contains(back) && back.offsetParent !== null) back.focus({ preventScroll: true });
  }

  function pickerBusy(on) {
    picker.classList.toggle("is-busy", on);
    picker.querySelector(".picker").setAttribute("aria-busy", String(on));
    picker.querySelectorAll(".picker-item, .picker-new, .picker-create button").forEach((button) => {
      button.disabled = on;
    });
  }

  function defaultPlaylistName() {
    const names = new Set((pickerState?.lists || []).map((list) => fold(list.name)));
    let number = (pickerState?.lists.length || 0) + 1;
    while (names.has(fold(`Mi playlist n.º ${number}`))) number += 1;
    return `Mi playlist n.º ${number}`;
  }

  function startCreate() {
    if (!pickerState) return;
    pickerNew.hidden = true;
    pickerCreate.hidden = false;
    pickerNote.hidden = true;
    const input = pickerCreate.elements.name;
    input.value = defaultPlaylistName();
    input.focus();
    input.select();
  }

  function cancelCreate() {
    pickerCreate.hidden = true;
    pickerNew.hidden = false;
    pickerNew.focus();
  }

  async function submitCreate() {
    const state = pickerState;
    if (!state || picker.classList.contains("is-busy")) return;
    const input = pickerCreate.elements.name;
    const name = input.value.trim();
    if (!name) {
      input.focus();
      return;
    }
    pickerBusy(true);
    try {
      const saved = await api.createPlaylist({ name, ...pickerBody(state) });
      closePicker();
      await paintSide();
      addedToast(saved.result, { created: true });
      refreshIfShowing(saved.id);
    } catch (error) {
      showToast(error.message);
    } finally {
      pickerBusy(false);
    }
  }

  async function pickPlaylist(id) {
    const state = pickerState;
    if (!state || picker.classList.contains("is-busy")) return;
    const list = state.lists.find((item) => String(item.id) === String(id));
    if (!list) return;
    if (state.single && list.contains) {
      pickerNote.innerHTML = `<p>«${esc(state.single.title)}» ya está en «${esc(list.name)}». Una playlist no repite canciones.</p>
        <div class="picker-note-actions">
          <button type="button" class="btn ghost small" data-note="skip">Omitir</button>
          <button type="button" class="btn solid small" data-note="view" data-id="${list.id}">Ver playlist</button>
        </div>`;
      pickerNote.hidden = false;
      pickerNote.querySelector("[data-note='skip']").focus();
      return;
    }
    pickerBusy(true);
    try {
      const result = await api.addToPlaylist(list.id, pickerBody(state));
      closePicker();
      addedToast(result);
      refreshIfShowing(result.playlist_id);
    } catch (error) {
      showToast(error.message);
    } finally {
      pickerBusy(false);
    }
  }

  function addedToast(result, options = {}) {
    const name = `«${result.playlist_name}»`;
    const see = { label: "Ver", run: () => go(`#/playlist/${result.playlist_id}`) };
    if (!result.added && !options.created) {
      showToast(result.skipped > 1 ? `Ya estaban todas en ${name}` : `Ya estaba en ${name}`, [see]);
      return;
    }
    const undo = {
      label: "Deshacer",
      run: async () => {
        if (options.created) {
          await api.deletePlaylist(result.playlist_id);
          await paintSide();
          const [page, pageId] = parts();
          if (page === "playlist" && String(pageId) === String(result.playlist_id)) go("#/library");
          else refreshIfShowing(result.playlist_id);
          showToast("Listo, se deshizo");
          return;
        }
        await api.removeFromPlaylist(result.playlist_id, result.added_keys);
        refreshIfShowing(result.playlist_id);
        showToast("Listo, se deshizo");
      },
    };
    let text;
    if (options.created) text = result.added > 1 ? `Playlist ${name} creada con ${result.added} canciones` : `Agregada a ${name}`;
    else text = result.added === 1 ? `Agregada a ${name}` : `${result.added} canciones agregadas a ${name}`;
    if (result.skipped) text += ` · ${result.skipped} ya ${result.skipped === 1 ? "estaba" : "estaban"}`;
    showToast(text, [undo, see]);
  }

  // Vuelve a pintar la vista si muestra esa lista (o la biblioteca), sin saltar al inicio.
  async function refreshIfShowing(playlistId) {
    const [page, pageId] = parts();
    if (!((page === "playlist" && String(pageId) === String(playlistId)) || page === "library")) return;
    const y = window.scrollY;
    const inner = view.scrollTop;
    await route();
    view.scrollTop = inner;
    window.scrollTo(0, y);
  }

  function addTrackToPlaylist(track) {
    if (!track) return;
    remember([track]);
    return openPicker({
      subtitle: track.artist ? `«${track.title}» · ${track.artist}` : `«${track.title}»`,
      keys: [trackKey(track)],
      single: track,
    });
  }

  async function addCollection(button) {
    const kind = button.dataset.kind;
    const rows = [...view.querySelectorAll(".tracklist .track")];
    if (!rows.length) {
      showToast("No hay canciones para agregar");
      return;
    }
    const title = view.querySelector(".sheet h1")?.textContent.trim() || "";
    const subtitle = `${plural(rows.length, "canción", "canciones")} de «${title}»`;
    if (kind === "likes") {
      const keys = rows.map((row) => trackFromRow(row)).filter(Boolean).map(trackKey);
      await openPicker({ subtitle, keys });
      return;
    }
    const id = Number(button.dataset.id);
    await openPicker({ subtitle, source: { type: kind, id }, exclude: kind === "playlist" ? id : null });
  }

  function onPickerKey(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      if (!pickerCreate.hidden) cancelCreate();
      else if (!pickerNote.hidden) {
        pickerNote.hidden = true;
        pickerList.querySelector(".picker-item")?.focus();
      } else closePicker();
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      const items = [pickerNew, ...pickerList.querySelectorAll(".picker-item")].filter((el) => !el.hidden && el.offsetParent !== null);
      if (!items.length) return;
      const current = items.indexOf(document.activeElement);
      if (document.activeElement === pickerSearch && event.key === "ArrowDown") {
        event.preventDefault();
        items[0].focus();
        return;
      }
      if (current === -1) return;
      event.preventDefault();
      const next = current + (event.key === "ArrowDown" ? 1 : -1);
      if (next < 0) pickerSearch.focus();
      else items[Math.min(next, items.length - 1)].focus();
      return;
    }
    trapFocus(event, picker);
  }

  // ---------- Ordenar una lista propia ----------

  function renumber(list) {
    [...list.querySelectorAll(".track")].forEach((row, index) => {
      const num = row.querySelector(".num");
      if (num) num.textContent = String(index + 1);
      row.querySelectorAll("[data-index]").forEach((el) => {
        el.dataset.index = String(index);
      });
    });
  }

  function toggleSorting(button) {
    const list = view.querySelector(".tracklist");
    if (!list) return;
    const on = !list.classList.contains("is-sorting");
    list.classList.toggle("is-sorting", on);
    button.setAttribute("aria-pressed", String(on));
    button.textContent = on ? "Listo" : "Ordenar";
    button.classList.toggle("solid", on);
    button.classList.toggle("ghost", !on);
    if (on) announce("Modo ordenar: usa las flechas de cada canción para subirla o bajarla.");
    else {
      if (orderTimer) flushOrder();
      announce("Orden guardado.");
    }
  }

  function moveRow(button, delta) {
    const row = button.closest(".track");
    const list = row?.parentElement;
    if (!row || !list) return;
    const sibling = delta < 0 ? row.previousElementSibling : row.nextElementSibling;
    if (!sibling) {
      announce(delta < 0 ? "Ya está primera." : "Ya está última.");
      return;
    }
    if (delta < 0) list.insertBefore(row, sibling);
    else list.insertBefore(sibling, row);
    renumber(list);
    button.focus({ preventScroll: true });
    row.scrollIntoView({ block: "nearest" });
    const rows = [...list.querySelectorAll(".track")];
    announce(`${trackFromRow(row)?.title || "Canción"}: posición ${rows.indexOf(row) + 1} de ${rows.length}`);
    orderPlaylist = list.dataset.playlist;
    clearTimeout(orderTimer);
    orderTimer = setTimeout(() => flushOrder(), 700);
  }

  async function flushOrder() {
    clearTimeout(orderTimer);
    orderTimer = 0;
    const id = orderPlaylist;
    orderPlaylist = null;
    const list = view.querySelector(`.tracklist[data-playlist="${id}"]`);
    if (!id || !list) return;
    const keys = [...list.querySelectorAll(".track")].map((row) => trackFromRow(row)).filter(Boolean).map(trackKey);
    try {
      await api.reorderPlaylist(id, keys);
    } catch (error) {
      showToast(error.message);
      refreshIfShowing(id);
    }
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
      const count = Number(target.dataset.count || 0);
      await openPicker({
        subtitle: `${plural(count, "pista", "pistas")} de «${target.dataset.name}»`,
        paths: [target.dataset.path],
      });
      return;
    }
    if (action === "add-collection") {
      await addCollection(target);
      return;
    }
    if (action === "sort-playlist") {
      toggleSorting(target);
      return;
    }
    if (action === "move-up" || action === "move-down") {
      moveRow(target, action === "move-up" ? -1 : 1);
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
      if (!menu.hidden && !menu.contains(event.target) && !event.target.closest("[data-action='menu'], [data-action='queue-menu']")) closeMenu();
    });
    menu.addEventListener("click", async (event) => {
      const target = event.target.closest("[data-action], a");
      if (!target) return;
      const action = target.dataset.action;
      const track = menuTrack;
      const opener = menuOpener;
      closeMenu(!target.matches("a"));
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
        menuOpener = opener;
        await addTrackToPlaylist(track);
      }
      if (action === "remove-from-list") {
        const playlistId = target.dataset.playlist;
        const key = trackKey(track);
        const result = await api.removeFromPlaylist(playlistId, [key]);
        await refreshIfShowing(playlistId);
        showToast(`Quitada de «${result.playlist_name}»`, [{
          label: "Deshacer",
          run: async () => {
            await api.addToPlaylist(playlistId, result.position == null ? { keys: [key] } : { keys: [key], position: result.position });
            await refreshIfShowing(playlistId);
            showToast(`Volvió a «${result.playlist_name}»`);
          },
        }]);
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
    modal.addEventListener("click", (event) => {
      if (event.target === modal) closeModal();
    });
    picker.addEventListener("click", (event) => {
      if (event.target === picker) {
        closePicker();
        return;
      }
      const pick = event.target.closest("[data-pick]");
      if (pick) {
        pickPlaylist(pick.dataset.pick);
        return;
      }
      const note = event.target.closest("[data-note]");
      if (note?.dataset.note === "skip") {
        pickerNote.hidden = true;
        pickerList.querySelector(".picker-item")?.focus();
      }
      if (note?.dataset.note === "view") {
        const id = note.dataset.id;
        closePicker();
        go(`#/playlist/${id}`);
      }
    });
    document.getElementById("picker-close").addEventListener("click", closePicker);
    pickerNew.addEventListener("click", startCreate);
    document.getElementById("picker-create-cancel").addEventListener("click", cancelCreate);
    pickerCreate.addEventListener("submit", (event) => {
      event.preventDefault();
      submitCreate();
    });
    pickerSearch.addEventListener("input", paintPicker);
    pickerSearch.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      const only = pickerList.querySelectorAll(".picker-item");
      if (only.length === 1) only[0].click();
    });
    toast.addEventListener("pointerenter", () => clearTimeout(toastTimer));
    toast.addEventListener("focusin", () => clearTimeout(toastTimer));
    toast.addEventListener("pointerleave", () => {
      if (!toast.hidden) toastTimer = setTimeout(hideToast, 3000);
    });
    toast.addEventListener("focusout", () => {
      if (!toast.hidden) toastTimer = setTimeout(hideToast, 3000);
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
    document.getElementById("p-add").addEventListener("click", () => {
      const track = player.current();
      if (track) addTrackToPlaylist(track).catch((error) => showToast(error.message));
    });
    document.getElementById("p-like").addEventListener("click", () => {
      const track = player.current();
      if (track) toggleLike(track.id).catch((error) => showToast(error.message));
    });
    document.getElementById("p-title").addEventListener("click", () => {
      const track = player.current();
      if (!track) return;
      if (phoneQuery.matches) {
        openNow();
        return;
      }
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
    document.getElementById("np-open").addEventListener("click", openNow);
    document.getElementById("np-open").setAttribute("aria-haspopup", "dialog");
    document.getElementById("now-close").addEventListener("click", closeNow);
    document.getElementById("p-queue").addEventListener("click", () => openDrawer("queue"));
    document.getElementById("p-queue").setAttribute("aria-controls", "drawer");
    document.getElementById("p-studio").addEventListener("click", () => go("#/audio"));
    document.getElementById("drawer-close").addEventListener("click", () => openDrawer(drawerMode));
    now.addEventListener("click", (event) => {
      const button = event.target.closest("[data-now]");
      if (!button) return;
      const action = button.dataset.now;
      if (action === "play") player.toggle();
      if (action === "prev") player.previous();
      if (action === "next") player.next();
      if (action === "shuffle") player.toggleShuffle();
      if (action === "repeat") player.cycleRepeat();
      if (action === "like") {
        const track = player.current();
        if (track) toggleLike(track.id).catch((error) => showToast(error.message));
      }
      if (action === "add") {
        const track = player.current();
        if (track) addTrackToPlaylist(track).catch((error) => showToast(error.message));
      }
      if (action === "queue") {
        closeNow();
        if (drawer.hidden || drawerMode !== "queue") openDrawer("queue");
      }
      if (action === "audio") {
        closeNow();
        go("#/audio");
      }
    });
    const nowSeek = document.getElementById("now-seek");
    nowSeek.addEventListener("pointerdown", () => {
      nowSeeking = true;
    });
    nowSeek.addEventListener("input", () => {
      nowSeeking = true;
      paintRange(nowSeek);
    });
    nowSeek.addEventListener("change", () => {
      player.seekRatio(Number(nowSeek.value) / 1000);
      nowSeeking = false;
    });
    bindNowSwipe();
    view.addEventListener("input", onAudioInput);
    view.addEventListener("change", (event) => {
      onAudioChange(event).catch((error) => showToast(error.message));
    });
    view.addEventListener("click", (event) => {
      onAudioClick(event).catch((error) => showToast(error.message));
    });
    view.addEventListener("submit", (event) => {
      const form = event.target.closest("[data-audio-form='save-preset']");
      if (!form) return;
      event.preventDefault();
      try {
        player.saveCustomPreset(form.elements.name.value);
        form.reset();
        showToast("Ajuste guardado");
      } catch (error) {
        showToast(error.message);
      }
    });
    drawerBody.addEventListener("click", (event) => {
      const play = event.target.closest("[data-action='queue-play']");
      if (play) player.playQueueIndex(Number(play.dataset.index));
      const remove = event.target.closest("[data-action='queue-remove']");
      if (remove) player.removeQueued(Number(remove.dataset.index));
      const more = event.target.closest("[data-action='queue-menu']");
      if (more) {
        const track = player.snapshot().queue[Number(more.dataset.index)];
        if (track) {
          remember([track]);
          openMenu(more, track);
        }
      }
    });
    drawerBody.addEventListener("change", (event) => {
      if (event.target.id === "autoplay") player.setAutoplay(event.target.checked);
    });
    const seek = document.getElementById("p-seek");
    seek.addEventListener("pointerdown", () => {
      seeking = true;
    });
    seek.addEventListener("input", () => {
      seeking = true;
      paintRange(seek);
    });
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
    window.addEventListener("keydown", onKey);
    document.getElementById("keys-help").addEventListener("click", showShortcuts);
    document.getElementById("side-toggle").addEventListener("click", toggleSide);
    document.getElementById("menu-btn").addEventListener("click", toggleSide);
    document.getElementById("side-scrim").addEventListener("click", closeSide);
    document.getElementById("sidebar").addEventListener("click", (event) => {
      if (event.target.closest("a[href]")) closeSide();
    });
    if (loadUiPrefs().collapsed) document.body.classList.add("side-collapsed");
    desktopQuery.addEventListener("change", () => {
      closeSide();
      syncSideButtons();
    });
    syncSideButtons();
    window.addEventListener("beforeinstallprompt", (event) => {
      event.preventDefault();
      installPrompt = event;
      showInstall(true);
    });
    window.addEventListener("appinstalled", () => {
      installPrompt = null;
      showInstall(false);
      showToast("SpotyOFO instalada");
    });
    document.getElementById("install-app").addEventListener("click", () => promptInstall());
    player.on("audio", syncAudioView);
    player.on("notice", (message) => showToast(message));
    player.on("error", (error) => {
      if (error?.name === "NotAllowedError") showToast("Toca reproducir para empezar");
      else showToast(error?.message || "Error de reproducción");
    });
    if (navigator.mediaDevices && typeof navigator.mediaDevices.addEventListener === "function") {
      navigator.mediaDevices.addEventListener("devicechange", () => {
        if (outputsLoaded) loadOutputs().catch(() => {});
      });
    }
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
