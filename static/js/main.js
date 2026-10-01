import { createPlayer } from "./player.js?v=20260930c";
import { createUI } from "./ui.js?v=20260930c";

const player = createPlayer();
const ui = createUI(player);
ui.boot();

// El service worker solo funciona en contexto seguro (HTTPS o localhost).
// La app instalada en Windows a menudo restaura la ventana anterior sin volver a pedir los
// archivos. Al mostrarse, compara la versión con el servidor y recarga si hay una nueva.
if ("serviceWorker" in navigator && window.isSecureContext) {
  const asset = document.querySelector('meta[name="asset-version"]')?.content || "";
  let reloading = false;

  function reloadOnce() {
    if (reloading || sessionStorage.getItem("spotyofo-reloading") === "1") return;
    reloading = true;
    sessionStorage.setItem("spotyofo-reloading", "1");
    location.reload();
  }

  let hadController = Boolean(navigator.serviceWorker.controller);
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (!hadController) {
      hadController = true;
      return;
    }
    reloadOnce();
  });

  window.addEventListener("load", () => {
    const script = asset ? `/sw.js?v=${encodeURIComponent(asset)}` : "/sw.js";
    navigator.serviceWorker.register(script, { scope: "/", updateViaCache: "none" })
      .then((registration) => {
        const pull = async () => {
          try {
            await registration.update();
            const response = await fetch(script, { cache: "no-store" });
            const text = await response.text();
            const match = text.match(/const VERSION = "([0-9a-f]+)"/);
            if (!match || !asset || match[1] === asset) {
              sessionStorage.removeItem("spotyofo-reloading");
              return;
            }
            reloadOnce();
          } catch {
            // Sin red la app sigue con la versión que ya tiene.
          }
        };
        pull();
        document.addEventListener("visibilitychange", () => {
          if (document.visibilityState === "visible") pull();
        });
      })
      .catch(() => {});
  });
}
