"""Tests del SetupWizardService.

Integración con repositorios SQLite reales sobre ``tmp_path``. Usa el
mismo FakeHasher que test_auth_service para evitar pagar bcrypt en
cada test — el bcrypt real ya está validado en test_bcrypt_hasher.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from core.models import permissions as perms
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
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner
from infrastructure.security.bcrypt_hasher import BcryptHasher

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "infrastructure" / "database" / "migrations"

ROLE_ID_SUPERADMIN = 1
ROLE_ID_ADMIN = 2


class FakeHasher(BcryptHasher):
    """Hasher determinístico — evita pagar 12 rounds de bcrypt por test."""

    def __init__(self) -> None:
        # No llamamos a super().__init__ a propósito — evita el chequeo
        # de cost_factor y el costo de bcrypt en cada test.
        self._cost_factor = 12
        self._log = logging.getLogger(self.__class__.__name__)

    def hash(self, password: str) -> str:
        return f"fake-hash-of-{password}"

    def verify(self, password: str, password_hash: str) -> bool:
        return password_hash == f"fake-hash-of-{password}"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "test_setup_wizard.db")
    MigrationsRunner(database, MIGRATIONS_DIR).run()
    return database


@pytest.fixture
def policy() -> PasswordPolicy:
    return PasswordPolicy(min_length=8, require_digit=True, require_letter=True)


@pytest.fixture
def service(db: Database, policy: PasswordPolicy) -> SetupWizardService:
    usuario_repo = UsuarioRepositorySQLite(db)
    audit = AuditLogger(AuditLogRepositorySQLite(db), machine_name="PC-TEST")
    return SetupWizardService(
        usuario_read=usuario_repo,
        usuario_write=usuario_repo,
        rol_read=RolRepositorySQLite(db),
        hasher=FakeHasher(),
        audit_logger=audit,
        password_policy=policy,
    )


@pytest.fixture
def usuario_repo(db: Database) -> UsuarioRepositorySQLite:
    return UsuarioRepositorySQLite(db)


@pytest.fixture
def audit_repo(db: Database) -> AuditLogRepositorySQLite:
    return AuditLogRepositorySQLite(db)


# ── Tests: is_first_run ───────────────────────────────────────────────────────


def test_is_first_run_true_en_bd_virgen(service: SetupWizardService) -> None:
    assert service.is_first_run() is True


def test_is_first_run_false_tras_crear_superadmin(service: SetupWizardService) -> None:
    service.create_superadmin("admin", "Secreto1!", "Administrador")
    assert service.is_first_run() is False


def test_is_first_run_true_si_solo_hay_admins_no_superadmins(
    service: SetupWizardService, usuario_repo: UsuarioRepositorySQLite
) -> None:
    """Un ADMIN pre-existente NO cuenta como SUPERADMIN."""
    from core.models.usuario import Usuario

    usuario_repo.create(
        Usuario(
            id=None,
            username="pepe",
            password_hash="fake-hash-of-x",
            full_name="Pepe Admin",
            role_id=ROLE_ID_ADMIN,
        )
    )
    assert service.is_first_run() is True


# ── Tests: create_superadmin feliz ────────────────────────────────────────────


def test_create_superadmin_devuelve_usuario_con_id(service: SetupWizardService) -> None:
    usuario = service.create_superadmin("admin", "Secreto1!", "Administrador")
    assert usuario.id is not None
    assert usuario.username == "admin"
    assert usuario.role_id == ROLE_ID_SUPERADMIN
    assert usuario.is_active is True


def test_create_superadmin_hashea_password(service: SetupWizardService) -> None:
    """Valida que el service delega al hasher antes de persistir.

    La garantía "bcrypt real no produce la password en claro" la cubre
    ``test_bcrypt_hasher.py``. Aquí solo verificamos que el service
    invocó al hasher (el FakeHasher marca el hash con un prefijo
    determinístico) en lugar de guardar el texto plano.
    """
    usuario = service.create_superadmin("admin", "Secreto1!", "Administrador")
    assert usuario.password_hash == "fake-hash-of-Secreto1!"
    assert usuario.password_hash != "Secreto1!"


def test_create_superadmin_registra_setup_completed_en_audit(
    service: SetupWizardService, audit_repo: AuditLogRepositorySQLite
) -> None:
    usuario = service.create_superadmin("admin", "Secreto1!", "Administrador")
    recientes = audit_repo.list_recent(5)
    setup_events = [e for e in recientes if e.action == "setup_completed"]
    assert len(setup_events) == 1
    assert setup_events[0].user_id == usuario.id


def test_rol_asignado_es_el_de_code_superadmin(service: SetupWizardService, db: Database) -> None:
    """Evitamos hardcodear id=1. Validamos vía code."""
    from core.repositories.rol_repository_sqlite import RolRepositorySQLite

    rol_repo = RolRepositorySQLite(db)
    rol_superadmin = rol_repo.get_by_code(perms.ROLE_SUPERADMIN)
    assert rol_superadmin is not None

    usuario = service.create_superadmin("admin", "Secreto1!", "Administrador")
    assert usuario.role_id == rol_superadmin.id


# ── Tests: create_superadmin errores ──────────────────────────────────────────


def test_create_superadmin_dos_veces_falla(service: SetupWizardService) -> None:
    service.create_superadmin("admin", "Secreto1!", "Administrador")
    with pytest.raises(SetupAlreadyCompletedError):
        service.create_superadmin("otro", "OtraClave2!", "Otro Admin")


def test_create_superadmin_password_debil_lanza_weak_password(
    service: SetupWizardService,
) -> None:
    with pytest.raises(WeakPasswordError):
        service.create_superadmin("admin", "short", "Administrador")


def test_create_superadmin_password_sin_digito_lanza(
    service: SetupWizardService,
) -> None:
    with pytest.raises(WeakPasswordError):
        service.create_superadmin("admin", "SinNumero!", "Administrador")


def test_create_superadmin_username_vacio_lanza_value_error(
    service: SetupWizardService,
) -> None:
    with pytest.raises(ValueError):
        service.create_superadmin("", "Secreto1!", "Administrador")


def test_create_superadmin_full_name_vacio_lanza_value_error(
    service: SetupWizardService,
) -> None:
    with pytest.raises(ValueError):
        service.create_superadmin("admin", "Secreto1!", "")


def test_create_superadmin_username_duplicado_lanza(
    service: SetupWizardService, usuario_repo: UsuarioRepositorySQLite
) -> None:
    """Si ya existe un ADMIN con username 'admin', el wizard debe detectar el conflicto."""
    from core.models.usuario import Usuario

    usuario_repo.create(
        Usuario(
            id=None,
            username="admin",
            password_hash="fake-hash-of-x",
            full_name="Admin Pre-existente",
            role_id=ROLE_ID_ADMIN,
        )
    )
    with pytest.raises(DuplicateUsernameError) as exc:
        service.create_superadmin("admin", "Secreto1!", "Nuevo Admin")
    assert exc.value.username == "admin"


def test_create_superadmin_no_registra_audit_si_falla(
    service: SetupWizardService, audit_repo: AuditLogRepositorySQLite
) -> None:
    with pytest.raises(WeakPasswordError):
        service.create_superadmin("admin", "weak", "Administrador")
    events = [e for e in audit_repo.list_recent(5) if e.action == "setup_completed"]
    assert events == []
