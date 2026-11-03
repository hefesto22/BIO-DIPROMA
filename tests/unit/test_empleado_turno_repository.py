"""Tests del EmpleadoTurnoRepositorySQLite.

Cubre: asignación inicial, invariante "un solo vigente" (índice parcial
único), historial ordenado, cambio atómico de turno (cerrar vigente +
asignar nuevo), rollback transaccional ante fallas, y consultas auxiliares.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Tuple

import pytest

from core.models.cargo import Cargo
from core.models.departamento import Departamento
from core.models.empleado import Empleado
from core.models.turno import DIAS_LABORALES, Turno
from core.repositories.cargo_repository_sqlite import CargoRepositorySQLite
from core.repositories.departamento_repository_sqlite import (
    DepartamentoRepositorySQLite,
)
from core.repositories.empleado_repository_sqlite import (
    EmpleadoRepositorySQLite,
)
from core.repositories.empleado_turno_repository_sqlite import (
    EmpleadoTurnoRepositorySQLite,
)
from core.repositories.turno_repository_sqlite import TurnoRepositorySQLite
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "infrastructure" / "database" / "migrations"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def setup(
    tmp_path: Path,
) -> Tuple[EmpleadoTurnoRepositorySQLite, int, int, int]:
    """Crea DB con migraciones aplicadas, seedea 1 empleado y 2 turnos.

    Returns:
        Tupla (repo, empleado_id, turno_a_id, turno_b_id).
    """
    db = Database(tmp_path / "test_empleado_turno.db")
    MigrationsRunner(db, MIGRATIONS_DIR).run()
    depto = DepartamentoRepositorySQLite(db).create(Departamento(id=None, nombre="Admin"))
    cargo = CargoRepositorySQLite(db).create(Cargo(id=None, nombre="Secretaria"))
    assert depto.id is not None and cargo.id is not None
    emp = EmpleadoRepositorySQLite(db).create(
        Empleado(
            id=None,
            dni="0501-1990-12345",
            nombres="Juan",
            apellidos="Pérez",
            departamento_id=depto.id,
            cargo_id=cargo.id,
            fecha_ingreso="2020-01-15",
        )
    )
    assert emp.id is not None
    turno_repo = TurnoRepositorySQLite(db)
    t_a = turno_repo.create(
        Turno(
            id=None,
            nombre="A",
            hora_entrada="08:00",
            hora_salida="17:00",
            dias_semana=DIAS_LABORALES,
        )
    )
    t_b = turno_repo.create(
        Turno(
            id=None,
            nombre="B",
            hora_entrada="07:00",
            hora_salida="16:00",
            dias_semana=DIAS_LABORALES,
        )
    )
    assert t_a.id is not None and t_b.id is not None
    return EmpleadoTurnoRepositorySQLite(db), emp.id, t_a.id, t_b.id


# ── asignar ───────────────────────────────────────────────────────────────────


def test_asignar_crea_fila_vigente(
    setup: Tuple[EmpleadoTurnoRepositorySQLite, int, int, int],
) -> None:
    repo, emp_id, turno_a, _ = setup
    asignacion = repo.asignar(emp_id, turno_a, "2026-01-01")
    assert asignacion.id is not None and asignacion.id > 0
    assert asignacion.empleado_id == emp_id
    assert asignacion.turno_id == turno_a
    assert asignacion.fecha_inicio == "2026-01-01"
    assert asignacion.fecha_fin is None


def test_asignar_dos_vigentes_es_legal_a_nivel_repo(
    setup: Tuple[EmpleadoTurnoRepositorySQLite, int, int, int],
) -> None:
    """Sub-3.2.B: la migración 008 quitó el índice único parcial.

    A nivel BD ahora se permite tener varias asignaciones vigentes
    para un mismo empleado (cubriendo días distintos de la semana).
    La invariante "los días no se solapan" la valida el
    ``EmpleadoService``, no la BD, porque depende del bitmask del
    Turno (otra tabla).
    """
    repo, emp_id, turno_a, turno_b = setup
    repo.asignar(emp_id, turno_a, "2026-01-01")
    # Ya no falla — el repo permite la segunda vigente.
    repo.asignar(emp_id, turno_b, "2026-02-01")
    vigentes = repo.list_vigentes(emp_id)
    assert {v.turno_id for v in vigentes} == {turno_a, turno_b}


def test_asignar_con_empleado_inexistente_falla(
    setup: Tuple[EmpleadoTurnoRepositorySQLite, int, int, int],
) -> None:
    repo, _, turno_a, _ = setup
    with pytest.raises(sqlite3.IntegrityError):
        repo.asignar(999, turno_a, "2026-01-01")


def test_asignar_con_turno_inexistente_falla(
    setup: Tuple[EmpleadoTurnoRepositorySQLite, int, int, int],
) -> None:
    repo, emp_id, _, _ = setup
    with pytest.raises(sqlite3.IntegrityError):
        repo.asignar(emp_id, 999, "2026-01-01")


# ── get_vigente ───────────────────────────────────────────────────────────────


def test_get_vigente_sin_historial_devuelve_none(
    setup: Tuple[EmpleadoTurnoRepositorySQLite, int, int, int],
) -> None:
    repo, emp_id, _, _ = setup
    assert repo.get_vigente(emp_id) is None


def test_get_vigente_devuelve_la_asignacion_abierta(
    setup: Tuple[EmpleadoTurnoRepositorySQLite, int, int, int],
) -> None:
    repo, emp_id, turno_a, _ = setup
    asignacion = repo.asignar(emp_id, turno_a, "2026-01-01")
    vigente = repo.get_vigente(emp_id)
    assert vigente is not None
    assert vigente.id == asignacion.id
    assert vigente.fecha_fin is None


def test_get_vigente_tras_cerrar_devuelve_none(
    setup: Tuple[EmpleadoTurnoRepositorySQLite, int, int, int],
) -> None:
    repo, emp_id, turno_a, turno_b = setup
    repo.asignar(emp_id, turno_a, "2026-01-01")
    repo.cerrar_vigente_y_asignar(emp_id, turno_b, "2026-03-31", "2026-04-01")
    vigente = repo.get_vigente(emp_id)
    # Tras el cambio, la vigente debe ser la nueva — no None.
    assert vigente is not None
    assert vigente.turno_id == turno_b


# ── list_historial ────────────────────────────────────────────────────────────


def test_list_historial_vacio(
    setup: Tuple[EmpleadoTurnoRepositorySQLite, int, int, int],
) -> None:
    repo, emp_id, _, _ = setup
    assert repo.list_historial(emp_id) == []


def test_list_historial_ordenado_desc(
    setup: Tuple[EmpleadoTurnoRepositorySQLite, int, int, int],
) -> None:
    repo, emp_id, turno_a, turno_b = setup
    repo.asignar(emp_id, turno_a, "2025-01-01")
    repo.cerrar_vigente_y_asignar(emp_id, turno_b, "2025-12-31", "2026-01-01")
    historial = repo.list_historial(emp_id)
    assert len(historial) == 2
    # La más reciente (fecha_inicio mayor) debe ir primero.
    assert historial[0].fecha_inicio == "2026-01-01"
    assert historial[1].fecha_inicio == "2025-01-01"


# ── list_by_turno ─────────────────────────────────────────────────────────────


def test_list_by_turno_trae_asignaciones_del_turno(
    setup: Tuple[EmpleadoTurnoRepositorySQLite, int, int, int],
) -> None:
    repo, emp_id, turno_a, turno_b = setup
    repo.asignar(emp_id, turno_a, "2025-01-01")
    repo.cerrar_vigente_y_asignar(emp_id, turno_b, "2025-12-31", "2026-01-01")
    por_a = repo.list_by_turno(turno_a)
    por_b = repo.list_by_turno(turno_b)
    assert len(por_a) == 1
    assert por_a[0].fecha_fin == "2025-12-31"
    assert len(por_b) == 1
    assert por_b[0].fecha_fin is None


# ── cerrar_vigente ────────────────────────────────────────────────────────────


def test_cerrar_vigente_cierra_sin_abrir_nueva(
    setup: Tuple[EmpleadoTurnoRepositorySQLite, int, int, int],
) -> None:
    repo, emp_id, turno_a, _ = setup
    original = repo.asignar(emp_id, turno_a, "2026-01-01")
    assert original.id is not None
    repo.cerrar_vigente(emp_id, "2026-03-31")
    # Ya no hay vigente.
    assert repo.get_vigente(emp_id) is None
    # Pero la fila original sigue en el historial con fecha_fin seteada.
    historial = repo.list_historial(emp_id)
    assert len(historial) == 1
    assert historial[0].id == original.id
    assert historial[0].fecha_fin == "2026-03-31"


def test_cerrar_vigente_solo_sin_vigente_levanta_value_error(
    setup: Tuple[EmpleadoTurnoRepositorySQLite, int, int, int],
) -> None:
    repo, emp_id, _, _ = setup
    with pytest.raises(ValueError, match="no tiene asignación vigente"):
        repo.cerrar_vigente(emp_id, "2026-03-31")


def test_cerrar_vigente_con_fecha_menor_al_inicio_falla(
    setup: Tuple[EmpleadoTurnoRepositorySQLite, int, int, int],
) -> None:
    """CHECK ``fecha_fin >= fecha_inicio`` en la fila rechaza la operación."""
    repo, emp_id, turno_a, _ = setup
    repo.asignar(emp_id, turno_a, "2026-03-01")
    with pytest.raises(sqlite3.IntegrityError):
        repo.cerrar_vigente(emp_id, "2026-02-15")
    # La vigente quedó intacta.
    vigente = repo.get_vigente(emp_id)
    assert vigente is not None
    assert vigente.fecha_fin is None


def test_cerrar_vigente_idempotencia_segunda_llamada_falla(
    setup: Tuple[EmpleadoTurnoRepositorySQLite, int, int, int],
) -> None:
    """Llamar dos veces seguidas: la segunda falla (ya no hay vigente)."""
    repo, emp_id, turno_a, _ = setup
    repo.asignar(emp_id, turno_a, "2026-01-01")
    repo.cerrar_vigente(emp_id, "2026-03-31")
    with pytest.raises(ValueError, match="no tiene asignación vigente"):
        repo.cerrar_vigente(emp_id, "2026-04-30")


# ── cerrar_vigente_y_asignar ──────────────────────────────────────────────────


def test_cerrar_vigente_y_asignar_cierra_y_crea(
    setup: Tuple[EmpleadoTurnoRepositorySQLite, int, int, int],
) -> None:
    repo, emp_id, turno_a, turno_b = setup
    original = repo.asignar(emp_id, turno_a, "2026-01-01")
    assert original.id is not None
    nueva = repo.cerrar_vigente_y_asignar(emp_id, turno_b, "2026-03-31", "2026-04-01")
    historial = repo.list_historial(emp_id)
    assert len(historial) == 2
    cerrada = next(h for h in historial if h.id == original.id)
    assert cerrada.fecha_fin == "2026-03-31"
    assert nueva.turno_id == turno_b
    assert nueva.fecha_fin is None


def test_cerrar_vigente_sin_vigente_levanta_value_error(
    setup: Tuple[EmpleadoTurnoRepositorySQLite, int, int, int],
) -> None:
    repo, emp_id, _, turno_b = setup
    with pytest.raises(ValueError, match="no tiene asignación vigente"):
        repo.cerrar_vigente_y_asignar(emp_id, turno_b, "2026-03-31", "2026-04-01")


def test_cerrar_vigente_con_turno_nuevo_invalido_revierte(
    setup: Tuple[EmpleadoTurnoRepositorySQLite, int, int, int],
) -> None:
    """Atomicidad: si el INSERT de la nueva falla, el UPDATE se revierte."""
    repo, emp_id, turno_a, _ = setup
    original = repo.asignar(emp_id, turno_a, "2026-01-01")
    assert original.id is not None
    with pytest.raises(sqlite3.IntegrityError):
        repo.cerrar_vigente_y_asignar(
            emp_id,
            turno_id_nuevo=999,  # inválido
            fecha_fin_vigente="2026-03-31",
            fecha_inicio_nueva="2026-04-01",
        )
    # La fila original debe seguir vigente (fecha_fin IS NULL).
    vigente = repo.get_vigente(emp_id)
    assert vigente is not None
    assert vigente.id == original.id
    assert vigente.fecha_fin is None
    # Y no debe haber filas extra.
    assert len(repo.list_historial(emp_id)) == 1


def test_cerrar_vigente_con_fechas_invalidas_revierte(
    setup: Tuple[EmpleadoTurnoRepositorySQLite, int, int, int],
) -> None:
    """El CHECK ``fecha_fin >= fecha_inicio`` en la fila cerrada aborta."""
    repo, emp_id, turno_a, turno_b = setup
    original = repo.asignar(emp_id, turno_a, "2026-03-01")
    assert original.id is not None
    # fecha_fin (2026-02-15) < fecha_inicio (2026-03-01) de la cerrada → CHECK falla.
    with pytest.raises(sqlite3.IntegrityError):
        repo.cerrar_vigente_y_asignar(
            emp_id,
            turno_id_nuevo=turno_b,
            fecha_fin_vigente="2026-02-15",
            fecha_inicio_nueva="2026-04-01",
        )
    # Vigente original intacta.
    vigente = repo.get_vigente(emp_id)
    assert vigente is not None
    assert vigente.id == original.id
    assert vigente.fecha_fin is None
