"""Tests del ``UsuarioAdminService`` (Sub-2.7a).

Integración con ``UsuarioRepositorySQLite`` real + bcrypt real
+ ``AuditLogger`` real sobre ``tmp_path``. Cubre el happy path y las
6 reglas de seguridad (R1-R6) del módulo de admin de usuarios.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Tuple

import pytest

from core.models import permissions as perms
from core.repositories.audit_log_repository_sqlite import (
    AuditLogRepositorySQLite,
)
from core.repositories.rol_repository_sqlite import RolRepositorySQLite
from core.repositories.usuario_repository_sqlite import (
    UsuarioRepositorySQLite,
)
from core.services.audit_logger import AuditLogger
from core.services.errors import (
    CannotAssignSuperadminRoleError,
    CannotChangeSelfRoleError,
    CannotDeactivateOnlySuperadminError,
    CannotDeactivateSelfError,
    CannotResetSuperadminPasswordError,
    DuplicateUsernameError,
    InvalidUsernameError,
    UsuarioNotFoundError,
    WeakPasswordError,
)
from core.services.password_policy import PasswordPolicy
from core.services.usuario_admin_service import UsuarioAdminService
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner
from infrastructure.security.bcrypt_hasher import BcryptHasher

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "infrastructure" / "database" / "migrations"

# IDs canónicos del seed de 002_auth.sql.
ROLE_SUPERADMIN_ID = 1
ROLE_ADMIN_ID = 2
ROLE_REPORTES_ID = 3
ROLE_OPERADOR_ID = 4

VALID_PWD = "ClaveSegura1"  # cumple PasswordPolicy default


class _FakeHasher(BcryptHasher):
    """Hasher determinístico — evita pagar 12 rounds de bcrypt por test.

    Mismo patrón que ``test_setup_wizard_service.FakeHasher``: salta el
    ``super().__init__`` para no chocar con el chequeo de cost_factor>=12
    y produce hashes predecibles para que las aserciones sean robustas.
    """

    def __init__(self) -> None:
        self._cost_factor = 12
        self._log = logging.getLogger(self.__class__.__name__)

    def hash(self, password: str) -> str:
        return f"fake-hash-of-{password}"

    def verify(self, password: str, password_hash: str) -> bool:
        return password_hash == f"fake-hash-of-{password}"


@pytest.fixture
def setup(tmp_path: Path) -> Tuple[UsuarioAdminService, Database, int]:
    """Crea DB + servicio con todas sus dependencias reales.

    Returns:
        (service, db, superadmin_id) — el SUPERADMIN ya está sembrado
        para que las pruebas de R1-R6 puedan referenciarlo como actor.
    """
    db = Database(tmp_path / "test_usuario_admin.db")
    MigrationsRunner(db, MIGRATIONS_DIR).run()
    usuario_repo = UsuarioRepositorySQLite(db)
    rol_repo = RolRepositorySQLite(db)
    audit_repo = AuditLogRepositorySQLite(db)
    hasher = _FakeHasher()
    policy = PasswordPolicy(min_length=8, require_digit=True, require_letter=True)
    service = UsuarioAdminService(
        usuario_read=usuario_repo,
        usuario_write=usuario_repo,
        rol_read=rol_repo,
        hasher=hasher,
        password_policy=policy,
        audit_logger=AuditLogger(audit_repo, machine_name="test"),
    )
    # Sembrar SUPERADMIN inicial — el trigger SQL impide que se cree por
    # vías normales si ya existe, así que lo creamos directo en BD.
    super_id = service.create_usuario(
        username="superadmin",
        full_name="Super Admin",
        role_id=ROLE_SUPERADMIN_ID,
        password=VALID_PWD,
        actor_user_id=None,  # bootstrap: aún no hay usuarios — audit con user_id NULL
        actor_role_code=perms.ROLE_SUPERADMIN,
    ).id
    assert super_id is not None
    return service, db, super_id


def _count_audit(db: Database, action: str) -> int:
    with db.transaction() as conn:
        cur = conn.execute("SELECT COUNT(*) AS n FROM audit_log WHERE action = ?", (action,))
        return int(cur.fetchone()["n"])


# ── Happy path ────────────────────────────────────────────────────────────────


def test_create_usuario_admin_ok(setup: Tuple[UsuarioAdminService, Database, int]) -> None:
    service, db, super_id = setup
    creado = service.create_usuario(
        username="contadora",
        full_name="Maria Lopez",
        role_id=ROLE_ADMIN_ID,
        password=VALID_PWD,
        actor_user_id=super_id,
        actor_role_code=perms.ROLE_SUPERADMIN,
    )
    assert creado.id is not None
    assert creado.username == "contadora"
    assert creado.role_id == ROLE_ADMIN_ID
    # Password se hashea — nunca debe quedar en plano.
    assert creado.password_hash != VALID_PWD
    assert creado.password_hash == f"fake-hash-of-{VALID_PWD}"
    # Audit log: 1 superadmin (setup) + 1 contadora.
    assert _count_audit(db, "usuario_created") == 2


def test_list_usuarios_resuelve_rol(
    setup: Tuple[UsuarioAdminService, Database, int],
) -> None:
    service, _, super_id = setup
    service.create_usuario(
        "operador1", "Op1", ROLE_OPERADOR_ID, VALID_PWD, super_id, perms.ROLE_SUPERADMIN
    )
    lista = service.list_usuarios()
    nombres = {row.usuario.username: row.rol_code for row in lista}
    assert nombres["superadmin"] == perms.ROLE_SUPERADMIN
    assert nombres["operador1"] == perms.ROLE_OPERADOR


def test_update_usuario_cambia_full_name(
    setup: Tuple[UsuarioAdminService, Database, int],
) -> None:
    service, db, super_id = setup
    creado = service.create_usuario(
        "ana", "Ana Vieja", ROLE_REPORTES_ID, VALID_PWD, super_id, perms.ROLE_SUPERADMIN
    )
    assert creado.id is not None
    service.update_usuario(
        creado.id,
        "Ana Nueva",
        ROLE_REPORTES_ID,
        actor_user_id=super_id,
        actor_role_code=perms.ROLE_SUPERADMIN,
    )
    actualizado = service.get_usuario(creado.id)
    assert actualizado.full_name == "Ana Nueva"
    assert _count_audit(db, "usuario_updated") == 1


# ── Validación de campos ─────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["", "ab", "a" * 33, "espacio dentro", "símbolo!"])
def test_create_usuario_rechaza_username_invalido(
    setup: Tuple[UsuarioAdminService, Database, int],
    bad: str,
) -> None:
    service, _, super_id = setup
    with pytest.raises(InvalidUsernameError):
        service.create_usuario(bad, "X", ROLE_ADMIN_ID, VALID_PWD, super_id, perms.ROLE_SUPERADMIN)


def test_create_usuario_password_debil(
    setup: Tuple[UsuarioAdminService, Database, int],
) -> None:
    service, _, super_id = setup
    with pytest.raises(WeakPasswordError):
        service.create_usuario("user1", "X", ROLE_ADMIN_ID, "abc", super_id, perms.ROLE_SUPERADMIN)


def test_create_usuario_duplicate(
    setup: Tuple[UsuarioAdminService, Database, int],
) -> None:
    service, _, super_id = setup
    service.create_usuario(
        "rep1", "R1", ROLE_REPORTES_ID, VALID_PWD, super_id, perms.ROLE_SUPERADMIN
    )
    with pytest.raises(DuplicateUsernameError):
        service.create_usuario(
            "rep1", "Otro", ROLE_REPORTES_ID, VALID_PWD, super_id, perms.ROLE_SUPERADMIN
        )


# ── R1: ADMIN no puede asignar SUPERADMIN ────────────────────────────────────


def test_admin_no_puede_crear_superadmin(
    setup: Tuple[UsuarioAdminService, Database, int],
) -> None:
    service, _, super_id = setup
    admin = service.create_usuario(
        "admin1", "Admin", ROLE_ADMIN_ID, VALID_PWD, super_id, perms.ROLE_SUPERADMIN
    )
    assert admin.id is not None
    with pytest.raises(CannotAssignSuperadminRoleError):
        service.create_usuario(
            "fake_super",
            "Fake",
            ROLE_SUPERADMIN_ID,
            VALID_PWD,
            actor_user_id=admin.id,
            actor_role_code=perms.ROLE_ADMIN,
        )


def test_admin_no_puede_promover_a_superadmin(
    setup: Tuple[UsuarioAdminService, Database, int],
) -> None:
    service, _, super_id = setup
    admin = service.create_usuario(
        "admin2", "Admin2", ROLE_ADMIN_ID, VALID_PWD, super_id, perms.ROLE_SUPERADMIN
    )
    assert admin.id is not None
    target = service.create_usuario(
        "rep2", "Rep2", ROLE_REPORTES_ID, VALID_PWD, super_id, perms.ROLE_SUPERADMIN
    )
    assert target.id is not None
    with pytest.raises(CannotAssignSuperadminRoleError):
        service.update_usuario(
            target.id,
            "Rep2",
            ROLE_SUPERADMIN_ID,
            actor_user_id=admin.id,
            actor_role_code=perms.ROLE_ADMIN,
        )


# ── R2: no auto-desactivarse ─────────────────────────────────────────────────


def test_no_puede_desactivarse_a_si_mismo(
    setup: Tuple[UsuarioAdminService, Database, int],
) -> None:
    service, _, super_id = setup
    with pytest.raises(CannotDeactivateSelfError):
        service.deactivate_usuario(super_id, actor_user_id=super_id)


# ── R3: no desactivar al único SUPERADMIN ────────────────────────────────────


def test_no_puede_desactivar_unico_superadmin(
    setup: Tuple[UsuarioAdminService, Database, int],
) -> None:
    service, _, super_id = setup
    # Creamos un admin con permiso para desactivar.
    admin = service.create_usuario(
        "admin3", "A3", ROLE_ADMIN_ID, VALID_PWD, super_id, perms.ROLE_SUPERADMIN
    )
    assert admin.id is not None
    with pytest.raises(CannotDeactivateOnlySuperadminError):
        service.deactivate_usuario(super_id, actor_user_id=admin.id)


# ── R4: ADMIN no puede resetear password del SUPERADMIN ──────────────────────


def test_admin_no_puede_resetear_password_superadmin(
    setup: Tuple[UsuarioAdminService, Database, int],
) -> None:
    service, _, super_id = setup
    admin = service.create_usuario(
        "admin4", "A4", ROLE_ADMIN_ID, VALID_PWD, super_id, perms.ROLE_SUPERADMIN
    )
    assert admin.id is not None
    with pytest.raises(CannotResetSuperadminPasswordError):
        service.reset_password(
            super_id,
            "OtraClave99",
            actor_user_id=admin.id,
            actor_role_code=perms.ROLE_ADMIN,
        )


def test_superadmin_si_puede_resetear_su_password(
    setup: Tuple[UsuarioAdminService, Database, int],
) -> None:
    service, db, super_id = setup
    service.reset_password(
        super_id,
        "NuevaClave99",
        actor_user_id=super_id,
        actor_role_code=perms.ROLE_SUPERADMIN,
    )
    assert _count_audit(db, "usuario_password_reset") == 1


# ── R5: no cambiar el propio rol ─────────────────────────────────────────────


def test_no_puede_cambiar_su_propio_rol(
    setup: Tuple[UsuarioAdminService, Database, int],
) -> None:
    service, _, super_id = setup
    admin = service.create_usuario(
        "admin5", "A5", ROLE_ADMIN_ID, VALID_PWD, super_id, perms.ROLE_SUPERADMIN
    )
    assert admin.id is not None
    with pytest.raises(CannotChangeSelfRoleError):
        service.update_usuario(
            admin.id,
            "A5",
            ROLE_REPORTES_ID,
            actor_user_id=admin.id,
            actor_role_code=perms.ROLE_ADMIN,
        )


# ── Lifecycle: desactivar / reactivar / desbloquear ──────────────────────────


def test_deactivate_y_reactivate_es_idempotente(
    setup: Tuple[UsuarioAdminService, Database, int],
) -> None:
    service, db, super_id = setup
    target = service.create_usuario(
        "op2", "Op2", ROLE_OPERADOR_ID, VALID_PWD, super_id, perms.ROLE_SUPERADMIN
    )
    assert target.id is not None
    service.deactivate_usuario(target.id, actor_user_id=super_id)
    service.deactivate_usuario(target.id, actor_user_id=super_id)  # no-op
    assert _count_audit(db, "usuario_deactivated") == 1
    actualizado = service.get_usuario(target.id)
    assert actualizado.is_active is False
    service.reactivate_usuario(target.id, actor_user_id=super_id)
    service.reactivate_usuario(target.id, actor_user_id=super_id)  # no-op
    assert _count_audit(db, "usuario_reactivated") == 1


def test_unlock_resetea_failed_attempts(
    setup: Tuple[UsuarioAdminService, Database, int],
) -> None:
    service, db, super_id = setup
    target = service.create_usuario(
        "op3", "Op3", ROLE_OPERADOR_ID, VALID_PWD, super_id, perms.ROLE_SUPERADMIN
    )
    assert target.id is not None
    # Forzar estado bloqueado simulando lo que hace AuthService.
    with db.transaction() as conn:
        conn.execute(
            "UPDATE usuarios SET failed_attempts=5, locked_until='2099-01-01T00:00:00' "
            "WHERE id=?",
            (target.id,),
        )
    service.unlock_usuario(target.id, actor_user_id=super_id)
    desbloqueado = service.get_usuario(target.id)
    assert desbloqueado.failed_attempts == 0
    assert desbloqueado.locked_until is None
    assert _count_audit(db, "usuario_unlocked") == 1


def test_get_usuario_inexistente_falla(
    setup: Tuple[UsuarioAdminService, Database, int],
) -> None:
    service, _, _ = setup
    with pytest.raises(UsuarioNotFoundError):
        service.get_usuario(9999)
