"""Tests del AuthService.

Tests de integración entre el servicio y los repositorios SQLite reales
(sobre tmp_path). Un fake aísla el hashing para no pagar 12 rounds por
cada test; el BcryptHasher real se valida por separado en su módulo.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.models import permissions as perms
from core.models.usuario import Usuario
from core.repositories.audit_log_repository_sqlite import AuditLogRepositorySQLite
from core.repositories.rol_repository_sqlite import RolRepositorySQLite
from core.repositories.usuario_repository_sqlite import UsuarioRepositorySQLite
from core.services.audit_logger import AuditLogger
from core.services.auth_service import AuthService
from core.services.errors import (
    AccountInactiveError,
    AccountLockedError,
    InvalidCredentialsError,
)
from core.services.session import Session
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner
from infrastructure.security.bcrypt_hasher import BcryptHasher

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "infrastructure" / "database" / "migrations"

ROLE_ID_ADMIN = 2

MAX_ATTEMPTS = 5
LOCKOUT_MIN = 15


class FakeHasher(BcryptHasher):
    """Hasher que aprueba siempre el string 'correct' y rechaza todo lo demás.

    Hereda de BcryptHasher para satisfacer el tipo del constructor de
    AuthService (LSP). No llama al ``__init__`` del padre para evitar el
    check de cost_factor y el costo real del bcrypt.
    """

    def __init__(self) -> None:
        # No llamamos a super().__init__ a propósito — evita el chequeo
        # de cost_factor y el costo de bcrypt en cada test.
        self._cost_factor = 12
        self._log = logging.getLogger(self.__class__.__name__)

    def hash(self, password: str) -> str:
        return f"fake-hash-of-{password}"

    def verify(self, password: str, password_hash: str) -> bool:
        return password == "correct"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "test_auth_service.db")
    MigrationsRunner(database, MIGRATIONS_DIR).run()
    return database


@pytest.fixture
def service(db: Database) -> AuthService:
    usuario_repo = UsuarioRepositorySQLite(db)
    # Insertamos un usuario ADMIN con el "hash" fake.
    usuario_repo.create(
        Usuario(
            id=None,
            username="pepe",
            password_hash="fake-hash-of-correct",  # FakeHasher.verify lo aceptará
            full_name="Pepe Admin",
            role_id=ROLE_ID_ADMIN,
        )
    )
    audit = AuditLogger(
        AuditLogRepositorySQLite(db),
        machine_name="PC-TEST",
    )
    return AuthService(
        usuario_read=usuario_repo,
        usuario_write=usuario_repo,
        rol_read=RolRepositorySQLite(db),
        hasher=FakeHasher(),
        audit_logger=audit,
        max_failed_attempts=MAX_ATTEMPTS,
        lockout_duration_minutes=LOCKOUT_MIN,
    )


@pytest.fixture
def usuario_repo(db: Database) -> UsuarioRepositorySQLite:
    return UsuarioRepositorySQLite(db)


@pytest.fixture
def audit_repo(db: Database) -> AuditLogRepositorySQLite:
    return AuditLogRepositorySQLite(db)


# ── Tests: validación del constructor ─────────────────────────────────────────


def test_constructor_rechaza_max_attempts_cero(db: Database) -> None:
    with pytest.raises(ValueError):
        AuthService(
            usuario_read=UsuarioRepositorySQLite(db),
            usuario_write=UsuarioRepositorySQLite(db),
            rol_read=RolRepositorySQLite(db),
            hasher=FakeHasher(),
            audit_logger=AuditLogger(AuditLogRepositorySQLite(db), machine_name="PC-TEST"),
            max_failed_attempts=0,
            lockout_duration_minutes=15,
        )


def test_constructor_rechaza_lockout_cero(db: Database) -> None:
    with pytest.raises(ValueError):
        AuthService(
            usuario_read=UsuarioRepositorySQLite(db),
            usuario_write=UsuarioRepositorySQLite(db),
            rol_read=RolRepositorySQLite(db),
            hasher=FakeHasher(),
            audit_logger=AuditLogger(AuditLogRepositorySQLite(db), machine_name="PC-TEST"),
            max_failed_attempts=5,
            lockout_duration_minutes=0,
        )


# ── Tests: login exitoso ──────────────────────────────────────────────────────


def test_login_correcto_devuelve_session_con_permisos(service: AuthService) -> None:
    session = service.login("pepe", "correct")
    assert isinstance(session, Session)
    assert session.username == "pepe"
    assert session.role_code == perms.ROLE_ADMIN
    assert perms.MANAGE_EMPLOYEES in session.permissions


def test_login_correcto_resetea_failed_attempts(
    service: AuthService, usuario_repo: UsuarioRepositorySQLite
) -> None:
    # Simulamos que tenía 3 intentos previos fallidos.
    usuario = usuario_repo.get_by_username("pepe")
    assert usuario is not None and usuario.id is not None
    usuario_repo.update_login_state(usuario.id, failed_attempts=3, locked_until=None)

    service.login("pepe", "correct")

    actualizado = usuario_repo.get_by_username("pepe")
    assert actualizado is not None
    assert actualizado.failed_attempts == 0
    assert actualizado.locked_until is None


def test_login_correcto_registra_login_ok_en_audit(
    service: AuthService, audit_repo: AuditLogRepositorySQLite
) -> None:
    service.login("pepe", "correct")
    recientes = audit_repo.list_recent(10)
    actions = [e.action for e in recientes]
    assert "login_ok" in actions


# ── Tests: login incorrecto ───────────────────────────────────────────────────


def test_login_password_incorrecta_falla_con_mensaje_generico(
    service: AuthService,
) -> None:
    with pytest.raises(InvalidCredentialsError) as exc:
        service.login("pepe", "WRONG")
    # Mensaje genérico — no revela si fue user o pwd.
    assert "Usuario o contraseña" in str(exc.value)


def test_login_usuario_inexistente_da_el_mismo_mensaje(service: AuthService) -> None:
    with pytest.raises(InvalidCredentialsError) as exc:
        service.login("noexiste", "correct")
    assert "Usuario o contraseña" in str(exc.value)


def test_login_inexistente_registra_audit_con_user_id_null(
    service: AuthService, audit_repo: AuditLogRepositorySQLite
) -> None:
    with pytest.raises(InvalidCredentialsError):
        service.login("noexiste", "correct")
    recientes = audit_repo.list_recent(5)
    login_fails = [e for e in recientes if e.action == "login_fail"]
    assert len(login_fails) == 1
    assert login_fails[0].user_id is None


def test_login_password_mala_incrementa_contador(
    service: AuthService, usuario_repo: UsuarioRepositorySQLite
) -> None:
    with pytest.raises(InvalidCredentialsError):
        service.login("pepe", "WRONG")
    u = usuario_repo.get_by_username("pepe")
    assert u is not None
    assert u.failed_attempts == 1
    assert u.locked_until is None


# ── Tests: bloqueo tras 5 intentos ────────────────────────────────────────────


def test_bloqueo_tras_5_intentos_fallidos(
    service: AuthService, usuario_repo: UsuarioRepositorySQLite
) -> None:
    for _ in range(MAX_ATTEMPTS):
        with pytest.raises(InvalidCredentialsError):
            service.login("pepe", "WRONG")

    u = usuario_repo.get_by_username("pepe")
    assert u is not None
    assert u.failed_attempts == MAX_ATTEMPTS
    assert u.locked_until is not None
    # El lockout debe ser ~15 min en el futuro.
    assert u.locked_until > datetime.now(timezone.utc).isoformat(timespec="seconds")


def test_login_con_cuenta_bloqueada_lanza_account_locked(
    service: AuthService, usuario_repo: UsuarioRepositorySQLite
) -> None:
    for _ in range(MAX_ATTEMPTS):
        with pytest.raises(InvalidCredentialsError):
            service.login("pepe", "WRONG")

    # Incluso con password correcta, la cuenta sigue bloqueada.
    with pytest.raises(AccountLockedError):
        service.login("pepe", "correct")


def test_bloqueo_registra_account_locked_en_audit(
    service: AuthService, audit_repo: AuditLogRepositorySQLite
) -> None:
    for _ in range(MAX_ATTEMPTS):
        with pytest.raises(InvalidCredentialsError):
            service.login("pepe", "WRONG")

    recientes = audit_repo.list_recent(20)
    actions = [e.action for e in recientes]
    assert "account_locked" in actions


def test_lockout_expirado_permite_login_correcto(
    service: AuthService, usuario_repo: UsuarioRepositorySQLite
) -> None:
    """Opción B aprobada: tras locked_until, auto-desbloqueo en el próximo login OK."""
    u = usuario_repo.get_by_username("pepe")
    assert u is not None and u.id is not None
    # Forzamos locked_until EN EL PASADO.
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")
    usuario_repo.update_login_state(u.id, failed_attempts=MAX_ATTEMPTS, locked_until=past)

    # Debe permitir el login y limpiar el estado.
    session = service.login("pepe", "correct")
    assert session.username == "pepe"

    limpio = usuario_repo.get_by_username("pepe")
    assert limpio is not None
    assert limpio.failed_attempts == 0
    assert limpio.locked_until is None


# ── Tests: cuenta inactiva ────────────────────────────────────────────────────


def test_login_usuario_inactivo_lanza_account_inactive(
    service: AuthService, usuario_repo: UsuarioRepositorySQLite, db: Database
) -> None:
    # Desactivamos a pepe manualmente via SQL (no hay update público para is_active).
    u = usuario_repo.get_by_username("pepe")
    assert u is not None
    with db.transaction() as conn:
        conn.execute("UPDATE usuarios SET is_active = 0 WHERE id = ?", (u.id,))

    with pytest.raises(AccountInactiveError):
        service.login("pepe", "correct")
