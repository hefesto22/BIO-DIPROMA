"""Tests de la migración 005_reportes.sql.

Valida que el schema de la tabla ``descargas_reportes`` queda correcto
(columnas, índices, FKs, CHECKs) y que las restricciones del CHECK
funcionan a nivel SQLite.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "infrastructure" / "database" / "migrations"


@pytest.fixture
def db(tmp_path: Path) -> Database:
    """Database con todas las migraciones aplicadas (incluida 005)."""
    database = Database(tmp_path / "test_005.db")
    MigrationsRunner(database, MIGRATIONS_DIR).run()
    return database


def test_tabla_descargas_reportes_existe(db: Database) -> None:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master " "WHERE type='table' AND name='descargas_reportes'"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1


def test_indices_creados(db: Database) -> None:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master " "WHERE type='index' AND tbl_name='descargas_reportes'"
        ).fetchall()
    finally:
        conn.close()
    nombres = {r["name"] for r in rows}
    assert "idx_descargas_reportes_fecha_hora" in nombres
    assert "idx_descargas_reportes_user_id" in nombres


def test_check_tipo_reporte_rechaza_valor_invalido(db: Database) -> None:
    """Solo ``ASISTENCIA`` está permitido por el CHECK."""
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO descargas_reportes "
                "(user_id, fecha_hora_utc, tipo_reporte, rango_desde, rango_hasta, "
                " ruta_archivo, filas_exportadas) "
                "VALUES (NULL, '2026-04-24T10:00:00', 'PEPITO', "
                "'2026-04-01', '2026-04-30', '/tmp/x.xlsx', 0)"
            )


def test_check_rango_invalido_rechaza_hasta_anterior_a_desde(db: Database) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO descargas_reportes "
                "(user_id, fecha_hora_utc, tipo_reporte, rango_desde, rango_hasta, "
                " ruta_archivo, filas_exportadas) "
                "VALUES (NULL, '2026-04-24T10:00:00', 'ASISTENCIA', "
                "'2026-04-30', '2026-04-01', '/tmp/x.xlsx', 0)"
            )


def test_check_filas_exportadas_no_negativas(db: Database) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO descargas_reportes "
                "(user_id, fecha_hora_utc, tipo_reporte, rango_desde, rango_hasta, "
                " ruta_archivo, filas_exportadas) "
                "VALUES (NULL, '2026-04-24T10:00:00', 'ASISTENCIA', "
                "'2026-04-01', '2026-04-30', '/tmp/x.xlsx', -1)"
            )


def test_insert_minimo_funciona(db: Database) -> None:
    """El path feliz: insert sin user_id, con tipo y rango válidos."""
    with db.transaction() as conn:
        cursor = conn.execute(
            "INSERT INTO descargas_reportes "
            "(user_id, fecha_hora_utc, tipo_reporte, rango_desde, rango_hasta, "
            " ruta_archivo, filas_exportadas) "
            "VALUES (NULL, '2026-04-24T10:00:00', 'ASISTENCIA', "
            "'2026-04-01', '2026-04-30', '/tmp/x.xlsx', 12)"
        )
        assert cursor.lastrowid is not None and cursor.lastrowid > 0
