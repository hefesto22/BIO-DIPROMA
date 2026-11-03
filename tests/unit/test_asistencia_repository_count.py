"""Tests del método ``count_by_rango`` del AsistenciaRepositorySQLite.

Cubre la nueva operación añadida para Sub-3.5 (preview de export):
    - Conteo correcto del rango sin filtro.
    - Conteo correcto con filtro por empleado.
    - Bordes inclusivos del rango.
    - Devuelve 0 cuando no hay filas.
"""

from __future__ import annotations

from pathlib import Path
from typing import Tuple

import pytest

from core.models.asistencia import Asistencia, EstadoAsistencia
from core.models.cargo import Cargo
from core.models.departamento import Departamento
from core.models.empleado import Empleado
from core.models.turno import DIAS_LABORALES, Turno
from core.repositories.asistencia_repository_sqlite import (
    AsistenciaRepositorySQLite,
)
from core.repositories.cargo_repository_sqlite import CargoRepositorySQLite
from core.repositories.departamento_repository_sqlite import (
    DepartamentoRepositorySQLite,
)
from core.repositories.empleado_repository_sqlite import (
    EmpleadoRepositorySQLite,
)
from core.repositories.turno_repository_sqlite import TurnoRepositorySQLite
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "infrastructure" / "database" / "migrations"


@pytest.fixture
def setup(tmp_path: Path) -> Tuple[AsistenciaRepositorySQLite, int, int, int]:
    """Repo + 2 empleados + 1 turno; sin asistencias previas."""
    db = Database(tmp_path / "test_count.db")
    MigrationsRunner(db, MIGRATIONS_DIR).run()
    depto = DepartamentoRepositorySQLite(db).create(Departamento(id=None, nombre="Administración"))
    cargo = CargoRepositorySQLite(db).create(Cargo(id=None, nombre="Secretaria"))
    assert depto.id is not None and cargo.id is not None
    emp_a = EmpleadoRepositorySQLite(db).create(
        Empleado(
            id=None,
            dni="0501-1990-11111",
            nombres="Ana",
            apellidos="Zapata",
            departamento_id=depto.id,
            cargo_id=cargo.id,
            fecha_ingreso="2020-01-15",
        )
    )
    emp_b = EmpleadoRepositorySQLite(db).create(
        Empleado(
            id=None,
            dni="0501-1990-22222",
            nombres="Bob",
            apellidos="López",
            departamento_id=depto.id,
            cargo_id=cargo.id,
            fecha_ingreso="2021-01-15",
        )
    )
    turno = TurnoRepositorySQLite(db).create(
        Turno(
            id=None,
            nombre="Admin",
            hora_entrada="08:00",
            hora_salida="17:00",
            dias_semana=DIAS_LABORALES,
        )
    )
    assert emp_a.id is not None and emp_b.id is not None and turno.id is not None
    return AsistenciaRepositorySQLite(db), emp_a.id, emp_b.id, turno.id


def _seed(repo: AsistenciaRepositorySQLite, emp_id: int, turno_id: int, fecha: str) -> None:
    repo.upsert(
        Asistencia(
            id=None,
            empleado_id=emp_id,
            fecha=fecha,
            estado=EstadoAsistencia.PRESENTE.value,
            turno_id_aplicado=turno_id,
            hora_entrada_real="08:00:00",
            hora_salida_real="17:00:00",
            consolidada_en="2026-04-24T10:00:00",
        )
    )


def test_count_by_rango_sin_filas_devuelve_cero(
    setup: Tuple[AsistenciaRepositorySQLite, int, int, int],
) -> None:
    repo, _, _, _ = setup
    assert repo.count_by_rango(desde="2026-04-01", hasta="2026-04-30") == 0


def test_count_by_rango_cuenta_todas_sin_filtro(
    setup: Tuple[AsistenciaRepositorySQLite, int, int, int],
) -> None:
    repo, emp_a, emp_b, turno_id = setup
    _seed(repo, emp_a, turno_id, "2026-04-15")
    _seed(repo, emp_b, turno_id, "2026-04-15")
    _seed(repo, emp_a, turno_id, "2026-04-16")
    assert repo.count_by_rango(desde="2026-04-01", hasta="2026-04-30") == 3


def test_count_by_rango_filtra_por_empleado(
    setup: Tuple[AsistenciaRepositorySQLite, int, int, int],
) -> None:
    repo, emp_a, emp_b, turno_id = setup
    _seed(repo, emp_a, turno_id, "2026-04-15")
    _seed(repo, emp_b, turno_id, "2026-04-15")
    _seed(repo, emp_a, turno_id, "2026-04-16")
    assert repo.count_by_rango(desde="2026-04-01", hasta="2026-04-30", empleado_id=emp_a) == 2


def test_count_by_rango_bordes_inclusivos(
    setup: Tuple[AsistenciaRepositorySQLite, int, int, int],
) -> None:
    repo, emp_a, _, turno_id = setup
    _seed(repo, emp_a, turno_id, "2026-04-15")
    _seed(repo, emp_a, turno_id, "2026-04-20")
    # Rango exacto incluye ambos extremos.
    assert repo.count_by_rango(desde="2026-04-15", hasta="2026-04-20") == 2
    # Excluyendo un día por arriba.
    assert repo.count_by_rango(desde="2026-04-15", hasta="2026-04-19") == 1
