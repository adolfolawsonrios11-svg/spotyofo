/* SpotyOFO service worker. La versión la inyecta Flask al servir /sw.js.
 * Cuentas: las respuestas de la API (datos de cada usuario) NUNCA se guardan en caché,
 * y al cerrar sesión se borran todas las cachés de este sitio.
 * Audio offline: la caché spotyofo-offline-audio la llena la app (Me gusta → Descargar).
 * Las peticiones Range online siguen yendo al servidor; si no hay red y hay copia, se arma un 206. */
const VERSION = "__ASSET_V__";
const SHELL_CACHE = `spotyofo-shell-${VERSION}`;
const RUNTIME_CACHE = "spotyofo-runtime";
const OFFLINE_CACHE = "spotyofo-offline-audio";
const OLD_CACHES = ["spotyofo-api"];
const RUNTIME_LIMIT = 250;
const SHELL = [
  "/manifest.webmanifest",
  "/static/favicon.svg",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE)
      .then((cache) => cache.addAll(SHELL).catch(() => {}))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys
        .filter((key) => (key.startsWith("spotyofo-shell") && key !== SHELL_CACHE) || OLD_CACHES.includes(key))
        .map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

async function trim(cacheName, limit) {
  const cache = await caches.open(cacheName);
  const keys = await cache.keys();
  for (let i = 0; i < keys.length - limit; i += 1) await cache.delete(keys[i]);
}

function cacheable(response) {
  return response.ok && response.type === "basic" && !response.redirected;
}

async function networkFirst(request, cacheName, limit) {
  const cache = await caches.open(cacheName);
  try {
    const response = await fetch(request);
    if (cacheable(response)) {
      cache.put(request, response.clone()).then(() => limit && trim(cacheName, limit)).catch(() => {});
    }
    return response;
  } catch (error) {
    const cached = await cache.match(request) || await caches.match(request);
    if (cached) return cached;
    throw error;
  }
}

async function cacheFirst(request, cacheName, limit) {
  const cached = await caches.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (cacheable(response)) {
    const cache = await caches.open(cacheName);
    cache.put(request, response.clone()).then(() => trim(cacheName, limit)).catch(() => {});
  }
  return response;
}

async function staleWhileRevalidate(request, cacheName, limit) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);
  const refresh = fetch(request)
    .then((response) => {
      if (response.ok || response.type === "opaque") {
        cache.put(request, response.clone()).then(() => trim(cacheName, limit)).catch(() => {});
      }
      return response;
    })
    .catch(() => cached);
  return cached || refresh;
}

function parseRange(header, size) {
  const m = /^bytes=(\d*)-(\d*)$/i.exec((header || "").trim());
  if (!m) return null;
  let start = m[1] === "" ? NaN : Number(m[1]);
  let end = m[2] === "" ? NaN : Number(m[2]);
  if (Number.isNaN(start)) {
    if (Number.isNaN(end)) return null;
    start = Math.max(0, size - end);
    end = size - 1;
  } else if (Number.isNaN(end)) {
    end = size - 1;
  }
  if (start < 0 || end < start || start >= size) return null;
  end = Math.min(end, size - 1);
  return { start, end };
}

async function offlineMatch(url) {
  const cache = await caches.open(OFFLINE_CACHE);
  return (
    (await cache.match(url.href)) ||
    (await cache.match(url.pathname + url.search)) ||
    (await cache.match(url.pathname))
  );
}

async function responseFromOffline(request, cached) {
  const buf = await cached.arrayBuffer();
  const type = cached.headers.get("Content-Type") || "application/octet-stream";
  const range = request.headers.get("range");
  if (!range) {
    return new Response(buf, {
      status: 200,
      headers: {
        "Content-Type": type,
        "Content-Length": String(buf.byteLength),
        "Accept-Ranges": "bytes",
        "X-SpotyOFO-Offline": "1",
      },
    });
  }
  const part = parseRange(range, buf.byteLength);
  if (!part) {
    return new Response(null, {
      status: 416,
      headers: { "Content-Range": `bytes */${buf.byteLength}` },
    });
  }
  const slice = buf.slice(part.start, part.end + 1);
  return new Response(slice, {
    status: 206,
    headers: {
      "Content-Type": type,
      "Content-Length": String(slice.byteLength),
      "Content-Range": `bytes ${part.start}-${part.end}/${buf.byteLength}`,
      "Accept-Ranges": "bytes",
      "X-SpotyOFO-Offline": "1",
    },
  });
}

async function audioFetch(request) {
  const url = new URL(request.url);
  const cached = await offlineMatch(url);
  // Con red: Range y GET van al servidor (comportamiento actual, 206 real).
  // Sin red (o fallo): si hay copia offline, se sirve completa o por rango.
  try {
    const response = await fetch(request);
    if (response && (response.ok || response.status === 206)) return response;
    if (cached) return responseFromOffline(request, cached);
    return response;
  } catch (error) {
    if (cached) return responseFromOffline(request, cached);
    throw error;
  }
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);

  if (url.origin === "https://fonts.googleapis.com" || url.origin === "https://fonts.gstatic.com") {
    event.respondWith(staleWhileRevalidate(request, RUNTIME_CACHE, RUNTIME_LIMIT));
    return;
  }
  if (url.origin !== self.location.origin) return;

  if (url.pathname.startsWith("/static/audio/") || url.pathname === "/api/equipo/audio") {
    event.respondWith(audioFetch(request));
    return;
  }

  if (request.headers.has("range")) return;

  if (request.mode === "navigate") {
    if (url.pathname !== "/") return;
    event.respondWith(
      networkFirst(request, SHELL_CACHE).catch(async () => (await caches.match("/")) || Response.error()),
    );
    return;
  }

  if (url.pathname.startsWith("/static/covers/") || url.pathname === "/api/equipo/image") {
    event.respondWith(cacheFirst(request, RUNTIME_CACHE, RUNTIME_LIMIT));
    return;
  }

  if (url.pathname.startsWith("/api/")) return;

  if (url.pathname.startsWith("/static/") || url.pathname === "/manifest.webmanifest") {
    event.respondWith(networkFirst(request, SHELL_CACHE));
  }
});

self.addEventListener("message", (event) => {
  if (event.data === "skip-waiting") self.skipWaiting();
  if (event.data === "logout") {
    event.waitUntil(caches.keys().then((keys) => Promise.all(keys.map((key) => caches.delete(key)))));
  }
});
