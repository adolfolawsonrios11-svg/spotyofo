/** Toggle claro/oscuro: persiste en localStorage; actualiza theme-color. */
const KEY = "spotyofo-theme";
const LIGHT_COLOR = "#f4f6fa";
const DARK_COLOR = "#090a0d";

export function currentTheme() {
  const attr = document.documentElement.getAttribute("data-theme");
  if (attr === "light" || attr === "dark") return attr;
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export function applyTheme(theme, { persist = true } = {}) {
  const next = theme === "light" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  document.documentElement.style.colorScheme = next;
  if (persist) {
    try { localStorage.setItem(KEY, next); } catch (_) { /* ignore */ }
  }
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", next === "light" ? LIGHT_COLOR : DARK_COLOR);
  const cs = document.querySelector('meta[name="color-scheme"]');
  if (cs) cs.setAttribute("content", next);
  document.querySelectorAll("[data-theme-toggle]").forEach((btn) => {
    const isLight = next === "light";
    btn.setAttribute("aria-pressed", isLight ? "true" : "false");
    btn.setAttribute("aria-label", isLight ? "Usar tema oscuro" : "Usar tema claro");
    btn.title = isLight ? "Tema oscuro" : "Tema claro";
  });
  return next;
}

export function toggleTheme() {
  return applyTheme(currentTheme() === "light" ? "dark" : "light");
}

export function bindThemeToggle(root = document) {
  root.querySelectorAll("[data-theme-toggle]").forEach((btn) => {
    if (btn.dataset.themeBound) return;
    btn.dataset.themeBound = "1";
    btn.addEventListener("click", () => toggleTheme());
  });
  applyTheme(currentTheme(), { persist: false });
}
