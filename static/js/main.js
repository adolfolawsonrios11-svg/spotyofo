import { createPlayer } from "./player.js?v=20260930e";
import { createUI } from "./ui.js?v=20260930e";

const player = createPlayer();
const ui = createUI(player);
ui.boot();

// SpotyOFO es un sitio web. Si el navegador todavía tiene el service worker de cuando
// se podía instalar, se da de baja y se recarga una vez para quedar sin esa caché.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.getRegistrations()
      .then(async (registrations) => {
        if (!registrations.length) return;
        await Promise.all(registrations.map((registration) => registration.unregister()));
        if (window.caches) {
          const keys = await caches.keys();
          await Promise.all(keys.map((key) => caches.delete(key)));
        }
        if (sessionStorage.getItem("spotyofo-sw-cleared") === "1") return;
        sessionStorage.setItem("spotyofo-sw-cleared", "1");
        location.reload();
      })
      .catch(() => {});
  });
}
