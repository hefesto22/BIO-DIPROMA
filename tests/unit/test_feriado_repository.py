"""Tests del FeriadoRepositorySQLite.

Cubre CRUD + delete físico (esta tabla lo permite), UNIQUE sobre
``fecha``, lookup por fecha (query crítica del servicio de
consolidación), y listado por rango inclusive.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.models.feriado import Feriado
from core.repositories.feriado_repository_sqlite import FeriadoRepositorySQLite
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "infrastructure" / "database" / "migrations"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def repo(tmp_path: Path) -> FeriadoRepositorySQLite:
    db = Database(tmp_path / "test_feriado.db")
    MigrationsRunner(db, MIGRATIONS_DIR).run()
    return FeriadoRepositorySQLite(db)


def _nuevo(fecha: str = "2026-09-15", descripcion: str = "Día de la Independencia") -> Feriado:
    return Feriado(id=None, fecha=fecha, descripcion=descripcion)


# ── Tests: create + read ──────────────────────────────────────────────────────


def test_create_asigna_id_y_persiste_campos(repo: FeriadoRepositorySQLite) -> None:
    creado = repo.create(_nuevo("2026-01-01", "Año Nuevo"))
    assert creado.id is not None and creado.id > 0
    assert creado.fecha == "2026-01-01"
    assert creado.descripcion == "Año Nuevo"


def test_get_by_id_encuentra(repo: FeriadoRepositorySQLite) -> None:
    creado = repo.create(_nuevo())
    assert creado.id is not None
    leido = repo.get_by_id(creado.id)
    assert leido is not None
    assert leido.fecha == "2026-09-15"


def test_get_by_id_inexistente_devuelve_none(
    repo: FeriadoRepositorySQLite,
) -> None:
    assert repo.get_by_id(9999) is None


def test_get_by_fecha_encuentra(repo: FeriadoRepositorySQLite) -> None:
    repo.create(_nuevo("2026-04-14", "Día de las Américas"))
    encontrado = repo.get_by_fecha("2026-04-14")
    assert encontrado is not None
    assert encontrado.descripcion == "Día de las Américas"


def test_get_by_fecha_inexistente_devuelve_none(
    repo: FeriadoRepositorySQLite,
) -> None:
    assert repo.get_by_fecha("2026-07-04") is None


def test_fecha_duplicada_falla(repo: FeriadoRepositorySQLite) -> None:
    repo.create(_nuevo("2026-09-15", "Independencia"))
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(_nuevo("2026-09-15", "Otro feriado"))


# ── Tests: listados ───────────────────────────────────────────────────────────


def test_list_all_vacio(repo: FeriadoRepositorySQLite) -> None:
    assert repo.list_all() == []


def test_list_all_ordenado_por_fecha_asc(repo: FeriadoRepositorySQLite) -> None:
    repo.create(_nuevo("2026-12-25", "Navidad"))
    repo.create(_nuevo("2026-01-01", "Año Nuevo"))
    repo.create(_nuevo("2026-09-15", "Independencia"))
    fechas = [f.fecha for f in repo.list_all()]
    assert fechas == ["2026-01-01", "2026-09-15", "2026-12-25"]


def test_list_by_rango_incluye_limites(repo: FeriadoRepositorySQLite) -> None:
    """El rango es inclusive en ambos extremos."""
    repo.create(_nuevo("2026-03-31", "Fuera"))
    repo.create(_nuevo("2026-04-01", "Borde inferior"))
    repo.create(_nuevo("2026-04-15", "Medio"))
    repo.create(_nuevo("2026-04-30", "Borde superior"))
    repo.create(_nuevo("2026-05-01", "Fuera"))

    resultado = repo.list_by_rango("2026-04-01", "2026-04-30")
    fechas = [f.fecha for f in resultado]
    assert fechas == ["2026-04-01", "2026-04-15", "2026-04-30"]


def test_list_by_rango_vacio_cuando_no_hay(repo: FeriadoRepositorySQLite) -> None:
    repo.create(_nuevo("2026-01-01", "Año Nuevo"))
    assert repo.list_by_rango("2026-06-01", "2026-06-30") == []


# ── Tests: update / delete ────────────────────────────────────────────────────


def test_update_descripcion_cambia_texto(repo: FeriadoRepositorySQLite) -> None:
    creado = repo.create(_nuevo("2026-09-15", "Independencia"))
    assert creado.id is not None
    repo.update_descripcion(creado.id, "Día de la Independencia de Honduras")
    leido = repo.get_by_id(creado.id)
    assert leido is not None
    assert leido.descripcion == "Día de la Independencia de Honduras"
    assert leido.fecha == "2026-09-15"  # fecha inmutable


def test_delete_borra_fisicamente(repo: FeriadoRepositorySQLite) -> None:
    creado = repo.create(_nuevo("2026-09-15", "Independencia"))
    assert creado.id is not None
    repo.delete(creado.id)
    assert repo.get_by_id(creado.id) is None
    # Y permite volver a crear uno con la misma fecha
    repo.create(_nuevo("2026-09-15", "Independencia (re-creado)"))


def test_delete_id_inexistente_no_falla(repo: FeriadoRepositorySQLite) -> None:
    """DELETE de 0 filas no es error en SQL."""
    repo.delete(9999)
