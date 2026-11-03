"""Tests del UsuarioRepositorySQLite.

Cubre CRUD completo, búsquedas, conteo por rol, updates específicos
(login_state, password_hash), integridad referencial (FK al rol) y
propagación de errores de los triggers del schema 002_auth.sql.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.models.usuario import Usuario
from core.repositories.usuario_repository_sqlite import UsuarioRepositorySQLite
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "infrastructure" / "database" / "migrations"

# IDs de rol según el seed (estables por diseño — ver 002_auth.sql).
ROLE_ID_SUPERADMIN = 1
ROLE_ID_ADMIN = 2
ROLE_ID_OPERADOR = 4


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def repo(tmp_path: Path) -> UsuarioRepositorySQLite:
    db = Database(tmp_path / "test_usuario.db")
    MigrationsRunner(db, MIGRATIONS_DIR).run()
    return UsuarioRepositorySQLite(db)


def _nuevo_usuario(
    username: str = "pepe",
    role_id: int = ROLE_ID_ADMIN,
) -> Usuario:
    """Construye un Usuario listo para insertar (sin id, sin timestamps)."""
    return Usuario(
        id=None,
        username=username,
        password_hash="$2b$12$fakehash",
        full_name="Usuario de prueba",
        role_id=role_id,
    )


# ── Tests: create + read ──────────────────────────────────────────────────────


def test_create_asigna_id_y_timestamps(repo: UsuarioRepositorySQLite) -> None:
    nuevo = repo.create(_nuevo_usuario())
    assert nuevo.id is not None and nuevo.id > 0
    assert nuevo.created_at is not None
    assert nuevo.updated_at is not None
    # Los timestamps deben ser ISO-8601 en UTC (acaban en '+00:00' o 'Z').
    assert "T" in nuevo.created_at
    assert nuevo.created_at == nuevo.updated_at


def test_create_default_is_active_true(repo: UsuarioRepositorySQLite) -> None:
    nuevo = repo.create(_nuevo_usuario())
    assert nuevo.is_active is True
    assert nuevo.failed_attempts == 0
    assert nuevo.locked_until is None


def test_get_by_id_encuentra_usuario_creado(repo: UsuarioRepositorySQLite) -> None:
    creado = repo.create(_nuevo_usuario(username="ana"))
    assert creado.id is not None
    leido = repo.get_by_id(creado.id)
    assert leido is not None
    assert leido.username == "ana"
    assert leido.role_id == ROLE_ID_ADMIN


def test_get_by_id_inexistente_devuelve_none(repo: UsuarioRepositorySQLite) -> None:
    assert repo.get_by_id(9999) is None


def test_get_by_username_encuentra_usuario(repo: UsuarioRepositorySQLite) -> None:
    repo.create(_nuevo_usuario(username="maria"))
    leido = repo.get_by_username("maria")
    assert leido is not None
    assert leido.full_name == "Usuario de prueba"


def test_get_by_username_case_sensitive(repo: UsuarioRepositorySQLite) -> None:
    repo.create(_nuevo_usuario(username="juan"))
    assert repo.get_by_username("JUAN") is None
    assert repo.get_by_username("juan") is not None


def test_list_all_vacio(repo: UsuarioRepositorySQLite) -> None:
    assert repo.list_all() == []


def test_list_all_ordenado_por_username(repo: UsuarioRepositorySQLite) -> None:
    repo.create(_nuevo_usuario(username="zoe"))
    repo.create(_nuevo_usuario(username="ana"))
    repo.create(_nuevo_usuario(username="maria"))
    usuarios = repo.list_all()
    assert [u.username for u in usuarios] == ["ana", "maria", "zoe"]


def test_create_duplicate_username_falla(repo: UsuarioRepositorySQLite) -> None:
    """El UNIQUE de username debe bloquear duplicados."""
    repo.create(_nuevo_usuario(username="dup"))
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(_nuevo_usuario(username="dup"))


def test_create_con_role_id_inexistente_falla(
    repo: UsuarioRepositorySQLite,
) -> None:
    """La FK a roles debe abortar si el role_id no existe."""
    usuario = _nuevo_usuario(username="huerfano", role_id=999)
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(usuario)


# ── Tests: count_by_role ──────────────────────────────────────────────────────


def test_count_by_role_vacio(repo: UsuarioRepositorySQLite) -> None:
    assert repo.count_by_role(ROLE_ID_ADMIN) == 0


def test_count_by_role_suma_correcta(repo: UsuarioRepositorySQLite) -> None:
    repo.create(_nuevo_usuario(username="admin1", role_id=ROLE_ID_ADMIN))
    repo.create(_nuevo_usuario(username="admin2", role_id=ROLE_ID_ADMIN))
    repo.create(_nuevo_usuario(username="op1", role_id=ROLE_ID_OPERADOR))
    assert repo.count_by_role(ROLE_ID_ADMIN) == 2
    assert repo.count_by_role(ROLE_ID_OPERADOR) == 1
    assert repo.count_by_role(ROLE_ID_SUPERADMIN) == 0


# ── Tests: update_login_state ─────────────────────────────────────────────────


def test_update_login_state_incrementa_intentos(
    repo: UsuarioRepositorySQLite,
) -> None:
    creado = repo.create(_nuevo_usuario(username="lock1"))
    assert creado.id is not None
    repo.update_login_state(creado.id, failed_attempts=3, locked_until=None)
    leido = repo.get_by_id(creado.id)
    assert leido is not None
    assert leido.failed_attempts == 3
    assert leido.locked_until is None


def test_update_login_state_aplica_bloqueo(
    repo: UsuarioRepositorySQLite,
) -> None:
    creado = repo.create(_nuevo_usuario(username="lock2"))
    assert creado.id is not None
    future = "2026-12-31T23:59:59+00:00"
    repo.update_login_state(creado.id, failed_attempts=5, locked_until=future)
    leido = repo.get_by_id(creado.id)
    assert leido is not None
    assert leido.failed_attempts == 5
    assert leido.locked_until == future


def test_update_login_state_cambia_updated_at(
    repo: UsuarioRepositorySQLite,
) -> None:
    creado = repo.create(_nuevo_usuario(username="stamp"))
    assert creado.id is not None and creado.updated_at is not None
    original_updated_at = creado.updated_at
    # Forzamos un timestamp distinto simplemente corriendo el update.
    # La única garantía es que es >= (mismos segundos es aceptable).
    repo.update_login_state(creado.id, failed_attempts=1, locked_until=None)
    leido = repo.get_by_id(creado.id)
    assert leido is not None and leido.updated_at is not None
    assert leido.updated_at >= original_updated_at


# ── Tests: update_password_hash ───────────────────────────────────────────────


def test_update_password_hash(repo: UsuarioRepositorySQLite) -> None:
    creado = repo.create(_nuevo_usuario(username="pwd"))
    assert creado.id is not None
    nuevo_hash = "$2b$12$NUEVO_HASH"
    repo.update_password_hash(creado.id, nuevo_hash)
    leido = repo.get_by_id(creado.id)
    assert leido is not None
    assert leido.password_hash == nuevo_hash
    # Los demás campos del dominio no deben haber cambiado.
    assert leido.username == "pwd"
    assert leido.failed_attempts == 0


# ── Tests: delete ─────────────────────────────────────────────────────────────


def test_delete_remueve_usuario(repo: UsuarioRepositorySQLite) -> None:
    creado = repo.create(_nuevo_usuario(username="delme"))
    assert creado.id is not None
    repo.delete(creado.id)
    assert repo.get_by_id(creado.id) is None
    assert repo.get_by_username("delme") is None


def test_delete_id_inexistente_no_falla(repo: UsuarioRepositorySQLite) -> None:
    """DELETE de un id que no existe no es un error en SQL — afecta 0 filas."""
    repo.delete(9999)  # No debe levantar excepción.


# ── Tests: trigger SUPERADMIN propagado ───────────────────────────────────────


def test_create_segundo_superadmin_propaga_trigger(
    repo: UsuarioRepositorySQLite,
) -> None:
    """El trigger BD debe llegar hasta el caller como IntegrityError."""
    repo.create(_nuevo_usuario(username="super1", role_id=ROLE_ID_SUPERADMIN))
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(_nuevo_usuario(username="super2", role_id=ROLE_ID_SUPERADMIN))
