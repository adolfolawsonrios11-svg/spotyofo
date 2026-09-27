/* SpotyOFO service worker. La versión la inyecta Flask al servir /sw.js. */
const VERSION = "__ASSET_V__";
const SHELL_CACHE = `spotyofo-shell-${VERSION}`;
const RUNTIME_CACHE = "spotyofo-runtime";
const API_CACHE = "spotyofo-api";
const RUNTIME_LIMIT = 250;
const API_LIMIT = 60;
const SHELL = [
  "/",
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
        .filter((key) => key.startsWith("spotyofo-shell") && key !== SHELL_CACHE)
        .map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

async function trim(cacheName, limit) {
  const cache = await caches.open(cacheName);
  const keys = await cache.keys();
  for (let i = 0; i < keys.length - limit; i += 1) await cache.delete(keys[i]);
}

async function networkFirst(request, cacheName, limit) {
  const cache = await caches.open(cacheName);
  try {
    const response = await fetch(request);
    if (response.ok && response.type === "basic") {
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
  if (response.ok || response.type === "opaque") {
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

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return; // subidas, me gusta y demás cambios van siempre a la red
  if (request.headers.has("range")) return; // audio con saltos: directo al servidor
  const url = new URL(request.url);

  // Fuentes de Google: se guardan para abrir sin conexión.
  if (url.origin === "https://fonts.googleapis.com" || url.origin === "https://fonts.gstatic.com") {
    event.respondWith(staleWhileRevalidate(request, RUNTIME_CACHE, RUNTIME_LIMIT));
    return;
  }
  if (url.origin !== self.location.origin) return;

  // Audio: nunca se guarda en caché (archivos grandes y peticiones por rangos).
  if (url.pathname.startsWith("/static/audio/") || url.pathname === "/api/equipo/audio") return;

  if (request.mode === "navigate") {
    event.respondWith(
      networkFirst(request, SHELL_CACHE).catch(async () => (await caches.match("/")) || Response.error()),
    );
    return;
  }

  // Portadas: primero la caché (cambian poco), con límite de entradas.
  if (url.pathname.startsWith("/static/covers/") || url.pathname === "/api/equipo/image") {
    event.respondWith(cacheFirst(request, RUNTIME_CACHE, RUNTIME_LIMIT));
    return;
  }

  // API de lectura: red primero, copia de respaldo para modo sin conexión.
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(networkFirst(request, API_CACHE, API_LIMIT));
    return;
  }

  // JS, CSS e íconos: red primero (siempre la versión nueva) con respaldo en caché.
  if (url.pathname.startsWith("/static/") || url.pathname === "/manifest.webmanifest") {
    event.respondWith(networkFirst(request, SHELL_CACHE));
  }
});

self.addEventListener("message", (event) => {
  if (event.data === "skip-waiting") self.skipWaiting();
});
