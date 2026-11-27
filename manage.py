"""Administración de OFO Music por consola (no importa la app web ni escribe archivos de ella).

Uso en el servidor (ofoserv), como el usuario del servicio:

  cd /var/www/spotyofo
  sudo -u ofo bash -c 'set -a; . /etc/spotyofo.env; set +a; .venv/bin/python manage.py reset-password USUARIO'

Comandos:
  list-users                 Muestra las cuentas (sin contraseñas).
  reset-password USUARIO     Pide una contraseña nueva (oculta) y la guarda. Reactiva la cuenta,
                             quita el bloqueo por intentos y cierra sus sesiones abiertas.
  create-admin USUARIO       Crea un administrador (si es el primero, recibe los datos anteriores).
  make-admin USUARIO         Da rol de administrador a una cuenta existente y la activa.
  demo-reset                 Crea o repara la cuenta demo (SPOTY_DEMO_USER, por defecto «demo») con la
                             contraseña SPOTY_DEMO_PASSWORD y la deja en su estado inicial (lista de
                             muestra, algunos Me gusta e historial). Solo toca datos de la cuenta demo.
                             En ofoserv lo ejecuta cada noche el timer spotyofo-demo-reset.

Opción --password-stdin: lee la contraseña de la entrada estándar (para scripts de prueba).
Opción --no-claim (create-admin): no asigna los datos anteriores al nuevo administrador.
"""

import argparse
import getpass
import sys

import accounts
import demo
from db import connect, init_schema


def read_password(args, username):
    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        password = getpass.getpass(f"Contraseña nueva para {username}: ")
        again = getpass.getpass("Repítela: ")
        if password != again:
            raise SystemExit("Las contraseñas no coinciden. No se cambió nada.")
    accounts.validate_password(password, username)
    return password


