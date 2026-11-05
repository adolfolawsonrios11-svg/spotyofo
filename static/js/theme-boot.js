/* SpotyOFO theme boot: corre antes del CSS para evitar flash.
 * Lee localStorage spotyofo-theme (light|dark) o prefers-color-scheme.
 */
(function () {
  try {
    var KEY = "spotyofo-theme";
    var saved = localStorage.getItem(KEY);
    var theme = saved === "light" || saved === "dark"
      ? saved
      : (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
    var root = document.documentElement;
    root.setAttribute("data-theme", theme);
    root.style.colorScheme = theme;
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", theme === "light" ? "#f4f6fa" : "#090a0d");
    var cs = document.querySelector('meta[name="color-scheme"]');
    if (cs) cs.setAttribute("content", theme);
  } catch (e) { /* localStorage bloqueado: queda el default del CSS */ }
})();
