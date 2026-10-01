import { api, onSessionExpired, setCsrf } from "./api.js?v=20260930f";
import { EQ_FREQS, EQ_LABELS, EQ_PRESETS } from "./player.js?v=20260930f";

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
  let genreState = null; // vista de género: { item, tracks, sort, filter }
  let genresOrder = "count";
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
  let me = null;
  let pendingTimer = 0;
  const userBtn = document.getElementById("user-btn");
  const userMenu = document.getElementById("user-menu");
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

  // Duración larga legible: «42 min», «3 h 12 min».
  function longDuration(ms) {
    const minutes = Math.max(0, Math.round((ms || 0) / 60000));
    if (minutes < 60) return `${minutes} min`;
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;
    return rest ? `${hours} h ${rest} min` : `${hours} h`;
  }

  function genreInitials(name) {
    const words = String(name || "").split(/\s+/).filter(Boolean);
    if (words.length > 1) return (words[0][0] + words[1][0]).toUpperCase();
    return (words[0] || "?").slice(0, 2).replace(/^./, (ch) => ch.toUpperCase());
  }

  function genreStyle(item) {
    return `--a:${esc(item.accent_a)};--b:${esc(item.accent_b)}`;
  }

  // Tamaño del texto según la palabra más larga, para no cortar «Alternativa» a la mitad.
  function genreFit(name) {
    const longest = Math.max(0, ...String(name || "").split(/\s+/).map((word) => word.length));
    if (longest >= 14) return "fit-xs";
    if (longest >= 10) return "fit-s";
    return "";
  }

  function genreTile(item) {
    return `<a class="genre ${genreFit(item.name)}" href="#/genre/${esc(item.slug)}" style="${genreStyle(item)}">
      <span class="genre-name">${esc(item.name)}</span>
      <small>${plural(item.count, "canción", "canciones")}</small>
    </a>`;
  }

  const SIDE_GENRES_KEY = "spotyofo-side-genres";
  const SIDE_GENRES_MAX = 12;

  function sideGenresOpen() {
    try {
      return localStorage.getItem(SIDE_GENRES_KEY) !== "0";
    } catch {
      return true;
    }
  }

  function sideGenres(list, activeSlug) {
    if (!list.length) return "";
    const open = sideGenresOpen();
    const named = list.filter((item) => item.slug !== "sin-genero").length;
    const shown = list.slice(0, SIDE_GENRES_MAX);
    const more = list.length > shown.length;
    return `<div class="side-group">
      <button type="button" class="side-fold" id="genre-fold" aria-expanded="${open}" aria-controls="side-genres" title="Géneros">
        <span class="side-fold-label">Géneros</span>
        <span class="side-fold-count" aria-label="${plural(named, "género", "géneros")}">${named}</span>
        <svg class="chev" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>
      </button>
      <div class="side-genres" id="side-genres" ${open ? "" : "hidden"}>
        ${shown.map((item) => `<a class="side-item genre-item ${item.slug === activeSlug ? "is-active" : ""}" href="#/genre/${esc(item.slug)}" title="${esc(item.name)} · ${plural(item.count, "canción", "canciones")}"${item.slug === activeSlug ? ' aria-current="page"' : ""}>
          <span class="dot gdot" style="${genreStyle(item)}" aria-hidden="true">${esc(genreInitials(item.name))}</span>
          <span class="side-text"><span class="side-name">${esc(item.name)}</span><small>${plural(item.count, "canción", "canciones")}</small></span>
        </a>`).join("")}
        ${more ? `<a class="side-item side-more" href="#/genres"><span class="dot more-dot" aria-hidden="true">···</span><span>Ver los ${list.length} géneros</span></a>` : ""}
      </div>
    </div>`;
  }

  function toggleSideGenres() {
    const fold = document.getElementById("genre-fold");
    const list = document.getElementById("side-genres");
    if (!fold || !list) return;
    const open = fold.getAttribute("aria-expanded") !== "true";
    fold.setAttribute("aria-expanded", String(open));
    list.hidden = !open;
    try {
      localStorage.setItem(SIDE_GENRES_KEY, open ? "1" : "0");
    } catch {
      /* almacenamiento bloqueado */
    }
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
    const [lists, genreData] = await Promise.all([api.playlists(), api.genres().catch(() => ({ genres: [] }))]);
    const mine = lists.filter((item) => !item.editorial);
    const editorial = lists.filter((item) => item.editorial);
    const current = parts();
    const activeId = current[0] === "playlist" ? current[1] : "";
    const activeGenre = current[0] === "genre" ? decodeURIComponent(current[1] || "") : "";
    const likesOn = current[0] === "likes";
    side.innerHTML = `
      <a class="side-item ${likesOn ? "is-active" : ""}" href="#/likes"><span class="dot heart" aria-hidden="true">♡</span><span>Me gusta</span></a>
      ${mine.map((item) => sideItem(item, activeId)).join("")}
      ${editorial.length ? `<p class="side-label">Editorial</p>` : ""}
      ${editorial.map((item) => sideItem(item, activeId)).join("")}
      ${sideGenres(genreData.genres || [], activeGenre)}
    `;
  }

  function sideItem(item, activeId) {
    return `<a class="side-item ${String(item.id) === activeId ? "is-active" : ""}" href="#/playlist/${item.id}">
      ${coverImg(item.cover_url, { size: 36, lazy: true })}
      <span>${esc(item.name)}</span>
    </a>`;
  }

  function markNav() {
    const name = parts()[0] || "home";
    const key = name === "likes" || name === "playlist" ? "library" : name === "genre" || name === "genres" ? "search" : name;
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
      eagerCovers = EAGER_COVERS;
      view.innerHTML = await builder();
      eagerCovers = 0;
      view.scrollTop = 0;
      window.scrollTo(0, 0);
      markPlaying();
    } catch (error) {
      view.innerHTML = `<p class="empty">${esc(error.message)}</p>`;
    }
  }

  // Portadas: las primeras de cada pantalla se piden de inmediato (las que se ven al abrir);
  // el resto solo cuando se acercan a la vista, así Inicio no pide cientos de imágenes a la vez.
  // Con tamaño fijo (size) se declara width/height para que el diseño no salte al llegar la imagen.
  const EAGER_COVERS = 8;
  let eagerCovers = 0;
  function coverImg(src, opts = {}) {
    const eager = !opts.lazy && (opts.high || eagerCovers > 0);
    if (eager && !opts.high) eagerCovers -= 1;
    const dims = opts.size ? ` width="${opts.size}" height="${opts.size}"` : "";
    return `<img src="${esc(src)}" alt=""${dims} decoding="async" loading="${eager ? "eager" : "lazy"}"${opts.high ? ' fetchpriority="high"' : ""}>`;
  }

  function card(href, cover, title, subtitle, action, id) {
    return `<article class="card">
      <div class="art">
        <a href="${href}">${coverImg(cover)}</a>
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
      ${coverImg(item.cover_url, { high: true })}
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
        ${coverImg(featured.cover_url, { high: true })}
        <div>
          <p class="kicker">Lista editorial</p>
          <h2>${esc(featured.name)}</h2>
          <p>${esc(featured.description)}</p>
          <button type="button" class="btn solid" data-action="play-playlist" data-id="${featured.id}">Reproducir</button>
        </div>
      </section>` : ""}
      ${data.recent.length ? rowSection("Escuchado por ti", data.recent.map((track, index) => trackRow(track, index, { album: true, context: "recent", contextId: "recent" })).join(""), true) : ""}
      ${data.popular.length ? rowSection("Lo que más sonó aquí", data.popular.map((track, index) => trackRow(track, index, { album: true })).join(""), true) : ""}
      ${data.playlists.length ? section("Listas", data.playlists.map((item) => card(`#/playlist/${item.id}`, item.cover_url, item.name, item.editorial ? "Editorial" : "Tu lista", "play-playlist", item.id)).join("")) : ""}
      ${data.albums.length ? section("Álbumes", data.albums.map((item) => card(`#/album/${item.id}`, item.cover_url, item.title, albumSub(item), "play-album", item.id)).join("")) : ""}
      ${data.artists.length ? section("Artistas", data.artists.map((item) => card(`#/artist/${item.id}`, item.cover_url, item.name, `${item.genre} · ${item.origin}`, "play-artist", item.id)).join("")) : ""}
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
      const list = data.genres || [];
      return `<h1 class="hello">Buscar</h1><p class="lead">Escribe un nombre o elige un género.</p>
        ${list.length ? `<section class="block"><div class="block-head"><h2>Explorar por género</h2>${list.length > 1 ? `<a class="head-link" href="#/genres">Ver todos</a>` : ""}</div><div class="genre-grid">${list.map(genreTile).join("")}</div></section>` : `<p class="empty">Todavía no hay canciones con género.</p>`}`;
    }
    const empty = !data.tracks.length && !data.albums.length && !data.artists.length && !data.playlists.length;
    if (empty) return `<h1 class="hello">Sin resultados</h1><p class="lead">No hay coincidencias para «${esc(query)}».</p>`;
    const songCount = data.track_total || data.tracks.length;
    const songLabel = `${songCount} ${songCount === 1 ? "canción" : "canciones"}`;
    return `
      <div class="results-head">
        <div>
          <h1 class="hello">Resultados</h1>
          <p class="lead">«${esc(query)}»${data.tracks.length ? ` · ${songLabel}` : ""}</p>
        </div>
        ${data.tracks.length ? `<button type="button" class="btn solid" data-action="play-search" data-query="${esc(query)}">Reproducir todos</button>` : ""}
      </div>
      ${data.tracks.length ? `<section class="block"><div class="block-head"><h2>Canciones</h2></div><div class="tracklist">${data.tracks.map((track, index) => trackRow(track, index, { album: true, context: "search", contextId: query })).join("")}</div></section>` : ""}
      ${data.artists.length ? section("Artistas", data.artists.map((item) => card(`#/artist/${item.id}`, item.cover_url, item.name, `${item.genre} · ${item.origin}`, "play-artist", item.id)).join("")) : ""}
      ${data.albums.length ? section("Álbumes", data.albums.map((item) => card(`#/album/${item.id}`, item.cover_url, item.title, albumSub(item), "play-album", item.id)).join("")) : ""}
      ${data.playlists.length ? section("Listas", data.playlists.map((item) => card(`#/playlist/${item.id}`, item.cover_url, item.name, item.editorial ? "Editorial" : "Tu lista", "play-playlist", item.id)).join("")) : ""}
    `;
  }

  async function renderLibrary() {
    const [lists, likes, genreData] = await Promise.all([api.playlists(), api.likes(), api.genres().catch(() => ({ genres: [] }))]);
    remember(likes);
    const genreCount = (genreData.genres || []).filter((item) => item.slug !== "sin-genero").length;
    const mine = lists.filter((item) => !item.editorial);
    const editorial = lists.filter((item) => item.editorial);
    return `
      <h1 class="hello">Biblioteca</h1>
      <p class="lead">${isDemo() ? "Esta es una cuenta demo compartida: lo que hagas aquí lo ven otros visitantes y se reinicia cada noche." : "Tus Me gusta y tus listas son solo tuyos: nadie más los ve."}</p>
      <div class="library-grid">
        <a class="lib-card likes" href="#/likes"><strong>Me gusta</strong><span>${likes.length} ${likes.length === 1 ? "canción" : "canciones"}</span></a>
        ${genreCount ? `<a class="lib-card genres" href="#/genres"><strong>Géneros</strong><span>${plural(genreCount, "género", "géneros")}</span></a>` : ""}
        <button type="button" class="lib-card create" data-action="create-playlist"><strong>Nueva lista</strong><span>Armar una selección</span></button>
      </div>
      ${mine.length ? section("Tus listas", mine.map((item) => card(`#/playlist/${item.id}`, item.cover_url, item.name, item.description || "Lista personal", "play-playlist", item.id)).join("")) : ""}
      ${editorial.length ? section("Editorial", editorial.map((item) => card(`#/playlist/${item.id}`, item.cover_url, item.name, item.description, "play-playlist", item.id)).join("")) : ""}
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

  const GENRE_SORTS = [
    ["artist", "Artista"],
    ["title", "Título"],
    ["album", "Álbum"],
    ["plays", "Más escuchadas"],
    ["recent", "Recién agregadas"],
    ["duration", "Duración"],
  ];
  const GENRE_SORT_KEY = "spotyofo-genre-sort";

  function savedGenreSort() {
    try {
      const value = localStorage.getItem(GENRE_SORT_KEY);
      return GENRE_SORTS.some(([key]) => key === value) ? value : "artist";
    } catch {
      return "artist";
    }
  }

  function sortGenreTracks(tracks, sort) {
    const list = tracks.slice();
    const text = (a, b) => String(a || "").localeCompare(String(b || ""), "es", { sensitivity: "base", numeric: true });
    const byAlbum = (a, b) => text(a.album, b.album) || (a.track_number || 0) - (b.track_number || 0) || a.id - b.id;
    if (sort === "title") list.sort((a, b) => text(a.title, b.title) || text(a.artist, b.artist));
    else if (sort === "album") list.sort(byAlbum);
    else if (sort === "plays") list.sort((a, b) => (b.plays || 0) - (a.plays || 0) || text(a.title, b.title));
    else if (sort === "recent") list.sort((a, b) => b.id - a.id);
    else if (sort === "duration") list.sort((a, b) => (b.duration_ms || 0) - (a.duration_ms || 0));
    return list; // «artist»: el orden del servidor (artista, año del disco, disco, número de pista)
  }

  function genreRows() {
    if (!genreState) return [];
    const query = fold(genreState.filter.trim());
    const sorted = sortGenreTracks(genreState.tracks, genreState.sort);
    if (!query) return sorted;
    return sorted.filter((track) => fold(`${track.title} ${track.artist} ${track.album}`).includes(query));
  }

  function genreListHtml() {
    const rows = genreRows();
    if (!rows.length) return `<p class="empty">Ninguna canción de ${esc(genreState.item.name)} coincide con «${esc(genreState.filter.trim())}».</p>`;
    return `<div class="tracklist">${rows.map((track, index) => trackRow(track, index, { album: true, context: "genre", contextId: genreState.item.slug })).join("")}</div>`;
  }

  function paintGenreList() {
    const box = document.getElementById("genre-tracks");
    if (!box || !genreState) return;
    box.innerHTML = genreListHtml();
    const shown = genreRows().length;
    const note = document.getElementById("genre-count");
    if (note) note.textContent = genreState.filter.trim() ? `${shown} de ${genreState.tracks.length}` : "";
    markPlaying();
  }

  async function renderGenre(slug) {
    const data = await api.genre(slug);
    remember(data.tracks);
    const item = data.genre;
    genreState = { item, tracks: data.tracks, sort: savedGenreSort(), filter: "" };
    const meta = [
      plural(item.count, "canción", "canciones"),
      item.artists ? plural(item.artists, "artista", "artistas") : "",
      longDuration(item.duration_ms),
    ].filter(Boolean).join(" · ");
    const sorts = GENRE_SORTS.map(([key, label]) => `<option value="${key}" ${key === genreState.sort ? "selected" : ""}>${label}</option>`).join("");
    return `
      <header class="sheet genre-sheet" style="${genreStyle(item)}">
        <div class="genre-art ${genreFit(item.name)}" aria-hidden="true"><span>${esc(item.name)}</span></div>
        <div>
          <p class="kicker">Género</p>
          <h1>${esc(item.name)}</h1>
          <p class="sheet-sub">${esc(meta)}</p>
          ${data.tracks.length ? `<div class="sheet-actions">
            <button type="button" class="btn solid" data-action="play-genre">Reproducir</button>
            <button type="button" class="btn ghost shuffle" data-action="play-genre-shuffle">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M16 4h5v5M4 18l5.5-5.5M14 14l6 6M4 6l6 6M16 20h5v-5"/></svg>
              Aleatorio
            </button>
            ${addButton("genre", item.slug)}
          </div>` : ""}
        </div>
      </header>
      ${data.tracks.length > 1 ? `<div class="list-tools">
        <label class="list-filter">
          <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6"/><path d="m16 16 4 4"/></svg>
          <input type="search" id="genre-filter" placeholder="Buscar en ${esc(item.name)}" aria-label="Buscar en ${esc(item.name)}" autocomplete="off">
        </label>
        <span class="list-count" id="genre-count" aria-live="polite"></span>
        <label class="list-sort">Ordenar por
          <select id="genre-sort">${sorts}</select>
        </label>
      </div>` : ""}
      <div id="genre-tracks">${genreListHtml()}</div>
    `;
  }

  async function renderGenres() {
    const data = await api.genres();
    const list = (data.genres || []).slice();
    if (genresOrder === "name") list.sort((a, b) => (a.slug === "sin-genero") - (b.slug === "sin-genero") || a.name.localeCompare(b.name, "es", { sensitivity: "base" }));
    const named = list.filter((item) => item.slug !== "sin-genero").length;
    return `
      <h1 class="hello">Géneros</h1>
      <p class="lead">${plural(named, "género", "géneros")} en la música del servidor. Una canción con varias etiquetas aparece en cada una.</p>
      ${list.length > 1 ? `<div class="seg" role="group" aria-label="Ordenar géneros">
        <button type="button" class="seg-btn" data-action="genres-order" data-order="count" aria-pressed="${genresOrder === "count"}">Más canciones</button>
        <button type="button" class="seg-btn" data-action="genres-order" data-order="name" aria-pressed="${genresOrder === "name"}">A–Z</button>
      </div>` : ""}
      ${list.length ? `<div class="genre-grid">${list.map(genreTile).join("")}</div>` : `<p class="empty">Todavía no hay canciones con género.</p>`}
    `;
  }

  function genreQueue() {
    const rows = [...view.querySelectorAll("#genre-tracks .track")].map((row) => trackFromRow(row)).filter(Boolean);
    remember(rows);
    return rows;
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
        ${coverImg(item.cover_url, { high: true })}
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
    return `<div class="track up-row${admin ? "" : " no-delete"}" data-id="${track.id}" data-song="${esc(track.song_key || "")}">
      <button type="button" class="up-cover" data-action="play-track" data-id="${track.id}" data-context="uploads" data-context-id="uploads" data-index="${index}" aria-label="Reproducir ${esc(track.title)}">
        ${coverImg(track.cover_url, { size: 48 })}
      </button>
      <div class="track-main">
        <button type="button" class="track-title" data-action="play-track" data-id="${track.id}" data-context="uploads" data-context-id="uploads" data-index="${index}">${esc(track.title)}</button>
        <a href="#/album/${track.album_id}">${esc(track.artist)} · ${esc(track.album)}</a>
      </div>
      <span class="time">${fmt(track.duration_ms)}</span>
      <button type="button" class="icon-btn heart ${track.liked ? "is-on" : ""}" data-action="like" data-like="${track.id}" aria-label="Me gusta">${track.liked ? "♥" : "♡"}</button>
      ${admin ? `<button type="button" class="icon-btn up-delete" data-action="delete-upload" data-id="${track.id}" aria-label="Eliminar ${esc(track.title)}" title="Eliminar">${TRASH_ICON}</button>` : ""}
      <button type="button" class="icon-btn more" data-action="menu" data-id="${track.id}" data-playlist="" aria-label="Más opciones para ${esc(track.title)}" aria-haspopup="menu">···</button>
    </div>`;
  }

  const UP_SHUFFLE_ICON = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M16 4h5v5M4 18l5.5-5.5M14 14l6 6M4 6l6 6M16 20h5v-5"/></svg>`;
  const UP_FIRST_PAGE = 100; // pistas que se piden primero: la lista aparece y suena enseguida
  const UP_PAGE = 2000; // tamaño de cada petición del resto (se piden en segundo plano)
  const UP_CHUNK = 120; // filas que se pintan por tanda, para no congelar la pantalla

  // Subidas: lo cargado hasta ahora, cuántas filas hay pintadas y la carga del resto en segundo plano.
  const upl = { token: 0, run: 0, tracks: [], total: 0, admin: false, max_mb: 200, rendered: 0, done: true, rest: Promise.resolve(), restError: null };

  // Sin repetidos (por id), conservando el orden en que llegan.
  function uniqueTracks(list) {
    const seen = new Set();
    return (list || []).filter((track) => {
      if (!track || seen.has(track.id)) return false;
      seen.add(track.id);
      return true;
    });
  }

  function uploadCount() {
    return upl.done ? upl.tracks.length : Math.max(upl.total, upl.tracks.length);
  }

  function uploadSummaryText() {
    const count = uploadCount();
    if (!count) return "No hay canciones para reproducir";
    const label = `${count} ${count === 1 ? "canción" : "canciones"}`;
    return upl.done ? `${label} · ${fmt(upl.tracks.reduce((sum, track) => sum + track.duration_ms, 0))}` : label;
  }

  function uploadToolbar() {
    const count = uploadCount();
    const off = count ? "" : " disabled";
    const total = count ? ` (${count})` : "";
    return `<div class="up-toolbar">
      <p class="fine" id="upload-summary">${uploadSummaryText()}</p>
      <div class="up-play">
        <button type="button" class="btn solid" data-action="play-uploads"${off} aria-describedby="upload-summary" aria-label="Reproducir todas las canciones subidas${total}">Reproducir todas${total}</button>
        <button type="button" class="btn ghost shuffle" data-action="play-uploads-shuffle"${off} aria-describedby="upload-summary" aria-label="Reproducir las canciones subidas en orden aleatorio">${UP_SHUFFLE_ICON}Aleatorio</button>
      </div>
    </div>`;
  }

  // Actualiza el resumen y el conteo de los botones cuando termina de llegar la lista.
  function paintUploadToolbar() {
    const summary = document.getElementById("upload-summary");
    if (!summary) return;
    summary.textContent = uploadSummaryText();
    const count = uploadCount();
    const all = view.querySelector("[data-action='play-uploads']");
    if (all) {
      all.textContent = count ? `Reproducir todas (${count})` : "Reproducir todas";
      all.setAttribute("aria-label", `Reproducir todas las canciones subidas${count ? ` (${count})` : ""}`);
    }
  }

  function uploadListHtml() {
    if (!upl.tracks.length) {
      return `${uploadToolbar()}
        <p class="empty">Aún no subiste canciones. Aparecerán aquí, en Buscar, en tus listas y en Me gusta.</p>`;
    }
    const first = upl.tracks.slice(0, UP_CHUNK);
    upl.rendered = first.length;
    return `${uploadToolbar()}
      <div class="tracklist">${first.map((track, index) => uploadRow(track, index, upl.admin)).join("")}</div>`;
  }

  // Pinta por tandas las filas que faltan (sin bloquear la pantalla). Una llamada nueva reemplaza a la anterior.
  async function syncUploadRows() {
    const run = ++upl.run;
    while (upl.rendered < upl.tracks.length) {
      const list = document.querySelector("#upload-list .tracklist");
      if (!list || run !== upl.run) return;
      const from = upl.rendered;
      const to = Math.min(upl.tracks.length, from + UP_CHUNK);
      list.insertAdjacentHTML("beforeend", upl.tracks.slice(from, to).map((track, i) => uploadRow(track, from + i, upl.admin)).join(""));
      upl.rendered = to;
      markPlaying();
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
  }

  // Pide el resto de la lista en segundo plano (nunca rechaza: deja el error en upl.restError).
  async function fetchUploadsRest(token) {
    try {
      for (let guard = 0; token === upl.token && upl.tracks.length < upl.total && guard < 50; guard += 1) {
        const page = await api.uploadsPage(upl.tracks.length, UP_PAGE);
        if (token !== upl.token) return;
        const before = upl.tracks.length;
        upl.tracks = uniqueTracks(upl.tracks.concat(page.tracks));
        if (upl.tracks.length === before) break; // nada nuevo (p. ej. se borró una): no seguir pidiendo
      }
    } catch (error) {
      if (token === upl.token) upl.restError = error;
    } finally {
      if (token === upl.token) {
        upl.done = true;
        paintUploadToolbar();
        syncUploadRows();
      }
    }
  }

  // Primera página ya, el resto en segundo plano.
  async function loadUploads() {
    const token = ++upl.token;
    const first = await api.uploadsPage(0, UP_FIRST_PAGE);
    if (token !== upl.token) return;
    upl.tracks = uniqueTracks(first.tracks);
    upl.total = Number(first.total) || upl.tracks.length;
    upl.admin = Boolean(first.admin);
    upl.max_mb = first.max_mb;
    upl.rendered = 0;
    upl.restError = null;
    upl.done = upl.tracks.length >= upl.total;
    upl.rest = upl.done ? Promise.resolve() : fetchUploadsRest(token);
  }

  // Arranca con lo que ya hay cargado (la primera pista suena de inmediato) y completa la cola cuando
  // llega el resto. «options» pasa directo a player.playContext.
  function playUploadsFrom(index, options) {
    const head = upl.tracks.slice();
    remember(head);
    const started = player.playContext(head, index, options);
    if (!upl.done) {
      const ctx = player.contextId();
      const token = upl.token;
      upl.rest.then(() => {
        if (token !== upl.token) return;
        if (upl.restError) {
          showToast("No se pudo cargar toda la lista; sigue lo que ya estaba en la cola");
          return;
        }
        const tail = upl.tracks.slice(head.length);
        remember(tail);
        player.extendQueue(tail, ctx);
      });
    }
    return started;
  }

  // «Reproducir todas» / «Aleatorio». Reproducir todas suena al instante con lo ya cargado y completa la
  // cola después. Aleatorio espera a tener la lista completa (casi siempre ya llegó) para mezclar todo el conjunto.
  let playingUploads = false;
  async function playUploads(shuffle) {
    if (playingUploads) return;
    playingUploads = true;
    const buttons = [...view.querySelectorAll("[data-action^='play-uploads']")];
    buttons.forEach((button) => {
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
    });
    try {
      if (!upl.tracks.length) {
        showToast("No hay canciones para reproducir");
        await refreshUploadList().catch(() => {});
        return;
      }
      if (shuffle) {
        if (!upl.done) await upl.rest;
        if (upl.restError) showToast("No se pudo cargar toda la lista; se mezcla lo que hay");
        const tracks = upl.tracks.slice();
        remember(tracks);
        await player.playShuffled(tracks);
      } else {
        await playUploadsFrom(0, { shuffle: false });
      }
    } finally {
      playingUploads = false;
      buttons.forEach((button) => {
        button.removeAttribute("aria-busy");
        button.disabled = false;
      });
    }
  }

  async function renderUploads() {
    await loadUploads();
    const data = { admin: upl.admin, max_mb: upl.max_mb };
    const panel = data.admin
      ? `<div class="upload-panel">
        <label class="upload-drop" for="upload-input">
          <input type="file" id="upload-input" multiple accept="${UPLOAD_ACCEPT}">
          <strong>Elegir archivos</strong>
          <span>MP3, M4A/AAC, FLAC, OGG u WAV · hasta ${esc(data.max_mb)} MB cada uno · puedes elegir varios</span>
        </label>
        <div id="upload-queue" class="upload-queue"></div>
      </div>`
      : `<div class="auth-card">
        <strong>Solo el administrador sube canciones</strong>
        <p>Las canciones subidas son para todos: puedes escucharlas, darles Me gusta y agregarlas a tus listas.</p>
      </div>`;
    setTimeout(syncUploadRows, 0);
    return `
      <h1 class="hello">${data.admin ? "Subir música" : "Canciones subidas"}</h1>
      <p class="lead">${data.admin ? "Sube archivos desde el teléfono o este equipo. Se leen título, artista, álbum, número de pista, duración y portada." : "Música que el administrador agregó al catálogo compartido."}</p>
      ${panel}
      <section class="block"><div class="block-head"><h2>${data.admin ? "Archivos subidos" : "Canciones"}</h2></div><div id="upload-list">${uploadListHtml()}</div></section>
    `;
  }

  async function refreshUploadList() {
    if (!document.getElementById("upload-list")) return;
    await loadUploads();
    const box = document.getElementById("upload-list");
    if (!box) return;
    box.innerHTML = uploadListHtml();
    markPlaying();
    syncUploadRows();
  }

  function confirmDelete(track) {
    openModal({
      title: "Eliminar canción",
      confirm: "Eliminar",
      danger: true,
      body: `<p>Se eliminará «${esc(track.title)}» de ${esc(track.artist)}, con su archivo. También sale de tus listas y de Me gusta.</p>`,
      onSubmit: async () => {
        closeModal();
        await api.deleteTrack(track.id);
        known.delete(track.id);
        const snap = player.snapshot();
        for (let i = snap.queue.length - 1; i >= 0; i -= 1) {
          if (snap.queue[i].id === track.id && i !== snap.index) player.removeQueued(i);
        }
        showToast("Canción eliminada");
        if (parts()[0] === "uploads") await refreshUploadList();
        else route();
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
          else if (error.status === 401 || error.status === 403) {
            row.done("error", error.message);
            items.slice(i + 1).forEach((rest) => rest.row.done("error", "Pendiente: vuelve a elegir el archivo"));
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
        ${coverImg(data.cover_url, { high: true })}
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

  // ---------- Cuenta y administración ----------

  function isAdmin() {
    return Boolean(me && me.admin);
  }

  function isDemo() {
    return Boolean(me && me.demo);
  }

  function initial(text) {
    return (String(text || "?").trim()[0] || "?").toUpperCase();
  }

  function paintPending(count) {
    const n = Number(count) || 0;
    if (me) me.pending = n;
    const label = n ? String(n > 99 ? "99+" : n) : "";
    for (const id of ["nav-badge", "user-admin-count"]) {
      const el = document.getElementById(id);
      if (!el) continue;
      el.textContent = label;
      el.hidden = !n;
    }
    const dot = document.getElementById("user-dot");
    if (dot) dot.hidden = !n || !isAdmin();
    const navAdmin = document.querySelector(".nav-link[data-nav='admin']");
    if (navAdmin) navAdmin.setAttribute("aria-label", n ? `Cuentas, ${n} ${n === 1 ? "solicitud pendiente" : "solicitudes pendientes"}` : "Cuentas");
    userBtn?.setAttribute("aria-label", `Tu cuenta: ${me?.user?.display_name || ""}${n && isAdmin() ? `. ${n} ${n === 1 ? "solicitud pendiente" : "solicitudes pendientes"}` : ""}`);
  }

  function paintMe() {
    const user = me?.user;
    if (!user) return;
    document.getElementById("user-initial").textContent = initial(user.display_name || user.username);
    document.getElementById("user-name").textContent = user.display_name || user.username;
    document.getElementById("user-menu-head").innerHTML = `<strong>${esc(user.display_name || user.username)}</strong><span>@${esc(user.username)}${isAdmin() ? " · Administrador" : ""}${isDemo() ? " · Cuenta demo" : ""}</span>`;
    document.querySelectorAll("[data-admin-only]").forEach((el) => {
      el.hidden = !isAdmin();
    });
    document.body.classList.toggle("is-admin", isAdmin());
    // Cuenta demo: aviso visible y sin «Cambiar contraseña» (el servidor también lo rechaza).
    document.body.classList.toggle("is-demo", isDemo());
    const demoBanner = document.getElementById("demo-banner");
    if (demoBanner) demoBanner.hidden = !isDemo();
    const passwordItem = document.getElementById("user-password");
    if (passwordItem) passwordItem.hidden = isDemo();
    const demoNote = document.getElementById("user-demo-note");
    if (demoNote) demoNote.hidden = !isDemo();
    paintPending(me.pending || 0);
  }

  async function loadMe() {
    me = await api.me();
    setCsrf(me.csrf);
    // Otro usuario en este navegador: borrar cachés del anterior.
    try {
      const last = localStorage.getItem("spotyofo-uid");
      if (last && last !== String(me.user.id)) await clearSiteCaches();
      localStorage.setItem("spotyofo-uid", String(me.user.id));
    } catch {
      /* almacenamiento bloqueado */
    }
    paintMe();
    clearInterval(pendingTimer);
    if (isAdmin()) {
      pendingTimer = setInterval(() => {
        if (document.hidden) return;
        api.me().then((data) => {
          setCsrf(data.csrf);
          paintPending(data.pending || 0);
        }).catch(() => {});
      }, 60000);
    }
  }

  async function clearSiteCaches() {
    try {
      navigator.serviceWorker?.controller?.postMessage("logout");
      if (window.caches) {
        const keys = await caches.keys();
        await Promise.all(keys.map((key) => caches.delete(key)));
      }
    } catch {
      /* sin caché disponible */
    }
  }

  let updating = false;

  function appVersionLabel() {
    return document.querySelector('meta[name="app-version"]')?.content || "";
  }

  function showInstall(available) {
    const button = document.getElementById("user-install");
    if (button) button.hidden = !available;
  }

  async function promptInstall() {
    if (!installPrompt) return;
    const event = installPrompt;
    installPrompt = null;
    showInstall(false);
    event.prompt();
    try {
      await event.userChoice;
    } catch {
      /* el navegador cerró el diálogo */
    }
  }

  async function updateApp() {
    if (updating) return;
    updating = true;
    closeUserMenu();
    const version = appVersionLabel();
    showToast("Buscando actualización…");
    const result = typeof window.spotyUpdate === "function" ? await window.spotyUpdate() : "offline";
    if (result === "reload") {
      showToast("Actualizando…");
      location.reload();
      return;
    }
    updating = false;
    if (result === "current") showToast(version ? `Ya tienes la última versión · ${version}` : "Ya tienes la última versión");
    else showToast("No se pudo buscar la actualización");
  }

  async function logout() {
    closeUserMenu();
    try {
      await api.logout();
    } catch {
      // Token vencido (p. ej. otra pestaña cambió la contraseña): renovar y reintentar una vez.
      await api.me().then((data) => setCsrf(data.csrf)).then(() => api.logout()).catch(() => {});
    }
    await clearSiteCaches();
    try {
      localStorage.removeItem("spotyofo-uid");
    } catch {
      /* almacenamiento bloqueado */
    }
    location.replace("/login?out=1");
  }

  function userMenuItems() {
    return [...userMenu.querySelectorAll("[role='menuitem']")].filter((el) => !el.hidden);
  }

  function openUserMenu() {
    closeMenu();
    userMenu.hidden = false;
    userBtn.setAttribute("aria-expanded", "true");
    userMenuItems()[0]?.focus();
  }

  function closeUserMenu(restore = false) {
    if (userMenu.hidden) return;
    userMenu.hidden = true;
    userBtn.setAttribute("aria-expanded", "false");
    if (restore) userBtn.focus();
  }

  function onUserMenuKey(event) {
    const items = userMenuItems();
    const index = items.indexOf(document.activeElement);
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      closeUserMenu(true);
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const next = (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      items[next]?.focus();
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      items[event.key === "Home" ? 0 : items.length - 1]?.focus();
    } else if (event.key === "Tab") {
      closeUserMenu();
    }
  }

  const STATUS_ORDER = [
    ["pending", "Solicitudes pendientes", "No hay solicitudes pendientes."],
    ["active", "Cuentas activas", ""],
    ["blocked", "Bloqueadas", ""],
    ["rejected", "Rechazadas", ""],
  ];

  function userActions(user, selfId) {
    const self = user.id === selfId;
    const btn = (act, label, kind = "ghost") => `<button type="button" class="btn small ${kind}" data-action="admin-act" data-act="${act}" data-user="${user.id}" data-name="${esc(user.display_name)}">${label}</button>`;
    const list = [];
    if (user.status === "pending") list.push(btn("approve", "Aprobar", "solid"), btn("reject", "Rechazar", "ghost danger-text"));
    if (user.status === "rejected") list.push(btn("approve", "Aprobar", "solid"));
    if (user.status === "blocked") list.push(btn("unblock", "Desbloquear", "solid"));
    if (!self && !user.demo && (user.status === "active" || user.status === "blocked")) list.push(btn("reset-password", "Restablecer contraseña"));
    if (!self && user.status === "active") list.push(btn("block", "Bloquear", "ghost danger-text"));
    if (!self) list.push(btn("delete", "Eliminar", "ghost danger-text"));
    if (self) list.push(`<a class="btn small ghost" href="/account/password">Cambiar mi contraseña</a>`);
    return list.join("");
  }

  function userRow(user, selfId) {
    const meta = [
      `@${esc(user.username)}`,
      user.role === "admin" ? "Administrador" : "",
      user.demo ? "Cuenta demo (se reinicia cada noche)" : "",
      user.id === selfId ? "Tú" : "",
      `Pidió acceso ${esc(user.created_at || "")}`,
      user.last_login_at ? `Último ingreso ${esc(user.last_login_at)}` : "Sin ingresos",
      user.status === "active" ? `${user.playlists} ${user.playlists === 1 ? "lista" : "listas"} · ${user.likes} Me gusta` : "",
      user.locked ? "Bloqueo temporal por intentos fallidos" : "",
      user.must_change_password ? "Debe cambiar su contraseña" : "",
    ].filter(Boolean).join(" · ");
    return `<li class="user-row" data-user="${user.id}">
      <span class="user-avatar" aria-hidden="true">${esc(initial(user.display_name))}</span>
      <div class="user-main">
        <strong>${esc(user.display_name)}</strong>
        <span>${meta}</span>
      </div>
      <span class="user-status is-${esc(user.status)}">${esc(user.status_label)}</span>
      <div class="user-actions">${userActions(user, selfId)}</div>
    </li>`;
  }

  async function renderAdmin() {
    const data = await api.adminUsers();
    paintPending(data.pending);
    const groups = STATUS_ORDER.map(([status, title, empty]) => {
      const users = data.users.filter((user) => user.status === status);
      if (!users.length && !empty) return "";
      const badge = status === "pending" && users.length ? ` <span class="count-badge" aria-hidden="true">${users.length}</span>` : "";
      return `<section class="block" aria-labelledby="users-${status}">
        <div class="block-head"><h2 id="users-${status}">${title}${badge}</h2></div>
        ${users.length ? `<ul class="user-list">${users.map((user) => userRow(user, data.me)).join("")}</ul>` : `<p class="empty">${empty}</p>`}
      </section>`;
    }).join("");
    return `
      <h1 class="hello">Cuentas</h1>
      <p class="lead">Tú decides quién entra. Cada cuenta tiene sus propios Me gusta, listas e historial; el catálogo de música es compartido.</p>
      ${groups}
    `;
  }

  function showTempPassword(user, password) {
    openModal({
      title: "Contraseña temporal",
      confirm: "Listo",
      noCancel: true,
      body: `<p>Entrégale esta contraseña a <strong>${esc(user.display_name)}</strong> (@${esc(user.username)}). Al entrar deberá elegir una nueva. No se volverá a mostrar.</p>
        <p class="temp-pass"><code id="temp-pass">${esc(password)}</code></p>
        <button type="button" class="btn ghost small" id="temp-copy">Copiar</button>`,
      onSubmit: async () => closeModal(),
    });
    document.getElementById("temp-copy")?.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(password);
        showToast("Contraseña copiada");
      } catch {
        const range = document.createRange();
        range.selectNodeContents(document.getElementById("temp-pass"));
        const selection = getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        showToast("Selecciónala y cópiala");
      }
    });
  }

  const ADMIN_CONFIRM = {
    reject: ["Rechazar solicitud", "Rechazar", (name) => `«${name}» no podrá entrar. Puedes aprobarla más adelante.`],
    block: ["Bloquear cuenta", "Bloquear", (name) => `«${name}» saldrá de todas sus sesiones y no podrá entrar hasta que la desbloquees.`],
    delete: ["Eliminar cuenta", "Eliminar", (name) => `Se borrarán la cuenta de «${name}», sus listas, sus Me gusta y su historial. No se puede deshacer.`],
    "reset-password": ["Restablecer contraseña", "Restablecer", (name) => `Se creará una contraseña temporal para «${name}» y se cerrarán sus sesiones.`],
  };

  async function adminAct(button) {
    const act = button.dataset.act;
    const id = button.dataset.user;
    const name = button.dataset.name || "";
    const run = async () => {
      button.disabled = true;
      try {
        if (act === "delete") {
          const result = await api.adminDelete(id);
          paintPending(result.pending);
          showToast(`Cuenta de «${name}» eliminada`);
        } else {
          const result = await api.adminAction(id, act);
          paintPending(result.pending);
          if (act === "reset-password" && result.temporary_password) showTempPassword(result.user, result.temporary_password);
          const done = { approve: "aprobada", reject: "rechazada", block: "bloqueada", unblock: "desbloqueada", "reset-password": "con contraseña temporal" };
          showToast(`Cuenta de «${name}» ${done[act] || "actualizada"}`);
        }
        if (parts()[0] === "admin") {
          const scroll = view.scrollTop;
          view.innerHTML = await renderAdmin();
          view.scrollTop = scroll;
          const again = view.querySelector(`.user-row[data-user="${CSS.escape(id)}"] .user-actions button`);
          (again || view.querySelector(".user-row button") || view).focus?.();
        }
      } finally {
        button.disabled = false;
      }
    };
    const confirm = ADMIN_CONFIRM[act];
    if (!confirm) return run();
    const [title, label, text] = confirm;
    openModal({
      title,
      confirm: label,
      danger: act !== "reset-password",
      body: `<p>${esc(text(name))}</p>`,
      onSubmit: async () => {
        closeModal();
        await run().catch((error) => showToast(error.message));
      },
    });
    return null;
  }

  async function route() {
    if (orderTimer) flushOrder();
    closePicker();
    const [name, arg] = parts();
    if (name !== "genre") genreState = null;
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
    if (name === "admin") return isAdmin() ? show(renderAdmin) : show(renderHome);
    if (name === "equipo" && !isAdmin()) return show(async () => `<h1 class="hello">Archivo de Equipo</h1><p class="empty">La carpeta de música del servidor es solo para el administrador.</p>`);
    if (name === "equipo") {
      const rel = parts().slice(1).map(decodeURIComponent).join("/");
      return show(() => renderEquipo(rel));
    }
    if (name === "likes") return show(renderLikes);
    if (name === "genres") return show(renderGenres);
    if (name === "genre" && arg) return show(() => renderGenre(decodeURIComponent(arg)));
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
    if (context === "uploads" && upl.tracks.length) {
      const at = upl.tracks.findIndex((item) => String(item.id) === String(button.dataset.id));
      await playUploadsFrom(at >= 0 ? at : index);
      return;
    }
    if (context === "search" || context === "recent" || context === "uploads" || context === "equipo" || context === "genre") {
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
          ${coverImg(track.cover_url, { size: 42, lazy: true })}
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
      ${track.source === "user" && isAdmin() ? item("delete-upload", "Eliminar archivo") : ""}
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
          ${coverImg(list.cover_url, { size: 48, lazy: true })}
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
    if (kind === "likes" || kind === "genre") {
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
    if (action === "play-genre" || action === "play-genre-shuffle") {
      const tracks = genreQueue();
      if (!tracks.length) {
        showToast("No hay canciones para reproducir");
        return;
      }
      if (action === "play-genre-shuffle") await player.playShuffled(tracks);
      else await player.playContext(tracks, 0, { shuffle: false });
      return;
    }
    if (action === "genres-order") {
      genresOrder = target.dataset.order === "name" ? "name" : "count";
      await show(renderGenres);
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
    if (action === "delete-upload") {
      const track = trackFromRow(target);
      if (track && isAdmin()) confirmDelete(track);
      return;
    }
    if (action === "admin-act") {
      await adminAct(target);
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
    if (action === "play-uploads" || action === "play-uploads-shuffle") {
      await playUploads(action === "play-uploads-shuffle");
      return;
    }
    if (action === "play-search") {
      const data = await api.search(target.dataset.query || "", true);
      const tracks = data.tracks || [];
      remember(tracks);
      if (!tracks.length) {
        showToast("No hay canciones para reproducir");
        return;
      }
      await player.playContext(tracks, 0, { shuffle: false });
      if (data.track_total > tracks.length) showToast(`Reproduciendo las primeras ${tracks.length} canciones`);
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
    side.addEventListener("click", (event) => {
      if (event.target.closest("#genre-fold")) toggleSideGenres();
    });
    view.addEventListener("change", (event) => {
      if (event.target.id !== "genre-sort" || !genreState) return;
      genreState.sort = event.target.value;
      try {
        localStorage.setItem(GENRE_SORT_KEY, genreState.sort);
      } catch {
        /* almacenamiento bloqueado */
      }
      paintGenreList();
    });
    view.addEventListener("input", (event) => {
      if (event.target.id !== "genre-filter" || !genreState) return;
      genreState.filter = event.target.value;
      paintGenreList();
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
      if (action === "delete-upload" && isAdmin()) confirmDelete(track);
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
    window.addEventListener("hashchange", () => {
      closeUserMenu();
      route();
    });
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
    onSessionExpired((needs) => location.assign(needs === "setup" ? "/setup" : "/login"));
    userBtn.addEventListener("click", () => (userMenu.hidden ? openUserMenu() : closeUserMenu(true)));
    userMenu.addEventListener("keydown", onUserMenuKey);
    const installed = window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
    window.addEventListener("beforeinstallprompt", (event) => {
      if (installed) return;
      event.preventDefault();
      installPrompt = event;
      showInstall(true);
    });
    window.addEventListener("appinstalled", () => {
      installPrompt = null;
      showInstall(false);
      showToast("SpotyOFO quedó instalada");
    });
    userMenu.addEventListener("click", (event) => {
      if (event.target.closest("#user-install")) {
        closeUserMenu();
        promptInstall();
        return;
      }
      if (event.target.closest("#user-update")) {
        updateApp();
        return;
      }
      if (event.target.closest("#user-logout")) {
        logout();
        return;
      }
      if (event.target.closest("a")) closeUserMenu();
    });
    document.addEventListener("click", (event) => {
      if (!userMenu.hidden && !userMenu.contains(event.target) && !userBtn.contains(event.target)) closeUserMenu();
    });
    loadMe()
      .then(() => {
        if (!location.hash) location.hash = "#/";
        else route();
      })
      .catch((error) => {
        if (error.status === 401) location.assign(error.data?.needs === "setup" ? "/setup" : "/login");
        else view.innerHTML = `<p class="empty">${esc(error.message)}</p>`;
      });
  }

  return { boot };
}
