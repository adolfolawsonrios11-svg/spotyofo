# -*- coding: utf-8 -*-
"""Pruebas del tema claro/oscuro (archivos + login vivo opcional).

Uso:
  python tests_theme.py
  python tests_theme.py http://127.0.0.1:5050

No toca la base. Solo lectura.
"""
from __future__ import annotations

import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = (sys.argv[1] if len(sys.argv) > 1 else "").rstrip("/")
ok = True
count = 0


def check(name, cond, info=""):
    global ok, count
    count += 1
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  -> {str(info)[:350]}"))


boot = (ROOT / "static/js/theme-boot.js").read_text(encoding="utf-8")
theme_mod = (ROOT / "static/js/theme.js").read_text(encoding="utf-8")
toggle = (ROOT / "static/js/theme-toggle.js").read_text(encoding="utf-8")
app_css = (ROOT / "static/css/app.css").read_text(encoding="utf-8")
auth_css = (ROOT / "static/css/auth.css").read_text(encoding="utf-8")
index = (ROOT / "templates/index.html").read_text(encoding="utf-8")
auth = (ROOT / "templates/auth.html").read_text(encoding="utf-8")
main = (ROOT / "static/js/main.js").read_text(encoding="utf-8")

check("theme-boot.js define data-theme y localStorage", "data-theme" in boot and "spotyofo-theme" in boot and "prefers-color-scheme" in boot)
check("theme-boot actualiza theme-color", 'theme-color' in boot and "#f4f6fa" in boot and "#090a0d" in boot)
check("theme.js exporta toggle/bind", "export function toggleTheme" in theme_mod and "export function bindThemeToggle" in theme_mod)
check("theme-toggle.js classic para auth", "data-theme-toggle" in toggle and "localStorage" in toggle)
check("app.css tiene [data-theme=light] y dark tokens", 'html[data-theme="light"]' in app_css and "--bg:" in app_css and ".theme-toggle" in app_css)
check("auth.css tiene tema claro", 'html[data-theme="light"]' in auth_css and ".theme-toggle" in auth_css)
check("index carga theme-boot antes del CSS", bool(re.search(r'theme-boot\.js.*\n\s*<link rel="stylesheet" href="/static/css/app\.css', index)))
check("index tiene boton data-theme-toggle", 'data-theme-toggle' in index and "icon-sun" in index and "icon-moon" in index)
check("auth tiene boot + toggle + script classic", "theme-boot.js" in auth and "theme-toggle.js" in auth and "data-theme-toggle" in auth)
check("main.js importa bindThemeToggle", "bindThemeToggle" in main and "theme.js" in main)
check("assets version bump 20261105a en main", "20261105a" in main)
check("sin script inline de tema en index (CSP)", "<script>try" not in index and "localStorage.getItem" not in index)

# contraste basico: texto claro vs fondo oscuro y viceversa (hex presentes)
check("tema claro usa texto oscuro #0f172a", "--text: #0f172a" in app_css)
check("tema claro usa fondo #f4f6fa", "--bg: #f4f6fa" in app_css)
check("sheets fuerzan texto claro sobre generos", ".sheet, .artist-hero, .hero, .lib-card.likes" in app_css)

if BASE:
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(NoRedirect)

    def get(path):
        req = urllib.request.Request(BASE + path, method="GET")
        try:
            with opener.open(req, timeout=20) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    st, h, body = get("/login")
    text = body.decode("utf-8", "replace")
    check("GET /login 200", st == 200, st)
    check("login incluye theme-boot.js", "theme-boot.js" in text, text[:200])
    check("login incluye theme-toggle", "data-theme-toggle" in text)
    csp = h.get("Content-Security-Policy") or h.get("content-security-policy") or ""
    check("CSP script-src self (sin unsafe-inline scripts)", "script-src 'self'" in csp and "script-src 'self' 'unsafe-inline'" not in csp, csp)
    st2, h2, body2 = get("/static/js/theme-boot.js")
    check("theme-boot.js servido 200", st2 == 200 and b"spotyofo-theme" in body2, st2)
    st3, _, body3 = get("/static/css/app.css")
    check("app.css servido con data-theme light", st3 == 200 and b'data-theme="light"' in body3, st3)

print(f"\n{count} pruebas, {'todas OK' if ok else 'HAY FALLOS'}")
sys.exit(0 if ok else 1)
