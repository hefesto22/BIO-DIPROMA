"""Tests de "último acceso" y "actividad reciente" del módulo Usuarios y roles.

Cubre las tres capas nuevas:
    - ``AuditLogRepositorySQLite.last_timestamp_by_user`` (SQL real),
    - ``UsuarioActividadService``,
    - ``UsuariosController.ultimos_accesos`` / ``actividad_reciente``,
      incluida la regla R7 (un ADMIN no ve la actividad del SUPERADMIN).
"""

from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

from core.models import permissions as perms
from core.models.audit_entry import AuditEntry
from core.models.rol import Rol
from core.models.usuario import Usuario
from core.repositories.audit_log_repository_sqlite import AuditLogRepositorySQLite
from core.repositories.usuario_repository_sqlite import UsuarioRepositorySQLite
from core.services.errors import PermissionDeniedError
from core.services.permission_service import PermissionService
from core.services.session import Session
from core.services.usuario_actividad_service import UsuarioActividadService
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner
from ui.controllers.usuarios_controller import UsuariosController

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "infrastructure" / "database" / "migrations"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "test.db")
    MigrationsRunner(db, MIGRATIONS_DIR).run()
    usuarios = UsuarioRepositorySQLite(db)
    for username, role_id in (("admin", 1), ("rrhh", 2), ("operador", 4)):
        usuarios.create(
            Usuario(
                id=None,
                username=username,
                password_hash="h",
                full_name=username.title(),
                role_id=role_id,
            )
        )
    return db


def _log(repo: AuditLogRepositorySQLite, user_id: int, action: str, ts: str) -> None:
    repo.insert(
        AuditEntry(id=None, user_id=user_id, action=action, machine_name="pc", timestamp=ts)
    )


# ── Repositorio ───────────────────────────────────────────────────────────────


def test_last_timestamp_by_user_devuelve_el_maximo_por_usuario(database: Database) -> None:
    repo = AuditLogRepositorySQLite(database)
    _log(repo, 1, "login_ok", "2026-10-01T10:00:00+00:00")
    _log(repo, 1, "login_ok", "2026-10-03T08:00:00+00:00")
    _log(repo, 2, "login_ok", "2026-10-02T09:00:00+00:00")
    _log(repo, 3, "login_fail", "2026-10-04T09:00:00+00:00")

    ultimos = repo.last_timestamp_by_user("login_ok")

    assert ultimos == {1: "2026-10-03T08:00:00+00:00", 2: "2026-10-02T09:00:00+00:00"}


def test_last_timestamp_by_user_ignora_entradas_sin_usuario(database: Database) -> None:
    repo = AuditLogRepositorySQLite(database)
    repo.insert(
        AuditEntry(
            id=None,
            user_id=None,
            action="login_ok",
            machine_name="pc",
            timestamp="2026-10-05T00:00:00+00:00",
        )
    )
    assert repo.last_timestamp_by_user("login_ok") == {}


# ── Servicio ──────────────────────────────────────────────────────────────────


def test_actividad_reciente_limita_y_ordena_desc(database: Database) -> None:
    repo = AuditLogRepositorySQLite(database)
    for dia in range(1, 6):
        _log(repo, 2, "login_ok", f"2026-10-0{dia}T10:00:00+00:00")
    svc = UsuarioActividadService(repo)

    actividad = svc.actividad_reciente(2, limit=3)

    assert [a.timestamp[:10] for a in actividad] == ["2026-10-05", "2026-10-04", "2026-10-03"]


def test_actividad_reciente_rechaza_limit_no_positivo(database: Database) -> None:
    svc = UsuarioActividadService(AuditLogRepositorySQLite(database))
    with pytest.raises(ValueError):
        svc.actividad_reciente(1, limit=0)


def test_ultimos_accesos_usa_login_ok(database: Database) -> None:
    repo = AuditLogRepositorySQLite(database)
    _log(repo, 3, "login_ok", "2026-10-07T07:00:00+00:00")
    _log(repo, 3, "usuario_created", "2026-10-08T07:00:00+00:00")
    assert UsuarioActividadService(repo).ultimos_accesos() == {3: "2026-10-07T07:00:00+00:00"}


# ── Controller ────────────────────────────────────────────────────────────────


class _StubActividad:
    def __init__(self) -> None:
        self.pedidos: List[int] = []

    def ultimos_accesos(self) -> dict[int, str]:
        return {1: "2026-10-01T00:00:00+00:00"}

    def actividad_reciente(self, user_id: int, limit: int = 10) -> List[AuditEntry]:
        self.pedidos.append(user_id)
        return [
            AuditEntry(id=1, user_id=user_id, action="login_ok", machine_name="pc", timestamp="x")
        ]


class _StubAdmin:
    def get_usuario(self, user_id: int) -> Usuario:
        role_id = 1 if user_id == 1 else 2
        return Usuario(id=user_id, username="u", password_hash="h", full_name="U", role_id=role_id)


class _StubRoles:
    _ROLES = {
        1: Rol(id=1, code="SUPERADMIN", name="Super", description="", permissions=frozenset()),
        2: Rol(id=2, code="ADMIN", name="Admin", description="", permissions=frozenset()),
    }

    def get_by_id(self, role_id: int) -> Rol | None:
        return self._ROLES.get(role_id)

    def list_all(self) -> List[Rol]:
        return list(self._ROLES.values())


def _controller(
    role_code: str, permisos: set[str], con_actividad: bool = True
) -> tuple[UsuariosController, _StubActividad]:
    actividad = _StubActividad()
    ctrl = UsuariosController(
        session=Session(
            user_id=7,
            username="actor",
            role_id=2,
            role_code=role_code,
            permissions=frozenset(permisos),
        ),
        permission_service=PermissionService(),
        usuario_admin_service=_StubAdmin(),  # type: ignore[arg-type]
        rol_read=_StubRoles(),  # type: ignore[arg-type]
        actividad_service=actividad if con_actividad else None,  # type: ignore[arg-type]
    )
    return ctrl, actividad


def test_controller_expone_actor() -> None:
    ctrl, _ = _controller(perms.ROLE_SUPERADMIN, {perms.MANAGE_USERS})
    assert ctrl.actor_user_id == 7
    assert ctrl.actor_es_superadmin is True


def test_superadmin_ve_actividad_de_cualquiera() -> None:
    ctrl, actividad = _controller(perms.ROLE_SUPERADMIN, {perms.MANAGE_USERS})
    assert len(ctrl.actividad_reciente(1)) == 1
    assert actividad.pedidos == [1]


def test_admin_no_ve_actividad_del_superadmin_r7() -> None:
    ctrl, actividad = _controller(perms.ROLE_ADMIN, {perms.MANAGE_USERS})
    assert ctrl.actividad_reciente(1) == []
    assert actividad.pedidos == []
    assert len(ctrl.actividad_reciente(2)) == 1


def test_sin_servicio_de_actividad_devuelve_vacio() -> None:
    ctrl, _ = _controller(perms.ROLE_SUPERADMIN, {perms.MANAGE_USERS}, con_actividad=False)
    assert ctrl.ultimos_accesos() == {}
    assert ctrl.actividad_reciente(2) == []


def test_actividad_requiere_manage_users() -> None:
    ctrl, _ = _controller(perms.ROLE_OPERADOR, {perms.VIEW_ATTENDANCE})
    with pytest.raises(PermissionDeniedError):
        ctrl.ultimos_accesos()
    with pytest.raises(PermissionDeniedError):
        ctrl.actividad_reciente(2)
