import { createPlayer } from "./player.js?v=20260930f";
import { createUI } from "./ui.js?v=20260930f";

const player = createPlayer();
const ui = createUI(player);
ui.boot();

const assetVersion = document.querySelector('meta[name="asset-version"]')?.content || "";
let reloading = false;

function reloadOnce() {
  if (reloading || sessionStorage.getItem("spotyofo-reloading") === "1") return;
  reloading = true;
  sessionStorage.setItem("spotyofo-reloading", "1");
  location.reload();
}

async function clearAppCaches() {
  if (!window.caches) return;
  const keys = await caches.keys();
  await Promise.all(keys.map((key) => caches.delete(key)));
}

// "current" ya está al día; "reload" hay versión nueva; "offline" no hubo red.
window.spotyUpdate = async function spotyUpdate() {
  try {
    const registration = "serviceWorker" in navigator ? await navigator.serviceWorker.getRegistration() : null;
    if (registration) {
      await registration.update();
      registration.waiting?.postMessage("skip-waiting");
    }
    const response = await fetch("/sw.js", { cache: "no-store" });
    if (!response.ok) return "offline";
    const text = await response.text();
    const match = text.match(/const VERSION = "([0-9a-f]+)"/);
    const remote = match ? match[1] : "";
    if (assetVersion && remote === assetVersion) {
      sessionStorage.removeItem("spotyofo-reloading");
      return "current";
    }
    await clearAppCaches();
    return "reload";
  } catch {
    return "offline";
  }
};

if ("serviceWorker" in navigator && window.isSecureContext) {
  let hadController = Boolean(navigator.serviceWorker.controller);
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (!hadController) {
      hadController = true;
      return;
    }
    reloadOnce();
  });

  window.addEventListener("load", () => {
    const script = assetVersion ? `/sw.js?v=${encodeURIComponent(assetVersion)}` : "/sw.js";
    navigator.serviceWorker.register(script, { scope: "/", updateViaCache: "none" })
      .then((registration) => {
        const pull = async () => {
          const result = await window.spotyUpdate();
          if (result === "reload") reloadOnce();
        };
        pull();
        document.addEventListener("visibilitychange", () => {
          if (document.visibilityState === "visible") pull();
        });
      })
      .catch(() => {});
  });
}