def find(conn, username):
    with conn.cursor() as cur:
        cur.execute("SELECT id, username, role, status, is_demo FROM users WHERE username = %s", (username,))
        return cur.fetchone()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Cuentas de OFO Music")
    parser.add_argument("command", choices=["list-users", "reset-password", "create-admin", "make-admin", "demo-reset", "trash-list", "trash-restore", "assign-uploads"])
    parser.add_argument("username", nargs="?")
    parser.add_argument("--password-stdin", action="store_true")
    parser.add_argument("--no-claim", action="store_true")
    parser.add_argument("--display-name", default="")
    parser.add_argument("--ids", default="", help="assign-uploads: ids de canciones separados por coma")
    parser.add_argument("--all-unowned", action="store_true", help="assign-uploads: todas las subidas sin dueño")
    args = parser.parse_args(argv)

    conn = connect()
    init_schema(conn)
    try:
        if args.command == "list-users":
            for user in accounts.list_users(conn):
                tag = "  [demo]" if user["demo"] else ""
                print(f"{user['id']:>4}  {user['username']:<24} {user['role']:<6} {user['status_label']:<10} último ingreso: {user['last_login_at'] or '-'}{tag}")
            return 0
        if args.command == "demo-reset":
            password = sys.stdin.readline().rstrip("\r\n") if args.password_stdin else demo.demo_password()
            if not password:
                raise SystemExit("Falta SPOTY_DEMO_PASSWORD (en .env o /etc/spotyofo.env). No se cambió nada.")
            try:
                result = demo.reset(conn, password)
            except (demo.DemoError, accounts.AccountError) as exc:
                raise SystemExit(f"Demo sin cambios: {getattr(exc, 'message', exc)}") from exc
            seeded, removed = result["seeded"], result["removed"]
            print(
                f"Demo «{demo.DEMO_USER}» (id {result['user_id']}) reiniciada."
                f"{' Cuenta creada.' if result['created'] else ''}"
                f"{' Contraseña actualizada.' if result['password_changed'] else ''}"
                f" Borrado: {removed['playlists']} listas, {removed['likes']} Me gusta, {removed['history']} reproducciones."
                f" Inicial: lista «{demo.SEED_PLAYLIST}» con {seeded['tracks']} canciones, {seeded['likes']} Me gusta,"
                f" {seeded['history']} en el historial."
            )
            return 0
        if args.command == "trash-list":
            import uploads

            rows = uploads.trash_list()
            for row in rows:
                state = "" if row["archivo_existe"] else "  [sin archivo]"
                print(f"{row['ficha']}  #{row['track_id']}  {row['artist']} - {row['title']}  borrada {row['deleted_at']} por {row.get('deleted_by_name') or row.get('deleted_by')}{state}")
            print(f"{len(rows)} en la papelera ({uploads.TRASH_DIR})")
            return 0
        if args.command == "trash-restore":
            import uploads

            if not args.username:
                parser.error("Falta el nombre de la ficha (.json). Míralas con trash-list.")
            try:
                new_id = uploads.trash_restore(conn, args.username)
            except uploads.UploadError as exc:
                raise SystemExit(exc.message) from exc
            print(f"Restaurada como pista #{new_id}. Si quieres su copia liviana: python audiolite.py --all")
            return 0
        if not args.username:
            parser.error("Falta el nombre de usuario.")
        username = accounts.normalize_username(args.username)
        try:
            if args.command == "reset-password":
                user = find(conn, username)
                if not user:
                    raise SystemExit(f"No existe la cuenta «{username}».")
                password = read_password(args, username)
                accounts.set_password(conn, user["id"], password, username)
                with conn.cursor() as cur:
                    if user["status"] in ("blocked", "rejected", "pending") and user["role"] == "admin":
                        cur.execute("UPDATE users SET status = 'active' WHERE id = %s", (user["id"],))
                print(f"Contraseña de «{username}» actualizada. Sus sesiones abiertas se cerraron.")
            elif args.command == "create-admin":
                password = read_password(args, username)
                if accounts.admin_exists(conn):
                    user_id = accounts.create_user(conn, username, args.display_name, password, role="admin", status="active")
                    print(f"Administrador «{username}» creado (id {user_id}).")
                else:
                    user_id, moved = accounts.create_first_admin(
                        conn, username, args.display_name, password, claim=not args.no_claim
                    )
                    print(f"Administrador principal «{username}» creado (id {user_id}). Datos anteriores asignados: {moved or 'ninguno'}")
            elif args.command == "assign-uploads":
                user = find(conn, username)
                if not user:
                    raise SystemExit(f"No existe la cuenta «{username}».")
                if user.get("is_demo"):
                    raise SystemExit("La cuenta demo no puede ser dueña de canciones.")
                ids = [int(part) for part in args.ids.split(",") if part.strip().isdigit()]
                if not ids and not args.all_unowned:
                    raise SystemExit("Indica --ids 1,2,3 o --all-unowned.")
                with conn.cursor() as cur:
                    if ids:
                        marks = ",".join(["%s"] * len(ids))
                        cur.execute(
                            f"UPDATE tracks SET uploaded_by = %s WHERE source = 'user' AND uploaded_by IS NULL AND id IN ({marks})",
                            (user["id"], *ids),
                        )
                    else:
                        cur.execute("UPDATE tracks SET uploaded_by = %s WHERE source = 'user' AND uploaded_by IS NULL", (user["id"],))
                    changed = cur.rowcount
                print(f"{changed} canciones sin dueño ahora pertenecen a «{username}».")
            elif args.command == "make-admin":
                user = find(conn, username)
                if not user:
                    raise SystemExit(f"No existe la cuenta «{username}».")
                if user.get("is_demo"):
                    raise SystemExit("La cuenta demo no puede ser administradora.")
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE users SET role = 'admin', status = 'active', approved_at = COALESCE(approved_at, NOW()) WHERE id = %s",
                        (user["id"],),
                    )
                print(f"«{username}» ahora es administrador.")
        except accounts.AccountError as exc:
            raise SystemExit(exc.message) from exc
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
