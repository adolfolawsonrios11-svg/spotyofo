import { createPlayer } from "./player.js";
import { createUI } from "./ui.js";

const player = createPlayer();
const ui = createUI(player);
ui.boot();

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
}
