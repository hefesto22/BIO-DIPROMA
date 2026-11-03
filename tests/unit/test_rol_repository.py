"""Tests del RolRepositorySQLite.

Valida lectura de los 4 roles canónicos provistos por el seed de
``002_auth.sql``: búsqueda por id, por code, listado completo y que los
permisos vienen deserializados a ``frozenset``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.models import permissions as perms
from core.repositories.rol_repository_sqlite import RolRepositorySQLite
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "infrastructure" / "database" / "migrations"


@pytest.fixture
def repo(tmp_path: Path) -> RolRepositorySQLite:
    db = Database(tmp_path / "test_rol.db")
    MigrationsRunner(db, MIGRATIONS_DIR).run()
    return RolRepositorySQLite(db)


def test_get_by_id_superadmin(repo: RolRepositorySQLite) -> None:
    rol = repo.get_by_id(1)
    assert rol is not None
    assert rol.code == perms.ROLE_SUPERADMIN
    assert rol.permissions == perms.ALL_PERMISSIONS


def test_get_by_id_inexistente_devuelve_none(repo: RolRepositorySQLite) -> None:
    assert repo.get_by_id(999) is None


def test_get_by_code_admin(repo: RolRepositorySQLite) -> None:
    rol = repo.get_by_code(perms.ROLE_ADMIN)
    assert rol is not None
    assert rol.id == 2
    # Sub-2.7a: ADMIN ahora tiene manage_users (gestiona usuarios) pero
    # NO manage_roles (no puede tocar la matriz de roles).
    assert perms.MANAGE_USERS in rol.permissions
    assert perms.MANAGE_ROLES not in rol.permissions
    assert perms.MANAGE_EMPLOYEES in rol.permissions


def test_get_by_code_case_sensitive(repo: RolRepositorySQLite) -> None:
    """Los códigos son case-sensitive — 'admin' ≠ 'ADMIN'."""
    assert repo.get_by_code("admin") is None
    assert repo.get_by_code(perms.ROLE_ADMIN) is not None


def test_list_all_devuelve_4_roles_en_orden(repo: RolRepositorySQLite) -> None:
    roles = repo.list_all()
    codigos = [r.code for r in roles]
    assert codigos == [
        perms.ROLE_SUPERADMIN,
        perms.ROLE_ADMIN,
        perms.ROLE_REPORTES,
        perms.ROLE_OPERADOR,
    ]


def test_permissions_son_frozenset_inmutable(repo: RolRepositorySQLite) -> None:
    """Los permisos deserializados deben ser ``frozenset`` — regla de diseño."""
    rol = repo.get_by_code(perms.ROLE_OPERADOR)
    assert rol is not None
    assert isinstance(rol.permissions, frozenset)
    assert rol.permissions == {perms.RUN_ZKTECO_SYNC, perms.VIEW_ATTENDANCE}
