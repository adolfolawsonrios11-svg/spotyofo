const SHELL = ["/", "/static/css/app.css", "/static/js/main.js", "/static/favicon.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open("spotyofo-shell")
      .then((cache) => cache.addAll(SHELL).catch(() => {}))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/static/audio/") || url.pathname.startsWith("/static/covers/user/")) {
    return;
  }
  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response.ok && (request.mode === "navigate" || url.pathname.startsWith("/static/"))) {
          const copy = response.clone();
          caches.open("spotyofo-shell").then((cache) => cache.put(request, copy)).catch(() => {});
        }
        return response;
      })
      .catch(async () => {
        const cached = await caches.match(request);
        if (cached) return cached;
        if (request.mode === "navigate") {
          const home = await caches.match("/");
          if (home) return home;
        }
        return new Response("", { status: 504, statusText: "Sin conexión" });
      })
  );
});
