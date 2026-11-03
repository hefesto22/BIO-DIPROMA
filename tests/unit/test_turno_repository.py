"""Tests del TurnoRepositorySQLite.

Cubre CRUD, búsquedas, listados, update de todos los campos mutables,
archive/unarchive, integridad del UNIQUE de nombre y validación de
CHECKs del schema (bitmask de días fuera de rango, minutos negativos).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.models.turno import (
    DIAS_FIN_DE_SEMANA,
    DIAS_LABORALES,
    DIAS_TODA_LA_SEMANA,
    Turno,
)
from core.repositories.turno_repository_sqlite import TurnoRepositorySQLite
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "infrastructure" / "database" / "migrations"


@pytest.fixture
def repo(tmp_path: Path) -> TurnoRepositorySQLite:
    db = Database(tmp_path / "test_turno.db")
    MigrationsRunner(db, MIGRATIONS_DIR).run()
    return TurnoRepositorySQLite(db)


def _nuevo_administrativo() -> Turno:
    """Turno L-V 08:00 a 17:00 con 60 min de descanso."""
    return Turno(
        id=None,
        nombre="Administrativo 8-5",
        hora_entrada="08:00",
        hora_salida="17:00",
        minutos_descanso=60,
        dias_semana=DIAS_LABORALES,
        cruza_medianoche=False,
    )


def _nuevo_nocturno() -> Turno:
    """Turno de vigilancia que cruza medianoche."""
    return Turno(
        id=None,
        nombre="Vigilancia Nocturna",
        hora_entrada="22:00",
        hora_salida="06:00",
        minutos_descanso=0,
        dias_semana=DIAS_TODA_LA_SEMANA,
        cruza_medianoche=True,
    )


# ── create + read ─────────────────────────────────────────────────────────────


def test_create_asigna_id(repo: TurnoRepositorySQLite) -> None:
    creado = repo.create(_nuevo_administrativo())
    assert creado.id is not None and creado.id > 0
    assert creado.nombre == "Administrativo 8-5"
    assert creado.hora_entrada == "08:00"
    assert creado.dias_semana == DIAS_LABORALES
    assert creado.cruza_medianoche is False
    assert creado.is_active is True


def test_create_persiste_cruza_medianoche(
    repo: TurnoRepositorySQLite,
) -> None:
    creado = repo.create(_nuevo_nocturno())
    assert creado.id is not None
    leido = repo.get_by_id(creado.id)
    assert leido is not None
    assert leido.cruza_medianoche is True
    assert leido.dias_semana == DIAS_TODA_LA_SEMANA


def test_get_by_nombre_encuentra_creado(repo: TurnoRepositorySQLite) -> None:
    repo.create(_nuevo_administrativo())
    leido = repo.get_by_nombre("Administrativo 8-5")
    assert leido is not None


def test_get_by_nombre_case_sensitive(repo: TurnoRepositorySQLite) -> None:
    repo.create(_nuevo_administrativo())
    assert repo.get_by_nombre("administrativo 8-5") is None


def test_get_by_id_inexistente(repo: TurnoRepositorySQLite) -> None:
    assert repo.get_by_id(9999) is None


def test_create_duplicate_nombre_falla(
    repo: TurnoRepositorySQLite,
) -> None:
    repo.create(_nuevo_administrativo())
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(_nuevo_administrativo())


def test_create_dias_fuera_de_rango_falla(
    repo: TurnoRepositorySQLite,
) -> None:
    """El CHECK ``dias_semana BETWEEN 0 AND 127`` debe rechazar bitmask inválido."""
    invalido = Turno(
        id=None,
        nombre="Raro",
        hora_entrada="08:00",
        hora_salida="17:00",
        dias_semana=200,
    )
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(invalido)


def test_create_minutos_descanso_negativos_falla(
    repo: TurnoRepositorySQLite,
) -> None:
    """El CHECK ``minutos_descanso >= 0`` debe rechazar negativos."""
    invalido = Turno(
        id=None,
        nombre="RaroNeg",
        hora_entrada="08:00",
        hora_salida="17:00",
        minutos_descanso=-10,
    )
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(invalido)


# ── listados ──────────────────────────────────────────────────────────────────


def test_list_all_vacio(repo: TurnoRepositorySQLite) -> None:
    assert repo.list_all() == []


def test_list_all_ordenado_por_nombre(repo: TurnoRepositorySQLite) -> None:
    repo.create(_nuevo_nocturno())
    repo.create(_nuevo_administrativo())
    nombres = [t.nombre for t in repo.list_all()]
    assert nombres == ["Administrativo 8-5", "Vigilancia Nocturna"]


def test_list_active_excluye_archivados(
    repo: TurnoRepositorySQLite,
) -> None:
    creado = repo.create(_nuevo_administrativo())
    repo.create(_nuevo_nocturno())
    assert creado.id is not None
    repo.archive(creado.id)
    nombres = [t.nombre for t in repo.list_active()]
    assert nombres == ["Vigilancia Nocturna"]


# ── update ────────────────────────────────────────────────────────────────────


def test_update_modifica_todos_los_campos(repo: TurnoRepositorySQLite) -> None:
    creado = repo.create(_nuevo_administrativo())
    assert creado.id is not None
    # Mutamos todos los campos editables.
    creado.nombre = "Administrativo 7-4"
    creado.hora_entrada = "07:00"
    creado.hora_salida = "16:00"
    creado.minutos_descanso = 45
    creado.dias_semana = DIAS_FIN_DE_SEMANA
    creado.cruza_medianoche = False
    repo.update(creado)
    leido = repo.get_by_id(creado.id)
    assert leido is not None
    assert leido.nombre == "Administrativo 7-4"
    assert leido.hora_entrada == "07:00"
    assert leido.hora_salida == "16:00"
    assert leido.minutos_descanso == 45
    assert leido.dias_semana == DIAS_FIN_DE_SEMANA


def test_update_sin_id_levanta_value_error(
    repo: TurnoRepositorySQLite,
) -> None:
    turno_sin_id = _nuevo_administrativo()
    with pytest.raises(ValueError):
        repo.update(turno_sin_id)


def test_update_a_nombre_existente_falla(
    repo: TurnoRepositorySQLite,
) -> None:
    repo.create(_nuevo_administrativo())
    b = repo.create(_nuevo_nocturno())
    assert b.id is not None
    b.nombre = "Administrativo 8-5"
    with pytest.raises(sqlite3.IntegrityError):
        repo.update(b)


# ── archive / unarchive ───────────────────────────────────────────────────────


def test_archive_desactiva(repo: TurnoRepositorySQLite) -> None:
    creado = repo.create(_nuevo_administrativo())
    assert creado.id is not None
    repo.archive(creado.id)
    leido = repo.get_by_id(creado.id)
    assert leido is not None
    assert leido.is_active is False


def test_unarchive_reactiva(repo: TurnoRepositorySQLite) -> None:
    creado = repo.create(_nuevo_administrativo())
    assert creado.id is not None
    repo.archive(creado.id)
    repo.unarchive(creado.id)
    leido = repo.get_by_id(creado.id)
    assert leido is not None
    assert leido.is_active is True
