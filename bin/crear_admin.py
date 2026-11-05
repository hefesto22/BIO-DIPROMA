"""Deja lista una instalación nueva (o recupera el acceso del Super Administrador).

Uso::

    python -m bin.crear_admin
    python -m bin.crear_admin --usuario admin --clave 12345678
    python -m bin.crear_admin --db-path "C:\\BIO-DIPROMA\\data\\zkteco_app.db"

Qué hace:
    - Crea la base de datos si no existe y aplica las migraciones (quedan
      los roles y los catálogos del sistema, sin empleados ni datos de
      ejemplo).
    - Si la base es nueva, crea lo mismo que la app al abrirse por primera
      vez (``InstalacionService``): ``admin`` (Super Administrador) y
      ``administrador`` (Administrador), los dos con ``12345678``, y el
      turno Operativo (L-V 07:00–17:00 con almuerzo de 12:00 a 13:00,
      sábado 07:00–12:00).
    - Si el usuario indicado ya existe, le pone la contraseña indicada, lo
      activa, le da rol SUPERADMIN y lo destraba (sirve también para
      recuperar el acceso).
    - Deja constancia en la bitácora (``audit_log``).

La contraseña NO pasa por la política de contraseñas (que pide letras y
números): es una decisión explícita de quien instala. Se recomienda
cambiarla desde Usuarios y roles una vez en uso.

Uso responsable: tiene acceso directo a la base de datos y no pide
inicio de sesión. Correrlo solo en la PC donde se instala la app.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import List, Optional, Sequence

import config
from core.models.usuario import Usuario
from core.repositories.audit_log_repository_sqlite import AuditLogRepositorySQLite
from core.repositories.rol_repository_sqlite import RolRepositorySQLite
from core.repositories.turno_repository_sqlite import TurnoRepositorySQLite
from core.repositories.usuario_repository_sqlite import UsuarioRepositorySQLite
from core.services.audit_logger import AuditLogger
from core.services.instalacion_service import CLAVE_INICIAL, InstalacionService
from infrastructure import paths
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner
from infrastructure.security.bcrypt_hasher import BcryptHasher

_SUPERADMIN = "SUPERADMIN"


def main(argv: Optional[List[str]] = None) -> int:
    """Punto de entrada. Devuelve el exit code para ``sys.exit``."""
    args = _parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format=config.LOG_FORMAT)

    if args.db_path is None:
        paths.ensure_runtime_dirs()
        db_path = config.DATABASE_PATH
    else:
        db_path = Path(args.db_path).resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)

    usuario = args.usuario.strip()
    if not usuario or not args.clave:
        print("✗ El usuario y la contraseña no pueden quedar vacíos.")
        return 1

    database = Database(db_path)
    MigrationsRunner(database, config.MIGRATIONS_DIR).run()

    usuarios = UsuarioRepositorySQLite(database)
    roles = RolRepositorySQLite(database)
    rol = roles.get_by_code(_SUPERADMIN)
    if rol is None or rol.id is None:
        print("✗ No se encontró el rol SUPERADMIN: la base de datos parece dañada.")
        return 2

    hasher = BcryptHasher(config.BCRYPT_COST_FACTOR)
    auditoria = AuditLogger(AuditLogRepositorySQLite(database))
    turnos = TurnoRepositorySQLite(database)
    instalacion = InstalacionService(
        usuario_read=usuarios,
        usuario_write=usuarios,
        rol_read=roles,
        turno_read=turnos,
        turno_write=turnos,
        hasher=hasher,
        audit_logger=auditoria,
    ).preparar(superadmin_username=usuario, clave_superadmin=args.clave)

    if usuario in instalacion.usuarios:
        _imprimir_instalacion(db_path, usuario, args.clave, instalacion.usuarios, instalacion.turno)
        return 0

    hash_clave = hasher.hash(args.clave)
    existente = usuarios.get_by_username(usuario)

    if existente is None:
        creado = usuarios.create(
            Usuario(
                id=None,
                username=usuario,
                password_hash=hash_clave,
                full_name=args.nombre,
                role_id=rol.id,
                is_active=True,
            )
        )
        assert creado.id is not None
        accion, user_id, texto = "superadmin_creado_cli", creado.id, "creado"
    else:
        assert existente.id is not None
        usuarios.update_profile(
            existente.id,
            full_name=existente.full_name or args.nombre,
            role_id=rol.id,
            is_active=True,
        )
        usuarios.update_password_hash(existente.id, hash_clave)
        usuarios.unlock_account(existente.id)
        accion, user_id, texto = "superadmin_restablecido_cli", existente.id, "restablecido"

    auditoria.log(
        action=accion,
        user_id=user_id,
        details=json.dumps({"username": usuario, "via": "bin.crear_admin"}),
    )

    print(f"✓ Usuario administrador {texto}.")
    print(f"  Base de datos: {db_path}")
    print(f"  Usuario:       {usuario}")
    print(f"  Contraseña:    {args.clave}")
    print(f"  Usuarios en total: {len(usuarios.list_all())}")
    return 0


def _imprimir_instalacion(
    db_path: Path,
    superadmin: str,
    clave: str,
    creados: Sequence[str],
    turno: Optional[str],
) -> None:
    print("✓ Instalación nueva lista.")
    print(f"  Base de datos: {db_path}")
    for username in creados:
        rol = "Super Administrador" if username == superadmin else "Administrador"
        contrasena = clave if username == superadmin else CLAVE_INICIAL
        print(f"  {rol + ':':21}{username} / {contrasena}")
    if turno:
        detalle = "L-V 07:00–17:00, almuerzo 12:00–13:00; sábado 07:00–12:00"
        print(f"  {'Turno:':21}{turno} ({detalle})")


def _parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m bin.crear_admin",
        description=f"Crea (o restablece) el usuario administrador de {config.APP_NAME}.",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Ruta de la base de datos. Por defecto, la de esta instalación (data/).",
    )
    parser.add_argument("--usuario", default="admin", help="Nombre de usuario (admin).")
    parser.add_argument("--clave", default="12345678", help="Contraseña (12345678).")
    parser.add_argument("--nombre", default="Administrador", help="Nombre para mostrar.")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
