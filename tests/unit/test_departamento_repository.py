"""Tests del DepartamentoRepositorySQLite.

Cubre CRUD, búsquedas por id/nombre, listados (todos vs. activos),
rename, archive/unarchive e integridad del UNIQUE de nombre.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.models.departamento import Departamento
from core.repositories.departamento_repository_sqlite import (
    DepartamentoRepositorySQLite,
)
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "infrastructure" / "database" / "migrations"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def repo(tmp_path: Path) -> DepartamentoRepositorySQLite:
    db = Database(tmp_path / "test_departamento.db")
    MigrationsRunner(db, MIGRATIONS_DIR).run()
    return DepartamentoRepositorySQLite(db)


def _nuevo(nombre: str = "Administración") -> Departamento:
    """Construye un Departamento listo para insertar."""
    return Departamento(id=None, nombre=nombre)


# ── Tests: create + read ──────────────────────────────────────────────────────


def test_create_asigna_id(repo: DepartamentoRepositorySQLite) -> None:
    creado = repo.create(_nuevo())
    assert creado.id is not None and creado.id > 0
    assert creado.nombre == "Administración"
    assert creado.is_active is True


def test_get_by_id_encuentra_creado(repo: DepartamentoRepositorySQLite) -> None:
    creado = repo.create(_nuevo(nombre="Obras Públicas"))
    assert creado.id is not None
    leido = repo.get_by_id(creado.id)
    assert leido is not None
    assert leido.nombre == "Obras Públicas"


def test_get_by_id_inexistente_devuelve_none(
    repo: DepartamentoRepositorySQLite,
) -> None:
    assert repo.get_by_id(9999) is None


def test_get_by_nombre_encuentra_creado(
    repo: DepartamentoRepositorySQLite,
) -> None:
    repo.create(_nuevo(nombre="Tesorería"))
    leido = repo.get_by_nombre("Tesorería")
    assert leido is not None
    assert leido.is_active is True


def test_get_by_nombre_case_sensitive(
    repo: DepartamentoRepositorySQLite,
) -> None:
    repo.create(_nuevo(nombre="Tesorería"))
    assert repo.get_by_nombre("tesorería") is None


def test_get_by_nombre_inexistente_devuelve_none(
    repo: DepartamentoRepositorySQLite,
) -> None:
    assert repo.get_by_nombre("NoExiste") is None


def test_create_duplicate_nombre_falla(
    repo: DepartamentoRepositorySQLite,
) -> None:
    repo.create(_nuevo(nombre="Dup"))
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(_nuevo(nombre="Dup"))


# ── Tests: listados ───────────────────────────────────────────────────────────


def test_list_all_vacio(repo: DepartamentoRepositorySQLite) -> None:
    assert repo.list_all() == []


def test_list_all_ordenado_alfabetico(
    repo: DepartamentoRepositorySQLite,
) -> None:
    repo.create(_nuevo("Zoología"))
    repo.create(_nuevo("Administración"))
    repo.create(_nuevo("Mantenimiento"))
    nombres = [d.nombre for d in repo.list_all()]
    assert nombres == ["Administración", "Mantenimiento", "Zoología"]


def test_list_all_incluye_archivados(
    repo: DepartamentoRepositorySQLite,
) -> None:
    activo = repo.create(_nuevo("Activo"))
    archivado = repo.create(_nuevo("Archivado"))
    assert archivado.id is not None
    repo.archive(archivado.id)
    todos = repo.list_all()
    assert len(todos) == 2
    assert {d.nombre for d in todos} == {"Activo", "Archivado"}
    assert {d.is_active for d in todos} == {True, False}
    assert activo.id in {d.id for d in todos}


def test_list_active_excluye_archivados(
    repo: DepartamentoRepositorySQLite,
) -> None:
    repo.create(_nuevo("Activo"))
    archivado = repo.create(_nuevo("Archivado"))
    assert archivado.id is not None
    repo.archive(archivado.id)
    activos = repo.list_active()
    assert [d.nombre for d in activos] == ["Activo"]


# ── Tests: rename / archive / unarchive ───────────────────────────────────────


def test_rename_cambia_nombre(repo: DepartamentoRepositorySQLite) -> None:
    creado = repo.create(_nuevo("Viejo"))
    assert creado.id is not None
    repo.rename(creado.id, "Nuevo")
    leido = repo.get_by_id(creado.id)
    assert leido is not None
    assert leido.nombre == "Nuevo"


def test_rename_a_nombre_existente_falla(
    repo: DepartamentoRepositorySQLite,
) -> None:
    repo.create(_nuevo("A"))
    b = repo.create(_nuevo("B"))
    assert b.id is not None
    with pytest.raises(sqlite3.IntegrityError):
        repo.rename(b.id, "A")


def test_archive_desactiva(repo: DepartamentoRepositorySQLite) -> None:
    creado = repo.create(_nuevo("Archívame"))
    assert creado.id is not None
    repo.archive(creado.id)
    leido = repo.get_by_id(creado.id)
    assert leido is not None
    assert leido.is_active is False


def test_unarchive_reactiva(repo: DepartamentoRepositorySQLite) -> None:
    creado = repo.create(_nuevo("Ping"))
    assert creado.id is not None
    repo.archive(creado.id)
    repo.unarchive(creado.id)
    leido = repo.get_by_id(creado.id)
    assert leido is not None
    assert leido.is_active is True


def test_archive_id_inexistente_no_falla(
    repo: DepartamentoRepositorySQLite,
) -> None:
    # UPDATE de 0 filas no es error en SQL.
    repo.archive(9999)
