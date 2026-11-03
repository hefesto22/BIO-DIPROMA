"""Tests del CargoRepositorySQLite.

Cubre CRUD, búsquedas, listados y archive/unarchive. Por ser gemelo de
Departamento, los casos son simétricos — se mantienen separados para
detectar divergencias accidentales entre ambas tablas.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.models.cargo import Cargo
from core.repositories.cargo_repository_sqlite import CargoRepositorySQLite
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "infrastructure" / "database" / "migrations"


@pytest.fixture
def repo(tmp_path: Path) -> CargoRepositorySQLite:
    db = Database(tmp_path / "test_cargo.db")
    MigrationsRunner(db, MIGRATIONS_DIR).run()
    return CargoRepositorySQLite(db)


def _nuevo(nombre: str = "Alcalde") -> Cargo:
    return Cargo(id=None, nombre=nombre)


# ── create + read ─────────────────────────────────────────────────────────────


def test_create_asigna_id(repo: CargoRepositorySQLite) -> None:
    creado = repo.create(_nuevo())
    assert creado.id is not None and creado.id > 0
    assert creado.nombre == "Alcalde"
    assert creado.is_active is True


def test_get_by_id_encuentra_creado(repo: CargoRepositorySQLite) -> None:
    creado = repo.create(_nuevo("Contador"))
    assert creado.id is not None
    leido = repo.get_by_id(creado.id)
    assert leido is not None
    assert leido.nombre == "Contador"


def test_get_by_id_inexistente_devuelve_none(
    repo: CargoRepositorySQLite,
) -> None:
    assert repo.get_by_id(9999) is None


def test_get_by_nombre_encuentra_creado(repo: CargoRepositorySQLite) -> None:
    repo.create(_nuevo("Secretaria"))
    leido = repo.get_by_nombre("Secretaria")
    assert leido is not None


def test_get_by_nombre_case_sensitive(repo: CargoRepositorySQLite) -> None:
    repo.create(_nuevo("Secretaria"))
    assert repo.get_by_nombre("SECRETARIA") is None


def test_create_duplicate_nombre_falla(
    repo: CargoRepositorySQLite,
) -> None:
    repo.create(_nuevo("Dup"))
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(_nuevo("Dup"))


# ── listados ──────────────────────────────────────────────────────────────────


def test_list_all_vacio(repo: CargoRepositorySQLite) -> None:
    assert repo.list_all() == []


def test_list_all_ordenado_alfabetico(repo: CargoRepositorySQLite) -> None:
    repo.create(_nuevo("Zoólogo"))
    repo.create(_nuevo("Alcalde"))
    repo.create(_nuevo("Mensajero"))
    nombres = [c.nombre for c in repo.list_all()]
    assert nombres == ["Alcalde", "Mensajero", "Zoólogo"]


def test_list_active_excluye_archivados(
    repo: CargoRepositorySQLite,
) -> None:
    repo.create(_nuevo("Activo"))
    archivado = repo.create(_nuevo("Archivado"))
    assert archivado.id is not None
    repo.archive(archivado.id)
    assert [c.nombre for c in repo.list_active()] == ["Activo"]


# ── rename / archive / unarchive ──────────────────────────────────────────────


def test_rename_cambia_nombre(repo: CargoRepositorySQLite) -> None:
    creado = repo.create(_nuevo("Viejo"))
    assert creado.id is not None
    repo.rename(creado.id, "Nuevo")
    leido = repo.get_by_id(creado.id)
    assert leido is not None
    assert leido.nombre == "Nuevo"


def test_rename_a_nombre_existente_falla(
    repo: CargoRepositorySQLite,
) -> None:
    repo.create(_nuevo("A"))
    b = repo.create(_nuevo("B"))
    assert b.id is not None
    with pytest.raises(sqlite3.IntegrityError):
        repo.rename(b.id, "A")


def test_archive_desactiva(repo: CargoRepositorySQLite) -> None:
    creado = repo.create(_nuevo("Archívame"))
    assert creado.id is not None
    repo.archive(creado.id)
    leido = repo.get_by_id(creado.id)
    assert leido is not None
    assert leido.is_active is False


def test_unarchive_reactiva(repo: CargoRepositorySQLite) -> None:
    creado = repo.create(_nuevo("Ping"))
    assert creado.id is not None
    repo.archive(creado.id)
    repo.unarchive(creado.id)
    leido = repo.get_by_id(creado.id)
    assert leido is not None
    assert leido.is_active is True
