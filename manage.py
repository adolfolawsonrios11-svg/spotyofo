"""Administración de SpotyOFO por consola (no importa la app web ni escribe archivos de ella).

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
    parser = argparse.ArgumentParser(description="Cuentas de SpotyOFO")
    parser.add_argument("command", choices=["list-users", "reset-password", "create-admin", "make-admin", "demo-reset"])
    parser.add_argument("username", nargs="?")
    parser.add_argument("--password-stdin", action="store_true")
    parser.add_argument("--no-claim", action="store_true")
    parser.add_argument("--display-name", default="")
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
