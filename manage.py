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

Opción --password-stdin: lee la contraseña de la entrada estándar (para scripts de prueba).
Opción --no-claim (create-admin): no asigna los datos anteriores al nuevo administrador.
"""

import argparse
import getpass
import sys

import accounts
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
        cur.execute("SELECT id, username, role, status FROM users WHERE username = %s", (username,))
        return cur.fetchone()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Cuentas de SpotyOFO")
    parser.add_argument("command", choices=["list-users", "reset-password", "create-admin", "make-admin"])
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
                print(f"{user['id']:>4}  {user['username']:<24} {user['role']:<6} {user['status_label']:<10} último ingreso: {user['last_login_at'] or '-'}")
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
