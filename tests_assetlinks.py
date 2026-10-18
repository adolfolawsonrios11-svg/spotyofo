"""Prueba de /.well-known/assetlinks.json (Digital Asset Links para la app Android / TWA).

Uso: python tests_assetlinks.py http://127.0.0.1:5050
Solo lectura: no crea cuentas ni toca la base.
"""

import json
import sys
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5050").rstrip("/")
PATH = "/.well-known/assetlinks.json"
EXPECTED = (
    '[{"relation":["delegate_permission/common.handle_all_urls"],"target":{"namespace":"android_app",'
    '"package_name":"cl.ofotech.spotyofo","sha256_cert_fingerprints":["0E:B2:2C:6E:22:45:87:C3:9B:E8:E4:92:51:07:B7:B6:'
    'D6:A7:0D:DB:1E:84:C5:6F:BF:06:FC:25:BF:36:20:76"]}}]'
)
UAS = {
    "navegador": "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Mobile Safari/537.36",
    "Google": "Mozilla/5.0 (compatible; Google-Site-Verification/1.0)",
    "sin UA": "",
}
ok = True
count = 0


def check(name, cond, info=""):
    global ok, count
    count += 1
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  -> {str(info)[:300]}"))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


opener = urllib.request.build_opener(NoRedirect)


def get(path, method="GET", headers=None):
    req = urllib.request.Request(BASE + path, method=method, headers=headers or {})
    try:
        with opener.open(req, timeout=20) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


for label, ua in UAS.items():
    st, h, body = get(PATH, headers={"User-Agent": ua} if ua else {})
    check(f"[{label}] 200 sin redirección ni login", st == 200, (st, h.get("Location")))
    check(f"[{label}] Content-Type application/json", (h.get("Content-Type") or "").split(";")[0].strip() == "application/json", h.get("Content-Type"))
    check(f"[{label}] contenido exacto", body.decode("utf-8") == EXPECTED, body[:200])
    check(f"[{label}] no crea sesión (sin Set-Cookie)", not h.get("Set-Cookie"), h.get("Set-Cookie"))
    cc = h.get("Cache-Control") or ""
    check(f"[{label}] caché corta y pública", "public" in cc and "max-age=300" in cc, cc)

data = json.loads(EXPECTED)
check("JSON válido: una declaración handle_all_urls", len(data) == 1 and data[0]["relation"] == ["delegate_permission/common.handle_all_urls"])
target = data[0]["target"]
check("paquete cl.ofotech.spotyofo y huella SHA-256 de 32 bytes",
      target["namespace"] == "android_app" and target["package_name"] == "cl.ofotech.spotyofo"
      and len(target["sha256_cert_fingerprints"]) == 1 and len(target["sha256_cert_fingerprints"][0].split(":")) == 32, target)
st, h, body = get(PATH, method="HEAD")
check("HEAD 200 sin cuerpo", st == 200 and body == b"", (st, len(body)))
st, h, body = get(PATH, method="POST", headers={"Content-Type": "application/json"})
check("POST no permitido (405), sin redirigir al login", st == 405, (st, h.get("Location")))
st, h, body = get(PATH, headers={"Cookie": "spotyofo_session=basura-invalida"})
check("con una cookie de sesión inválida sigue dando 200", st == 200 and body.decode() == EXPECTED, st)
st, h, body = get("/.well-known/otra-cosa.json")
check("el resto de /.well-known/ sigue protegido (302 al login)", st in (302, 303) and "/login" in (h.get("Location") or ""), (st, h.get("Location")))
st, h, body = get("/api/health")
check("/api/health sigue respondiendo", st == 200, st)

print(f"\n{count} pruebas, {'todas OK' if ok else 'HAY FALLOS'}")
sys.exit(0 if ok else 1)
