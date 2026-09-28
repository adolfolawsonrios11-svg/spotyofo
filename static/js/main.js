import { createPlayer } from "./player.js?v=20260928a";
import { createUI } from "./ui.js?v=20260928a";

const player = createPlayer();
const ui = createUI(player);
ui.boot();

// El service worker solo funciona en contexto seguro (HTTPS o localhost).
if ("serviceWorker" in navigator && window.isSecureContext) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
  });
}
