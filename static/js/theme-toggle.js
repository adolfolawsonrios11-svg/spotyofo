/* Classic (no module) theme toggle for auth pages. */
(function () {
  var KEY = "spotyofo-theme";
  var LIGHT = "#f4f6fa";
  var DARK = "#090a0d";

  function current() {
    var a = document.documentElement.getAttribute("data-theme");
    if (a === "light" || a === "dark") return a;
    return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  }

  function apply(theme) {
    var next = theme === "light" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    document.documentElement.style.colorScheme = next;
    try { localStorage.setItem(KEY, next); } catch (e) {}
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", next === "light" ? LIGHT : DARK);
    var cs = document.querySelector('meta[name="color-scheme"]');
    if (cs) cs.setAttribute("content", next);
    document.querySelectorAll("[data-theme-toggle]").forEach(function (btn) {
      var isLight = next === "light";
      btn.setAttribute("aria-pressed", isLight ? "true" : "false");
      btn.setAttribute("aria-label", isLight ? "Usar tema oscuro" : "Usar tema claro");
      btn.title = isLight ? "Tema oscuro" : "Tema claro";
    });
  }

  function bind() {
    document.querySelectorAll("[data-theme-toggle]").forEach(function (btn) {
      if (btn.dataset.themeBound) return;
      btn.dataset.themeBound = "1";
      btn.addEventListener("click", function () {
        apply(current() === "light" ? "dark" : "light");
      });
    });
    apply(current());
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();
