"""Tests de la migración 002_auth.sql.

Valida que el schema de auth queda correcto (tablas, índices, FKs),
que el seed de roles contiene los permisos esperados según la matriz
del PRD, y que los triggers de SUPERADMIN único funcionan tanto en
INSERT como en UPDATE.

Se usa el directorio real de migraciones (no uno temporal) porque
queremos validar exactamente el SQL que irá a producción.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

import pytest

from core.models import permissions as perms
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner

# Ruta al directorio real de migraciones (se ejecuta el SQL que va a producción).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "infrastructure" / "database" / "migrations"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def db_con_auth(tmp_path: Path) -> Database:
    """Database con migraciones 001 y 002 ya aplicadas sobre archivo nuevo."""
    db = Database(tmp_path / "test_auth.db")
    runner = MigrationsRunner(db, MIGRATIONS_DIR)
    runner.run()
    return db


# ── Helpers ───────────────────────────────────────────────────────────────────


def _fetch_role_by_code(conn: sqlite3.Connection, code: str) -> sqlite3.Row:
    # Anotamos el tipo explícito porque sqlite3.fetchone() retorna Any en los stubs
    # y mypy --strict no acepta retornar Any desde una función con tipo declarado.
    row: Optional[sqlite3.Row] = conn.execute(
        "SELECT id, code, name, description, permissions_json FROM roles WHERE code = ?",
        (code,),
    ).fetchone()
    assert row is not None, f"Rol {code} no existe en el seed"
    return row


def _insert_dummy_user(conn: sqlite3.Connection, username: str, role_code: str) -> None:
    """Inserta un usuario con datos falsos apto para validar triggers y FKs."""
    rol = _fetch_role_by_code(conn, role_code)
    conn.execute(
        "INSERT INTO usuarios "
        "(username, password_hash, full_name, role_id, created_at, updated_at) "
        "VALUES (?, 'dummy_hash', 'Nombre Falso', ?, "
        "'2026-01-01T00:00:00', '2026-01-01T00:00:00')",
        (username, rol["id"]),
    )


# ── Tests: schema y seed ──────────────────────────────────────────────────────


def test_tablas_creadas(db_con_auth: Database) -> None:
    """Las 3 tablas de auth y la de migraciones deben existir."""
    conn = db_con_auth.connect()
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    finally:
        conn.close()
    names = {r["name"] for r in rows}
    assert {"roles", "usuarios", "audit_log", "schema_migrations"} <= names


def test_seed_crea_los_4_roles_con_ids_fijos(db_con_auth: Database) -> None:
    """Los 4 roles canónicos deben existir con IDs 1..4 en orden esperado."""
    conn = db_con_auth.connect()
    try:
        rows = conn.execute("SELECT id, code FROM roles ORDER BY id").fetchall()
    finally:
        conn.close()
    assert [(r["id"], r["code"]) for r in rows] == [
        (1, perms.ROLE_SUPERADMIN),
        (2, perms.ROLE_ADMIN),
        (3, perms.ROLE_REPORTES),
        (4, perms.ROLE_OPERADOR),
    ]


def test_superadmin_tiene_todos_los_permisos(db_con_auth: Database) -> None:
    conn = db_con_auth.connect()
    try:
        row = _fetch_role_by_code(conn, perms.ROLE_SUPERADMIN)
    finally:
        conn.close()
    permisos = set(json.loads(row["permissions_json"]))
    assert permisos == set(perms.ALL_PERMISSIONS)


def test_admin_gestiona_usuarios_y_empleados_pero_no_roles(
    db_con_auth: Database,
) -> None:
    """Sub-2.7a actualizó el seed: ADMIN ahora tiene manage_users
    (puede crear usuarios) pero NO manage_roles (no puede tocar la
    matriz de permisos de los roles)."""
    conn = db_con_auth.connect()
    try:
        row = _fetch_role_by_code(conn, perms.ROLE_ADMIN)
    finally:
        conn.close()
    permisos = set(json.loads(row["permissions_json"]))
    assert perms.MANAGE_USERS in permisos
    assert perms.MANAGE_ROLES not in permisos
    assert perms.MANAGE_EMPLOYEES in permisos
    assert perms.EXPORT_REPORTS in permisos


def test_reportes_solo_tiene_permisos_de_exportar(db_con_auth: Database) -> None:
    conn = db_con_auth.connect()
    try:
        row = _fetch_role_by_code(conn, perms.ROLE_REPORTES)
    finally:
        conn.close()
    permisos = set(json.loads(row["permissions_json"]))
    assert permisos == {perms.EXPORT_REPORTS, perms.VIEW_EXPORT_HISTORY}


def test_operador_solo_sincroniza_y_ve_asistencia(db_con_auth: Database) -> None:
    conn = db_con_auth.connect()
    try:
        row = _fetch_role_by_code(conn, perms.ROLE_OPERADOR)
    finally:
        conn.close()
    permisos = set(json.loads(row["permissions_json"]))
    assert permisos == {perms.RUN_ZKTECO_SYNC, perms.VIEW_ATTENDANCE}


def test_todos_los_permisos_del_seed_son_conocidos(db_con_auth: Database) -> None:
    """Ningún permiso en el JSON debe ser huérfano respecto a ALL_PERMISSIONS."""
    conn = db_con_auth.connect()
    try:
        rows = conn.execute("SELECT permissions_json FROM roles").fetchall()
    finally:
        conn.close()
    for row in rows:
        for code in json.loads(row["permissions_json"]):
            assert code in perms.ALL_PERMISSIONS, f"Permiso desconocido: {code}"


# ── Tests: triggers de SUPERADMIN único ───────────────────────────────────────


def test_trigger_impide_insertar_segundo_superadmin(
    db_con_auth: Database,
) -> None:
    conn = db_con_auth.connect()
    try:
        _insert_dummy_user(conn, "super1", perms.ROLE_SUPERADMIN)
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            _insert_dummy_user(conn, "super2", perms.ROLE_SUPERADMIN)
    finally:
        conn.close()


def test_trigger_impide_promover_admin_a_superadmin(
    db_con_auth: Database,
) -> None:
    """UPDATE de role_id a SUPERADMIN debe abortar si ya hay uno."""
    conn = db_con_auth.connect()
    try:
        _insert_dummy_user(conn, "super1", perms.ROLE_SUPERADMIN)
        _insert_dummy_user(conn, "admin1", perms.ROLE_ADMIN)
        conn.commit()
        super_id = _fetch_role_by_code(conn, perms.ROLE_SUPERADMIN)["id"]
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE usuarios SET role_id = ? WHERE username = 'admin1'",
                (super_id,),
            )
    finally:
        conn.close()


def test_permite_multiples_admins_reportes_y_operadores(
    db_con_auth: Database,
) -> None:
    """El límite de unicidad aplica solo a SUPERADMIN."""
    conn = db_con_auth.connect()
    try:
        _insert_dummy_user(conn, "admin1", perms.ROLE_ADMIN)
        _insert_dummy_user(conn, "admin2", perms.ROLE_ADMIN)
        _insert_dummy_user(conn, "rep1", perms.ROLE_REPORTES)
        _insert_dummy_user(conn, "rep2", perms.ROLE_REPORTES)
        _insert_dummy_user(conn, "op1", perms.ROLE_OPERADOR)
        conn.commit()
        total = conn.execute("SELECT COUNT(*) AS c FROM usuarios").fetchone()["c"]
    finally:
        conn.close()
    assert total == 5


# ── Tests: integridad referencial ─────────────────────────────────────────────


def test_fk_impide_borrar_rol_con_usuarios(db_con_auth: Database) -> None:
    """ON DELETE RESTRICT: no se puede borrar un rol que tenga usuarios."""
    conn = db_con_auth.connect()
    try:
        _insert_dummy_user(conn, "admin1", perms.ROLE_ADMIN)
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM roles WHERE code = 'ADMIN'")
    finally:
        conn.close()


def test_audit_log_user_id_nulleable(db_con_auth: Database) -> None:
    """user_id puede ser NULL (login fallido con username inexistente)."""
    conn = db_con_auth.connect()
    try:
        conn.execute(
            "INSERT INTO audit_log (user_id, action, machine_name, timestamp) "
            "VALUES (NULL, 'login_fail', 'PC-TEST', '2026-01-01T00:00:00')"
        )
        conn.commit()
        row = conn.execute(
            "SELECT user_id, action FROM audit_log WHERE machine_name = 'PC-TEST'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["user_id"] is None
    assert row["action"] == "login_fail"


# ── Tests: idempotencia ───────────────────────────────────────────────────────


def test_migraciones_son_idempotentes(tmp_path: Path) -> None:
    """Dos corridas seguidas no deben duplicar el seed ni reventar."""
    db = Database(tmp_path / "idempot.db")
    runner = MigrationsRunner(db, MIGRATIONS_DIR)
    runner.run()
    segunda = runner.run()

    assert segunda == []  # Nada pendiente en la segunda corrida.

    conn = db.connect()
    try:
        total_roles = conn.execute("SELECT COUNT(*) AS c FROM roles").fetchone()["c"]
    finally:
        conn.close()
    assert total_roles == 4  # El INSERT OR IGNORE del seed no duplicó.
