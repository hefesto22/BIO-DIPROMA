"""Script CLI de recuperación del SUPERADMIN.

Sub-3.1 (production-ready): si el SUPERADMIN olvidó su password o quedó
bloqueado por intentos fallidos, este script permite resetear la
contraseña directamente contra la BD sin requerir login previo.

Uso:

    python -m bin.recover_superadmin
    python -m bin.recover_superadmin --db-path "dist/zkteco/data/zkteco_app.db"

Comportamiento:
    - Aplica las migraciones pendientes contra la BD indicada.
    - Busca el único usuario con rol ``SUPERADMIN``. Si no hay → aborta
      con instrucción de correr el setup_wizard.
    - Pide nueva contraseña dos veces (con eco oculto) y la valida con
      ``PasswordPolicy`` — mismas reglas que en el resto del sistema.
    - Hashea con bcrypt (cost factor de ``config``), persiste el hash y
      limpia ``failed_attempts`` + ``locked_until`` para destrabar la
      cuenta si estaba bloqueada.
    - Audita la operación con ``action='superadmin_recovered'`` (queda
      huella en ``audit_log``).

Uso responsable:
    Este script tiene acceso directo a la BD y NO requiere autenticación.
    Debe ejecutarse desde la máquina del operador con permisos de
    sistema, no desde la red. No incorporarlo a ninguna interfaz remota.
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import sys
from pathlib import Path
from typing import Optional

import config
from core.models.usuario import Usuario
from core.repositories.audit_log_repository_sqlite import AuditLogRepositorySQLite
from core.repositories.rol_repository_sqlite import RolRepositorySQLite
from core.repositories.usuario_repository_sqlite import UsuarioRepositorySQLite
from core.services.audit_logger import AuditLogger
from core.services.errors import WeakPasswordError
from core.services.password_policy import PasswordPolicy
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner
from infrastructure.security.bcrypt_hasher import BcryptHasher

_MAX_RETRIES = 3
_SUPERADMIN_CODE = "SUPERADMIN"


def main(argv: Optional[list[str]] = None) -> int:
    """Punto de entrada. Devuelve el exit code para ``sys.exit``."""
    args = _parse_args(argv)
    logging.basicConfig(level=config.LOG_LEVEL, format=config.LOG_FORMAT)

    db_path = _resolver_db_path(args.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    database = Database(db_path)
    MigrationsRunner(database, config.MIGRATIONS_DIR).run()

    usuario_repo = UsuarioRepositorySQLite(database)
    rol_repo = RolRepositorySQLite(database)
    audit = AuditLogger(AuditLogRepositorySQLite(database))

    superadmin_role = rol_repo.get_by_code(_SUPERADMIN_CODE)
    if superadmin_role is None or superadmin_role.id is None:
        print(
            "✗ No se encontró el rol SUPERADMIN en la BD. La instalación "
            "parece corrupta — corré primero las migraciones (abriendo la "
            "app o el setup_wizard)."
        )
        return 2

    superadmin = _resolver_superadmin(usuario_repo, superadmin_role.id)
    if superadmin is None:
        print(
            f"✗ No hay ningún usuario SUPERADMIN en {db_path}.\n"
            "  Corré primero el setup_wizard contra esa misma BD:\n"
            "    python -m bin.setup_wizard"
        )
        return 3

    print("═══════════════════════════════════════════════════════════════")
    print(f"  Recuperación de SUPERADMIN — {config.APP_NAME}")
    print(f"  BD: {db_path}")
    print(f"  Usuario: {superadmin.username}  (id={superadmin.id})")
    print("═══════════════════════════════════════════════════════════════")

    if not _confirmar_intencion(superadmin.username):
        print("Cancelado por el operador.")
        return 0

    policy = PasswordPolicy(
        min_length=config.MIN_PASSWORD_LENGTH,
        require_digit=config.PASSWORD_REQUIRE_DIGIT,
        require_letter=config.PASSWORD_REQUIRE_LETTER,
    )
    nueva_password = _pedir_password_confirmada()
    try:
        policy.validate(nueva_password)
    except WeakPasswordError as err:
        print(f"\n✗ Contraseña rechazada: {err.reason}")
        return 4

    hasher = BcryptHasher(config.BCRYPT_COST_FACTOR)
    nuevo_hash = hasher.hash(nueva_password)

    assert superadmin.id is not None
    usuario_repo.update_password_hash(superadmin.id, nuevo_hash)
    usuario_repo.unlock_account(superadmin.id)
    audit.log(
        action="superadmin_recovered",
        user_id=superadmin.id,
        details=json.dumps({"username": superadmin.username, "via": "cli"}),
    )

    print(f"\n✓ Contraseña actualizada y cuenta destrabada para '{superadmin.username}'.")
    print("  Ya podés iniciar sesión con la nueva contraseña.")
    return 0


# ── Parsing y resolución ──────────────────────────────────────────────────────


def _parse_args(argv: Optional[list[str]]) -> argparse.Namespace:
    """Parsea ``--db-path`` y devuelve el ``Namespace``."""
    parser = argparse.ArgumentParser(
        prog="bin.recover_superadmin",
        description=(
            "Recupera el acceso del único SUPERADMIN seteándole una nueva "
            "contraseña y destrabando lockouts. Útil si el operador olvidó "
            "su password."
        ),
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help=(
            "Ruta al archivo SQLite a recuperar. Útil para apuntar a la BD "
            "del .exe empaquetado (p.ej. dist/zkteco/data/zkteco_app.db)."
        ),
    )
    return parser.parse_args(argv)


def _resolver_db_path(explicit: Optional[str]) -> Path:
    """Devuelve el ``Path`` absoluto de la BD a recuperar."""
    if explicit is not None:
        return Path(explicit).resolve()
    return config.DATABASE_PATH


def _resolver_superadmin(
    repo: UsuarioRepositorySQLite, superadmin_role_id: int
) -> Optional[Usuario]:
    """Devuelve el SUPERADMIN actual (debería haber exactamente uno)."""
    for usuario in repo.list_all():
        if usuario.role_id == superadmin_role_id:
            return usuario
    return None


# ── Helpers CLI ───────────────────────────────────────────────────────────────


def _confirmar_intencion(username: str) -> bool:
    """Confirma con el operador antes de tocar la BD.

    Pide escribir explícitamente el username objetivo. Esto es una
    barrera baja contra ejecutar el script accidentalmente, pero
    suficiente como confirmación de intención.
    """
    print(
        "\n⚠  Vas a sobrescribir la contraseña del SUPERADMIN. La acción\n"
        "   queda registrada en el audit_log."
    )
    respuesta = input(f"   Para confirmar, escribí el username '{username}': ").strip()
    return respuesta == username


def _pedir_password_confirmada() -> str:
    """Pide password + confirmación sin eco (getpass), con reintentos."""
    for intento in range(1, _MAX_RETRIES + 1):
        p1 = getpass.getpass("Nueva contraseña: ")
        p2 = getpass.getpass("Confirmar contraseña: ")
        if not p1:
            restantes = _MAX_RETRIES - intento
            if restantes == 0:
                print("Se agotaron los intentos. Abortando.")
                sys.exit(11)
            print(f"  ✗ La contraseña no puede estar vacía. ({restantes} restantes)")
            continue
        if p1 != p2:
            restantes = _MAX_RETRIES - intento
            if restantes == 0:
                print("Se agotaron los intentos. Abortando.")
                sys.exit(12)
            print(f"  ✗ Las contraseñas no coinciden. ({restantes} restantes)")
            continue
        return p1
    raise AssertionError("unreachable")


if __name__ == "__main__":
    sys.exit(main())
