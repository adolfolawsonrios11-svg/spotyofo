// Radio en vivo: Nacional (Chile por región), Latinoamérica (por país), Mundial y Mis radios.
// Los datos vienen de /api/radio/* (proxy con caché de SpotyOFO hacia Radio Browser); el audio
// lo abre el reproductor directo desde cada emisora (solo https, sin HLS).
import { api } from "./api.js?v=20261017a";

const TABS = [
  ["nacional", "Nacional"],
  ["regional", "Latinoamérica", "Latam"],
  ["mundial", "Mundial"],
  ["mis", "Mis radios"],
];
const ORDERS = [
  ["clicks", "Más escuchadas"],
  ["votes", "Más votadas"],
  ["trend", "Tendencia"],
  ["name", "Nombre (A–Z)"],
];
const PAGE = 30;
const PREF_KEY = "spotyofo-radio";
const DEMO_NOTE = "La cuenta demo puede escuchar radios, pero no guardarlas en Mis radios.";
const HEART = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20s-7-4.4-7-9a4 4 0 0 1 7-2 4 4 0 0 1 7 2c0 4.6-7 9-7 9z"/></svg>`;
const PLAY = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14l12-7z"/></svg>`;
const PAUSE = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5h4v14H7zM13 5h4v14h-4z"/></svg>`;
const SEARCH = `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6"/><path d="m16 16 4 4"/></svg>`;

function loadPrefs() {
  try {
    const raw = JSON.parse(localStorage.getItem(PREF_KEY) || "{}");
    return {
      order: ORDERS.some(([key]) => key === raw.order) ? raw.order : "clicks",
      country: /^[A-Z]{2}$/.test(raw.country || "") ? raw.country : "AR",
      world: /^[A-Z]{2}$/.test(raw.world || "") ? raw.world : "",
      tab: TABS.some(([key]) => key === raw.tab) ? raw.tab : "nacional",
    };
  } catch {
    return { order: "clicks", country: "AR", world: "", tab: "nacional" };
  }
}

