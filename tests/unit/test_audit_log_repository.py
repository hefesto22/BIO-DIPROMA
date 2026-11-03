"""Tests del AuditLogRepositorySQLite.

Cubre insert (append-only), lecturas por orden temporal, filtro por
usuario, nullability de user_id (login fallido con username desconocido),
y validación de parámetros.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.models.audit_entry import AuditEntry
from core.models.usuario import Usuario
from core.repositories.audit_log_repository_sqlite import AuditLogRepositorySQLite
from core.repositories.usuario_repository_sqlite import UsuarioRepositorySQLite
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "infrastructure" / "database" / "migrations"

ROLE_ID_ADMIN = 2


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "test_audit.db")
    MigrationsRunner(database, MIGRATIONS_DIR).run()
    return database


@pytest.fixture
def repo(db: Database) -> AuditLogRepositorySQLite:
    return AuditLogRepositorySQLite(db)


@pytest.fixture
def usuario_repo(db: Database) -> UsuarioRepositorySQLite:
    return UsuarioRepositorySQLite(db)


def _crear_usuario(repo: UsuarioRepositorySQLite, username: str) -> Usuario:
    """Helper: crea un usuario ADMIN con datos falsos y devuelve la instancia."""
    return repo.create(
        Usuario(
            id=None,
            username=username,
            password_hash="$2b$12$fake",
            full_name="Audit Tester",
            role_id=ROLE_ID_ADMIN,
        )
    )


def _nueva_entrada(
    user_id: int | None,
    action: str,
    timestamp: str,
    details: str | None = None,
) -> AuditEntry:
    return AuditEntry(
        id=None,
        user_id=user_id,
        action=action,
        machine_name="PC-TEST",
        timestamp=timestamp,
        details=details,
    )


# ── Tests: insert ─────────────────────────────────────────────────────────────


def test_insert_asigna_id(
    repo: AuditLogRepositorySQLite,
    usuario_repo: UsuarioRepositorySQLite,
) -> None:
    usuario = _crear_usuario(usuario_repo, "auditor")
    assert usuario.id is not None
    entry = _nueva_entrada(usuario.id, "login_ok", "2026-01-01T10:00:00+00:00")
    insertada = repo.insert(entry)
    assert insertada.id is not None and insertada.id > 0
    # Los campos de entrada deben conservarse idénticos.
    assert insertada.user_id == usuario.id
    assert insertada.action == "login_ok"


def test_insert_user_id_null_para_login_fail_con_username_desconocido(
    repo: AuditLogRepositorySQLite,
) -> None:
    entry = _nueva_entrada(
        None, "login_fail", "2026-01-01T10:00:00+00:00", details='{"intento":"xxx"}'
    )
    insertada = repo.insert(entry)
    assert insertada.user_id is None
    assert insertada.details == '{"intento":"xxx"}'


# ── Tests: list_recent ────────────────────────────────────────────────────────


def test_list_recent_vacio(repo: AuditLogRepositorySQLite) -> None:
    assert repo.list_recent(10) == []


def test_list_recent_ordena_desc_por_timestamp(
    repo: AuditLogRepositorySQLite,
    usuario_repo: UsuarioRepositorySQLite,
) -> None:
    usuario = _crear_usuario(usuario_repo, "u1")
    assert usuario.id is not None
    repo.insert(_nueva_entrada(usuario.id, "a1", "2026-01-01T10:00:00+00:00"))
    repo.insert(_nueva_entrada(usuario.id, "a2", "2026-01-03T10:00:00+00:00"))
    repo.insert(_nueva_entrada(usuario.id, "a3", "2026-01-02T10:00:00+00:00"))
    recientes = repo.list_recent(10)
    assert [e.action for e in recientes] == ["a2", "a3", "a1"]


def test_list_recent_respeta_limit(
    repo: AuditLogRepositorySQLite,
    usuario_repo: UsuarioRepositorySQLite,
) -> None:
    usuario = _crear_usuario(usuario_repo, "u2")
    assert usuario.id is not None
    for i in range(5):
        ts = f"2026-01-{i + 1:02d}T10:00:00+00:00"
        repo.insert(_nueva_entrada(usuario.id, f"a{i}", ts))
    assert len(repo.list_recent(3)) == 3


def test_list_recent_limit_invalido_levanta_value_error(
    repo: AuditLogRepositorySQLite,
) -> None:
    with pytest.raises(ValueError):
        repo.list_recent(0)
    with pytest.raises(ValueError):
        repo.list_recent(-5)


# ── Tests: list_by_user ───────────────────────────────────────────────────────


def test_list_by_user_filtra_correctamente(
    repo: AuditLogRepositorySQLite,
    usuario_repo: UsuarioRepositorySQLite,
) -> None:
    alice = _crear_usuario(usuario_repo, "alice")
    bob = _crear_usuario(usuario_repo, "bob")
    assert alice.id is not None and bob.id is not None
    repo.insert(_nueva_entrada(alice.id, "login_ok", "2026-01-01T09:00:00+00:00"))
    repo.insert(_nueva_entrada(bob.id, "login_ok", "2026-01-01T09:30:00+00:00"))
    repo.insert(_nueva_entrada(alice.id, "export", "2026-01-01T10:00:00+00:00"))

    entradas_alice = repo.list_by_user(alice.id)
    assert [e.action for e in entradas_alice] == ["export", "login_ok"]

    entradas_bob = repo.list_by_user(bob.id)
    assert len(entradas_bob) == 1
    assert entradas_bob[0].action == "login_ok"


def test_list_by_user_inexistente_devuelve_lista_vacia(
    repo: AuditLogRepositorySQLite,
) -> None:
    assert repo.list_by_user(9999) == []


# ── Tests: integración con FK ON DELETE SET NULL ──────────────────────────────


def test_borrar_usuario_pone_user_id_null_en_entradas_previas(
    repo: AuditLogRepositorySQLite,
    usuario_repo: UsuarioRepositorySQLite,
) -> None:
    """La FK ``audit_log.user_id`` tiene ON DELETE SET NULL: al borrar el
    usuario, sus entradas históricas conservan la acción pero pierden el
    vínculo. Esto preserva el log de auditoría aunque el usuario desaparezca.
    """
    usuario = _crear_usuario(usuario_repo, "goner")
    assert usuario.id is not None
    user_id = usuario.id
    repo.insert(_nueva_entrada(user_id, "login_ok", "2026-01-01T09:00:00+00:00"))
    usuario_repo.delete(user_id)

    # La entrada histórica persiste pero con user_id = NULL.
    recientes = repo.list_recent(10)
    assert len(recientes) == 1
    assert recientes[0].user_id is None
    assert recientes[0].action == "login_ok"
