"""Entrypoint CLI del Setup Wizard.

Se ejecuta con:

    python -m bin.setup_wizard

Comportamiento:
    - Corre migraciones si hace falta (reusa ``MigrationsRunner``).
    - Pregunta si el sistema ya está configurado. Si ya hay un
      SUPERADMIN, imprime un mensaje y sale con código 0.
    - Si es primer arranque, pide username + nombre completo + password
      (dos veces, usando ``getpass`` para no eco) y crea al SUPERADMIN.
    - Hasta 3 reintentos por error de validación para no cerrar la
      terminal ante un typo.

Este script es la forma "oficial" de configurar la instalación sin UI.
La GUI equivalente aparecerá en Sub-entregable 1.5 consumiendo el
mismo ``SetupWizardService``.
"""

from __future__ import annotations

import getpass
import logging
import sys
from typing import Callable

import config
from core.repositories.audit_log_repository_sqlite import AuditLogRepositorySQLite
from core.repositories.rol_repository_sqlite import RolRepositorySQLite
from core.repositories.usuario_repository_sqlite import UsuarioRepositorySQLite
from core.services.audit_logger import AuditLogger
from core.services.errors import (
    DuplicateUsernameError,
    SetupAlreadyCompletedError,
    WeakPasswordError,
)
from core.services.password_policy import PasswordPolicy
from core.services.setup_wizard_service import SetupWizardService
from infrastructure import paths
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner
from infrastructure.security.bcrypt_hasher import BcryptHasher

_MAX_RETRIES = 3


def main() -> int:
    """Ejecuta el wizard. Devuelve el exit code para ``sys.exit``."""
    logging.basicConfig(level=config.LOG_LEVEL, format=config.LOG_FORMAT)
    # Asegurar carpetas de runtime — la BD vive en data/zkteco_app.db
    # y SQLite no crea la carpeta padre por sí solo.
    paths.ensure_runtime_dirs()

    service = _build_service()
    if not service.is_first_run():
        print("El sistema ya está configurado. El asistente no es necesario.")
        return 0

    print("═══════════════════════════════════════════════════════════════")
    print(f"  Asistente de configuración inicial — {config.APP_NAME}")
    print("  Se creará el único SUPERADMIN del sistema.")
    print("═══════════════════════════════════════════════════════════════")

    username = _pedir_con_reintentos(
        "Nombre de usuario",
        _leer_texto_no_vacio,
    )
    full_name = _pedir_con_reintentos(
        "Nombre completo",
        _leer_texto_no_vacio,
    )
    password = _pedir_password_confirmada()

    try:
        usuario = service.create_superadmin(username, password, full_name)
    except SetupAlreadyCompletedError as err:
        # Carrera muy improbable: alguien creó el SUPERADMIN entre el
        # is_first_run() y el create. Lo reportamos claro y salimos.
        print(f"\n{err}")
        return 1
    except WeakPasswordError as err:
        print(f"\nContraseña rechazada: {err.reason}")
        return 2
    except DuplicateUsernameError as err:
        print(f"\n{err}")
        return 3

    print(f"\n✓ SUPERADMIN creado con id={usuario.id}. ¡Listo!")
    return 0


# ── Helpers CLI ───────────────────────────────────────────────────────────────


def _build_service() -> SetupWizardService:
    """Compone el servicio con las dependencias reales (SQLite, bcrypt)."""
    database = Database(config.DATABASE_PATH)
    MigrationsRunner(database, config.MIGRATIONS_DIR).run()

    usuario_repo = UsuarioRepositorySQLite(database)
    audit = AuditLogger(AuditLogRepositorySQLite(database))
    policy = PasswordPolicy(
        min_length=config.MIN_PASSWORD_LENGTH,
        require_digit=config.PASSWORD_REQUIRE_DIGIT,
        require_letter=config.PASSWORD_REQUIRE_LETTER,
    )
    return SetupWizardService(
        usuario_read=usuario_repo,
        usuario_write=usuario_repo,
        rol_read=RolRepositorySQLite(database),
        hasher=BcryptHasher(config.BCRYPT_COST_FACTOR),
        audit_logger=audit,
        password_policy=policy,
    )


def _leer_texto_no_vacio(prompt: str) -> str:
    """Lee una línea del usuario; recorta y rechaza vacíos."""
    valor = input(f"{prompt}: ").strip()
    if not valor:
        raise ValueError(f"{prompt} no puede estar vacío.")
    return valor


def _pedir_con_reintentos(
    prompt: str,
    leer: Callable[[str], str],
) -> str:
    """Llama a ``leer`` hasta ``_MAX_RETRIES`` veces. Sale del proceso si agota."""
    for intento in range(1, _MAX_RETRIES + 1):
        try:
            return leer(prompt)
        except ValueError as err:
            restantes = _MAX_RETRIES - intento
            if restantes == 0:
                print(f"Se agotaron los intentos para '{prompt}'. Abortando.")
                sys.exit(10)
            print(f"  ✗ {err} ({restantes} intentos restantes)")
    # Inalcanzable — el loop retorna o llama sys.exit, pero mypy no lo infiere.
    raise AssertionError("unreachable")


def _pedir_password_confirmada() -> str:
    """Pide password + confirmación sin eco (getpass).

    Reintenta hasta ``_MAX_RETRIES`` veces si las dos entradas no coinciden.
    La validación de complejidad la hace ``PasswordPolicy`` más adelante —
    aquí solo nos preocupa la confirmación y que no sea vacío.
    """
    for intento in range(1, _MAX_RETRIES + 1):
        p1 = getpass.getpass("Contraseña: ")
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