export function createRadio({ player, view, esc, showToast, isDemo }) {
  const prefs = loadPrefs();
  const stations = new Map(); // uuid -> emisora (lo último que respondió el servidor)
  const favs = new Set();
  let meta = null;
  let metaPromise = null;
  let st = null;
  let seq = 0;
  let typing = 0;
  let names = null;
  try {
    names = new Intl.DisplayNames(["es"], { type: "region" });
  } catch {
    names = null;
  }

  function savePrefs() {
    try {
      localStorage.setItem(PREF_KEY, JSON.stringify(prefs));
    } catch {
      /* almacenamiento bloqueado */
    }
  }

  function countryName(code, fallback = "") {
    if (!code) return fallback;
    try {
      return names?.of(code) || fallback || code;
    } catch {
      return fallback || code;
    }
  }

  function loadMeta() {
    if (meta) return Promise.resolve(meta);
    if (!metaPromise) {
      metaPromise = api.radioMeta()
        .then((data) => {
          meta = data;
          return data;
        })
        .finally(() => {
          metaPromise = null;
        });
    }
    return metaPromise;
  }

  function keep(list) {
    for (const station of list || []) {
      stations.set(station.stationuuid, station);
      if (station.favorite) favs.add(station.stationuuid);
      else favs.delete(station.stationuuid);
    }
  }

  // ---------- pantalla ----------

  function hash() {
    return `#/radio/${st?.tab || prefs.tab}`;
  }

  function regionOptions(m) {
    const regions = m?.regions || [];
    const total = m?.cl_total;
    const opts = [`<option value="">Todas las regiones${total != null ? ` (${total})` : ""}</option>`];
    for (const region of regions) {
      if (region.stations === 0) continue;
      opts.push(`<option value="${esc(region.key)}">${esc(region.label)}${region.stations != null ? ` (${region.stations})` : ""}</option>`);
    }
    if (m?.cl_without_region) opts.push(`<option value="otras">Sin región declarada (${m.cl_without_region})</option>`);
    return opts.join("");
  }

  function countryOptions(m, tab) {
    const counts = new Map((m?.countries || []).map((c) => [c.code, c]));
    let codes;
    if (tab === "regional") codes = (m?.latam || ["AR"]).slice();
    else codes = (m?.countries || []).filter((c) => c.stations >= 3).map((c) => c.code);
    const items = codes.map((code) => ({ code, name: countryName(code, counts.get(code)?.name), n: counts.get(code)?.stations }));
    items.sort((a, b) => a.name.localeCompare(b.name, "es", { sensitivity: "base" }));
    const selected = tab === "regional" ? st.country : st.country || "";
    const head = tab === "mundial" ? [`<option value="">Todos los países</option>`] : [];
    return head.concat(items.map((item) => `<option value="${esc(item.code)}"${item.code === selected ? " selected" : ""}>${esc(item.name)}</option>`)).join("");
  }

  function chipsHtml(m) {
    const genres = (m?.genres || []).filter((g) => st.tab !== "nacional" || g.stations_cl == null || g.stations_cl > 0);
    if (!genres.length) return "";
    const chip = (key, label) => `<button type="button" class="chip radio-chip${st.genre === key ? " is-on" : ""}" data-radio="genre" data-genre="${esc(key)}" aria-pressed="${st.genre === key}">${esc(label)}</button>`;
    return `<div class="radio-chips" role="group" aria-label="Géneros">${chip("", "Todos")}${genres.map((g) => chip(g.key, g.label)).join("")}</div>`;
  }

  function toolsHtml(m) {
    const placeholder = st.tab === "mundial" ? "Buscar radios del mundo" : st.tab === "regional" ? "Buscar en este país" : "Buscar radios chilenas";
    const select = st.tab === "nacional"
      ? `<label class="list-sort radio-select">Región <select id="radio-region" aria-label="Región">${regionOptions(m)}</select></label>`
      : `<label class="list-sort radio-select">País <select id="radio-country" aria-label="País">${countryOptions(m, st.tab)}</select></label>`;
    const orders = ORDERS.map(([key, label]) => `<option value="${key}"${st.order === key ? " selected" : ""}>${label}</option>`).join("");
    return `<div class="list-tools radio-tools">
        <label class="list-filter">${SEARCH}<input type="search" id="radio-q" placeholder="${placeholder}" aria-label="${placeholder}" autocomplete="off" maxlength="80"></label>
        ${select}
        <label class="list-sort radio-select">Ordenar <select id="radio-order" aria-label="Ordenar">${orders}</select></label>
      </div>`;
  }

  async function render(tabArg) {
    const tab = TABS.some(([key]) => key === tabArg) ? tabArg : prefs.tab;
    prefs.tab = tab;
    savePrefs();
    seq += 1;
    st = {
      tab,
      q: "",
      region: "",
      country: tab === "regional" ? prefs.country : tab === "mundial" ? prefs.world : "CL",
      genre: "",
      order: prefs.order,
      offset: 0,
      list: [],
      total: null,
      hasMore: false,
      stale: false,
    };
    let m = null;
    if (tab !== "mis") {
      try {
        m = await loadMeta();
      } catch {
        m = null; // la lista mostrará el error con «Reintentar»
      }
    }
    const tabs = TABS.map(([key, label, short]) => `<a class="seg-btn" href="#/radio/${key}" aria-pressed="${key === tab}"${key === tab ? ' aria-current="page"' : ""}${short ? ` aria-label="${label}"` : ""}>${short ? `<span class="tab-full">${label}</span><span class="tab-short" aria-hidden="true">${short}</span>` : label}</a>`).join("");
    const intro = {
      nacional: "Radios de Chile por región. Muchas emisoras no declaran su región: aparecen en «Todas las regiones».",
      regional: "Radios de Latinoamérica: elige un país.",
      mundial: "Radios de todo el mundo, por nombre o género.",
      mis: isDemo() ? DEMO_NOTE : "Las radios que guardaste con el corazón.",
    }[tab];
    return `<section class="radio-view" data-radio-tab="${tab}">
      <div class="results-head">
        <div>
          <p class="kicker">Radio en vivo</p>
          <h1 class="hello">Radio</h1>
          <p class="lead">${esc(intro)}</p>
        </div>
      </div>
      <nav class="seg radio-tabs" aria-label="Secciones de radio">${tabs}</nav>
      ${tab === "mis" ? "" : toolsHtml(m)}
      ${tab === "mis" ? "" : chipsHtml(m)}
      <p class="radio-status" id="radio-status" aria-live="polite"></p>
      <div class="radio-list" id="radio-list"><p class="loading">Buscando radios…</p></div>
      <div class="radio-more"><button type="button" class="btn ghost" data-radio="more" hidden>Cargar más</button></div>
      <p class="fine radio-credit">Directorio: <a href="https://www.radio-browser.info/" target="_blank" rel="noopener noreferrer">Radio Browser</a>. Solo se muestran emisoras con transmisión segura (https). El ecualizador y el crossfade no se aplican a la radio.</p>
    </section>`;
  }

  function mounted() {
    if (!view.querySelector(".radio-view")) return;
    load(true);
  }

  function rowHtml(station) {
    const sub = [
      station.countrycode ? countryName(station.countrycode, station.country) : station.country,
      station.region_label || (station.countrycode !== "CL" ? station.state : ""),
    ].filter(Boolean).join(" · ");
    const tags = station.tags.slice(0, 3).join(", ");
    const quality = [station.codec, station.bitrate ? `${station.bitrate} kbps` : ""].filter(Boolean).join(" · ");
    const fav = favs.has(station.stationuuid);
    const logo = station.favicon
      ? `<img src="${esc(station.favicon)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" width="44" height="44">`
      : "";
    return `<article class="radio-row" data-uuid="${esc(station.stationuuid)}">
      <button type="button" class="radio-hit" data-radio="play" aria-label="Escuchar ${esc(station.name)} en vivo">
        <span class="radio-logo" aria-hidden="true"><span>${esc((station.name.trim()[0] || "?").toUpperCase())}</span>${logo}</span>
        <span class="radio-main">
          <span class="radio-name">${esc(station.name)}</span>
          <span class="radio-sub">${esc(sub)}${tags ? `<span class="radio-tags"> · ${esc(tags)}</span>` : ""}</span>
        </span>
        <span class="radio-q">${esc(quality)}</span>
        <span class="radio-state" aria-hidden="true">${PLAY}</span>
      </button>
      <button type="button" class="icon-btn heart${fav ? " is-on" : ""}" data-radio="fav" aria-pressed="${fav}" aria-label="${fav ? "Quitar de Mis radios" : "Guardar en Mis radios"}: ${esc(station.name)}">${HEART}</button>
    </article>`;
  }

  function paintList(append) {
    const box = view.querySelector("#radio-list");
    if (!box || !st) return;
    if (!st.list.length) {
      box.innerHTML = st.tab === "mis"
        ? `<p class="empty">${isDemo() ? esc(DEMO_NOTE) : "Aún no guardas radios. Toca el corazón de una emisora para tenerla aquí."}</p>`
        : `<p class="empty">No encontré radios con esos filtros.</p>`;
    } else if (append) {
      const rows = st.list.slice(box.querySelectorAll(".radio-row").length);
      box.insertAdjacentHTML("beforeend", rows.map(rowHtml).join(""));
    } else {
      box.innerHTML = st.list.map(rowHtml).join("");
    }
    const more = view.querySelector("[data-radio='more']");
    if (more) more.hidden = !st.hasMore;
    const status = view.querySelector("#radio-status");
    if (status) {
      const count = st.tab === "mis"
        ? `${st.list.length} ${st.list.length === 1 ? "radio guardada" : "radios guardadas"}`
        : st.total != null ? `${st.total} ${st.total === 1 ? "radio" : "radios"}` : `${st.list.length}${st.hasMore ? "+" : ""} radios`;
      status.textContent = count + (st.stale ? " · Radio Browser no respondió: muestro la última copia guardada." : "");
    }
    sync(player.snapshot());
  }

  async function load(reset) {
    if (!st) return;
    const mine = ++seq;
    const box = view.querySelector("#radio-list");
    if (reset) {
      st.offset = 0;
      st.list = [];
      if (box) box.innerHTML = `<p class="loading">Buscando radios…</p>`;
    }
    const more = view.querySelector("[data-radio='more']");
    if (more) more.disabled = true;
    try {
      if (st.tab === "mis") {
        const data = await api.radioFavorites();
        if (mine !== seq) return;
        favs.clear();
        keep(data.favorites);
        st.list = data.favorites;
        st.total = data.favorites.length;
        st.hasMore = false;
        paintList(false);
        return;
      }
      const params = { scope: st.tab, order: st.order, offset: st.offset, limit: PAGE };
      if (st.q) params.q = st.q;
      if (st.genre) params.genre = st.genre;
      if (st.tab === "nacional" && st.region) params.region = st.region;
      if (st.tab !== "nacional" && st.country) params.country = st.country;
      const data = await api.radioSearch(params);
      if (mine !== seq) return;
      keep(data.stations);
      const seen = new Set(st.list.map((s) => s.stationuuid));
      st.list = st.list.concat(data.stations.filter((s) => !seen.has(s.stationuuid)));
      st.total = data.total;
      st.hasMore = data.has_more;
      st.nextOffset = data.next_offset;
      st.stale = data.stale;
      paintList(!reset);
    } catch (error) {
      if (mine !== seq) return;
      const box2 = view.querySelector("#radio-list");
      if (box2 && (reset || !st.list.length)) {
        box2.innerHTML = `<div class="empty radio-error"><p>${esc(error.message || "No se pudo cargar la radio.")}</p><button type="button" class="btn ghost small" data-radio="retry">Reintentar</button></div>`;
      } else {
        showToast(error.message || "No se pudo cargar más radios.");
      }
    } finally {
      if (more && mine === seq) more.disabled = false;
    }
  }

  // ---------- reproducción y favoritos ----------

  function play(uuid) {
    const station = stations.get(uuid);
    if (!station) return;
    const now = player.radio();
    if (now && now.station.stationuuid === uuid && now.status !== "error") {
      player.toggle();
      return;
    }
    player.playRadio(station);
  }

  function paintHearts(uuid) {
    const on = favs.has(uuid);
    view.querySelectorAll(`.radio-row[data-uuid="${CSS.escape(uuid)}"] [data-radio='fav']`).forEach((button) => {
      button.classList.toggle("is-on", on);
      button.setAttribute("aria-pressed", String(on));
      const name = stations.get(uuid)?.name || "";
      button.setAttribute("aria-label", `${on ? "Quitar de Mis radios" : "Guardar en Mis radios"}: ${name}`);
    });
  }

  async function setFavorite(uuid, on, quiet = false) {
    if (isDemo()) {
      showToast(DEMO_NOTE);
      return false;
    }
    if (on) {
      const data = await api.radioFavoriteAdd(uuid);
      favs.add(uuid);
      if (data.favorite && !stations.has(uuid)) stations.set(uuid, data.favorite);
    } else {
      await api.radioFavoriteRemove(uuid);
      favs.delete(uuid);
    }
    const station = stations.get(uuid);
    if (station) station.favorite = on;
    paintHearts(uuid);
    if (st?.tab === "mis" && !on) {
      st.list = st.list.filter((s) => s.stationuuid !== uuid);
      paintList(false);
    } else if (st?.tab === "mis" && on && station && !st.list.some((s) => s.stationuuid === uuid)) {
      st.list = [station].concat(st.list);
      paintList(false);
    }
    syncFooter();
    if (!quiet) {
      const name = station?.name || "La radio";
      showToast(on ? `«${name}» guardada en Mis radios` : `«${name}» quitada de Mis radios`, on ? [] : [{
        label: "Deshacer",
        run: () => setFavorite(uuid, true, true).then(() => showToast(`«${name}» volvió a Mis radios`)).catch((error) => showToast(error.message)),
      }]);
    }
    return true;
  }

  function toggleFavorite(uuid, button) {
    if (button) button.disabled = true;
    return setFavorite(uuid, !favs.has(uuid))
      .catch((error) => showToast(error.message))
      .finally(() => {
        if (button) button.disabled = false;
      });
  }

  function toggleCurrent() {
    const now = player.radio();
    if (!now) return Promise.resolve();
    const uuid = now.station.stationuuid;
    if (!stations.has(uuid)) stations.set(uuid, now.station);
    return toggleFavorite(uuid);
  }

  function isFavorite(uuid) {
    return favs.has(uuid);
  }

  let footerHook = null;
  function syncFooter() {
    if (footerHook) footerHook(player.snapshot());
  }

  // Marca la emisora que suena (y su estado) en la lista visible.
  function sync(snap) {
    const now = snap?.radio;
    const uuid = now?.station?.stationuuid || "";
    view.querySelectorAll(".radio-row").forEach((row) => {
      const current = row.dataset.uuid === uuid;
      row.classList.toggle("is-current", current);
      row.classList.toggle("is-connecting", current && now.status === "connecting");
      row.classList.toggle("is-error", current && now.status === "error");
      const icon = row.querySelector(".radio-state");
      const live = current && (now.playing || now.status === "connecting");
      const want = live ? "pause" : "play";
      if (icon && icon.dataset.icon !== want) {
        icon.innerHTML = live ? PAUSE : PLAY;
        icon.dataset.icon = want;
      }
      const hit = row.querySelector(".radio-hit");
      const name = stations.get(row.dataset.uuid)?.name || "";
      hit?.setAttribute("aria-label", current && live ? `Pausar ${name}` : `Escuchar ${name} en vivo`);
    });
  }

  // ---------- eventos (delegados en la vista) ----------

  view.addEventListener("click", (event) => {
    const target = event.target.closest("[data-radio]");
    if (!target || !view.contains(target) || !target.closest(".radio-view")) return;
    const action = target.dataset.radio;
    const uuid = target.closest(".radio-row")?.dataset.uuid;
    if (action === "play" && uuid) play(uuid);
    if (action === "fav" && uuid) toggleFavorite(uuid, target);
    if (action === "more" && st) {
      st.offset = st.nextOffset ?? st.list.length;
      load(false);
    }
    if (action === "retry") {
      meta = null;
      load(true);
    }
    if (action === "genre" && st) {
      st.genre = target.dataset.genre || "";
      view.querySelectorAll("[data-radio='genre']").forEach((chip) => {
        const on = chip.dataset.genre === st.genre;
        chip.classList.toggle("is-on", on);
        chip.setAttribute("aria-pressed", String(on));
      });
      load(true);
    }
  });
  view.addEventListener("input", (event) => {
    if (event.target.id !== "radio-q" || !st) return;
    clearTimeout(typing);
    typing = setTimeout(() => {
      st.q = event.target.value.trim();
      load(true);
    }, 350);
  });
  view.addEventListener("change", (event) => {
    if (!st) return;
    const id = event.target.id;
    if (id === "radio-region") st.region = event.target.value;
    else if (id === "radio-country") {
      st.country = event.target.value;
      if (st.tab === "regional") prefs.country = st.country;
      else prefs.world = st.country;
      savePrefs();
    } else if (id === "radio-order") {
      st.order = event.target.value;
      prefs.order = st.order;
      savePrefs();
    } else return;
    load(true);
  });
  // Logo roto: queda la inicial (sin manejadores en línea; la CSP los bloquea).
  view.addEventListener("error", (event) => {
    if (event.target instanceof HTMLImageElement && event.target.closest(".radio-logo")) event.target.remove();
  }, true);

  return {
    render,
    mounted,
    sync,
    hash,
    toggleCurrent,
    isFavorite,
    onFooter(fn) {
      footerHook = fn;
    },
    remember(station) {
      if (station && !stations.has(station.stationuuid)) stations.set(station.stationuuid, station);
    },
  };
}
