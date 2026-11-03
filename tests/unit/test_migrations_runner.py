"""Tests unitarios de MigrationsRunner.

Usan archivos SQLite temporales (tmp_path) en vez de ":memory:" porque
:memory: crea una BD distinta por conexión y el runner abre/cierra varias.
tmp_path se limpia solo al terminar la sesión de pytest.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path: Path) -> Database:
    """Database apuntando a un archivo SQLite nuevo por test."""
    return Database(tmp_path / "test.db")


@pytest.fixture
def migrations_dir(tmp_path: Path) -> Path:
    """Directorio vacío listo para recibir migraciones de prueba."""
    d = tmp_path / "migrations"
    d.mkdir()
    return d


# ── Helpers ───────────────────────────────────────────────────────────────────


def _write_migration(migrations_dir: Path, filename: str, sql: str) -> None:
    (migrations_dir / filename).write_text(sql, encoding="utf-8")


def _schema_migrations_ddl() -> str:
    """DDL de la tabla de tracking, igual que 001_init.sql real."""
    return (
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  version TEXT PRIMARY KEY,"
        "  name TEXT NOT NULL,"
        "  applied_at TEXT NOT NULL"
        ");"
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_primera_corrida_aplica_migracion(tmp_db: Database, migrations_dir: Path) -> None:
    _write_migration(migrations_dir, "001_init.sql", _schema_migrations_ddl())
    runner = MigrationsRunner(tmp_db, migrations_dir)

    applied = runner.run()

    assert applied == ["001"]
    conn = tmp_db.connect()
    try:
        rows = conn.execute("SELECT version, name FROM schema_migrations").fetchall()
    finally:
        conn.close()
    assert [(r["version"], r["name"]) for r in rows] == [("001", "init")]


def test_segunda_corrida_no_aplica_nada(tmp_db: Database, migrations_dir: Path) -> None:
    _write_migration(migrations_dir, "001_init.sql", _schema_migrations_ddl())
    runner = MigrationsRunner(tmp_db, migrations_dir)

    runner.run()
    applied_segunda = runner.run()

    assert applied_segunda == []


def test_orden_numerico_respetado(tmp_db: Database, migrations_dir: Path) -> None:
    # Se escriben fuera de orden a propósito para verificar el sort.
    _write_migration(
        migrations_dir, "010_tardia.sql", "CREATE TABLE IF NOT EXISTS tardia (id INTEGER);"
    )
    _write_migration(
        migrations_dir, "002_segunda.sql", "CREATE TABLE IF NOT EXISTS segunda (id INTEGER);"
    )
    _write_migration(migrations_dir, "001_init.sql", _schema_migrations_ddl())
    runner = MigrationsRunner(tmp_db, migrations_dir)

    applied = runner.run()

    assert applied == ["001", "002", "010"]


def test_archivos_no_sql_se_ignoran(tmp_db: Database, migrations_dir: Path) -> None:
    _write_migration(migrations_dir, "001_init.sql", _schema_migrations_ddl())
    (migrations_dir / ".gitkeep").write_text("", encoding="utf-8")
    (migrations_dir / "README.md").write_text("# notas", encoding="utf-8")
    (migrations_dir / "backup.sql.bak").write_text("sql invalido", encoding="utf-8")
    # Falta el prefijo numérico — debe ignorarse aunque termine en .sql.
    (migrations_dir / "sin_prefijo.sql").write_text("DROP TABLE x;", encoding="utf-8")
    runner = MigrationsRunner(tmp_db, migrations_dir)

    applied = runner.run()

    assert applied == ["001"]


def test_sql_invalido_hace_rollback_y_relanza(tmp_db: Database, migrations_dir: Path) -> None:
    _write_migration(migrations_dir, "001_init.sql", _schema_migrations_ddl())
    _write_migration(migrations_dir, "002_malo.sql", "ESTO NO ES SQL VALIDO;")
    runner = MigrationsRunner(tmp_db, migrations_dir)

    with pytest.raises(sqlite3.Error):
        runner.run()

    # La 001 sí debió aplicarse antes del fallo de la 002.
    conn = tmp_db.connect()
    try:
        versions = [
            r["version"] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()
        ]
    finally:
        conn.close()
    assert versions == ["001"]


def test_directorio_inexistente_lanza_error(tmp_db: Database, tmp_path: Path) -> None:
    runner = MigrationsRunner(tmp_db, tmp_path / "no_existe")
    with pytest.raises(FileNotFoundError):
        runner.run()


def test_directorio_vacio_retorna_lista_vacia(tmp_db: Database, migrations_dir: Path) -> None:
    runner = MigrationsRunner(tmp_db, migrations_dir)
    assert runner.run() == []


def test_pragmas_aplicados_tras_connect(tmp_db: Database) -> None:
    """WAL y foreign_keys deben quedar activos en cada conexión nueva."""
    conn = tmp_db.connect()
    try:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
        fk_enabled = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    finally:
        conn.close()
    assert journal_mode == "wal"
    assert fk_enabled == 1
