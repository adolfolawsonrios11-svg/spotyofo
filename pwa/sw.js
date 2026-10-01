/* SpotyOFO service worker. La versión la inyecta Flask al servir /sw.js.
 * Cuentas: las respuestas de la API (datos de cada usuario) NUNCA se guardan en caché,
 * y al cerrar sesión se borran todas las cachés de este sitio. */
const VERSION = "__ASSET_V__";
const SHELL_CACHE = `spotyofo-shell-${VERSION}`;
const RUNTIME_CACHE = "spotyofo-runtime";
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
    // Solo la portada de la app se guarda (sin datos del usuario); login y demás van siempre a la red.
    if (url.pathname !== "/") return;
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

  // API: siempre a la red, sin copia (cada usuario ve solo sus datos).
  if (url.pathname.startsWith("/api/")) return;

  // JS, CSS e íconos: red primero (siempre la versión nueva) con respaldo en caché.
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
