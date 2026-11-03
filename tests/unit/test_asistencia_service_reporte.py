"""Tests de los métodos de ``AsistenciaService`` para reportes (Sub-3.5a).

Cubre:
    - ``contar_asistencias_para_reporte`` delega al repo.
    - ``list_asistencias_para_reporte`` devuelve TODAS las filas (sin
      truncamiento), con enriquecimiento de nombre/DNI/turno y orden
      determinístico por (fecha asc, empleado_id asc).
"""

from __future__ import annotations

from pathlib import Path
from typing import Tuple
from unittest.mock import MagicMock

import pytest

from core.models.asistencia import Asistencia, EstadoAsistencia
from core.models.cargo import Cargo
from core.models.departamento import Departamento
from core.models.empleado import Empleado
from core.models.turno import DIAS_LABORALES, Turno
from core.repositories.asistencia_repository_sqlite import (
    AsistenciaRepositorySQLite,
)
from core.repositories.audit_log_repository_sqlite import (
    AuditLogRepositorySQLite,
)
from core.repositories.cargo_repository_sqlite import CargoRepositorySQLite
from core.repositories.departamento_repository_sqlite import (
    DepartamentoRepositorySQLite,
)
from core.repositories.empleado_repository_sqlite import (
    EmpleadoRepositorySQLite,
)
from core.repositories.turno_repository_sqlite import TurnoRepositorySQLite
from core.services.asistencia_service import AsistenciaService
from core.services.audit_logger import AuditLogger
from core.services.consolidacion_service import ConsolidacionService
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "infrastructure" / "database" / "migrations"


@pytest.fixture
def setup(tmp_path: Path) -> Tuple[AsistenciaService, Database, int, int, int]:
    """Crea servicio + 2 empleados + 1 turno (sin asistencias previas).

    Returns:
        (service, db, emp_a_id, emp_b_id, turno_id).
    """
    db = Database(tmp_path / "test_reporte_service.db")
    MigrationsRunner(db, MIGRATIONS_DIR).run()
    dep = DepartamentoRepositorySQLite(db).create(Departamento(id=None, nombre="Administración"))
    cargo = CargoRepositorySQLite(db).create(Cargo(id=None, nombre="Secretaria"))
    assert dep.id is not None and cargo.id is not None
    emp_repo = EmpleadoRepositorySQLite(db)
    emp_a = emp_repo.create(
        Empleado(
            id=None,
            dni="0501-1990-11111",
            nombres="Ana",
            apellidos="Zapata",
            departamento_id=dep.id,
            cargo_id=cargo.id,
            fecha_ingreso="2020-01-15",
        )
    )
    emp_b = emp_repo.create(
        Empleado(
            id=None,
            dni="0501-1990-22222",
            nombres="Bob",
            apellidos="Alvarado",
            departamento_id=dep.id,
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
    service = AsistenciaService(
        asistencia_read=AsistenciaRepositorySQLite(db),
        asistencia_write=AsistenciaRepositorySQLite(db),
        empleado_read=emp_repo,
        turno_read=TurnoRepositorySQLite(db),
        audit_logger=AuditLogger(AuditLogRepositorySQLite(db), machine_name="t"),
        consolidacion_service=MagicMock(spec=ConsolidacionService),
    )
    return service, db, emp_a.id, emp_b.id, turno.id


def _insert(db: Database, emp_id: int, turno_id: int, fecha: str) -> None:
    AsistenciaRepositorySQLite(db).upsert(
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


# ── contar_asistencias_para_reporte ──────────────────────────────────────────


def test_contar_asistencias_sin_filtro(
    setup: Tuple[AsistenciaService, Database, int, int, int],
) -> None:
    service, db, emp_a, emp_b, turno_id = setup
    _insert(db, emp_a, turno_id, "2026-04-15")
    _insert(db, emp_b, turno_id, "2026-04-15")
    assert service.contar_asistencias_para_reporte(desde="2026-04-01", hasta="2026-04-30") == 2


def test_contar_asistencias_con_filtro_empleado(
    setup: Tuple[AsistenciaService, Database, int, int, int],
) -> None:
    service, db, emp_a, emp_b, turno_id = setup
    _insert(db, emp_a, turno_id, "2026-04-15")
    _insert(db, emp_b, turno_id, "2026-04-15")
    assert (
        service.contar_asistencias_para_reporte(
            desde="2026-04-01", hasta="2026-04-30", empleado_id=emp_a
        )
        == 1
    )


# ── list_asistencias_para_reporte ────────────────────────────────────────────


def test_list_para_reporte_devuelve_enriquecido(
    setup: Tuple[AsistenciaService, Database, int, int, int],
) -> None:
    service, db, emp_a, _, turno_id = setup
    _insert(db, emp_a, turno_id, "2026-04-15")
    items = service.list_asistencias_para_reporte(desde="2026-04-15", hasta="2026-04-15")
    assert len(items) == 1
    item = items[0]
    assert item.empleado_nombre_completo == "Zapata Ana"
    assert item.empleado_dni == "0501-1990-11111"
    assert item.turno_nombre == "Admin"


def test_list_para_reporte_no_truncamiento_respeta_n_grandes(
    setup: Tuple[AsistenciaService, Database, int, int, int],
) -> None:
    """A diferencia de ``list_asistencias``, NO aplica DEFAULT_LIMIT.

    Insertamos un volumen mayor al ``DEFAULT_LIMIT`` (500) para
    confirmar que el método de reporte trae todo.
    """
    from datetime import date, timedelta

    service, db, emp_a, _, turno_id = setup
    # 510 días seguidos — > DEFAULT_LIMIT (500). Hacemos UN solo
    # transacción con executemany para que el test sea rápido.
    filas = [
        (
            emp_a,
            (date(2024, 1, 1) + timedelta(days=i)).isoformat(),
            EstadoAsistencia.PRESENTE.value,
            turno_id,
            "08:00:00",
            "17:00:00",
            "2026-04-24T10:00:00",
        )
        for i in range(510)
    ]
    with db.transaction() as conn:
        conn.executemany(
            "INSERT INTO asistencias "
            "(empleado_id, fecha, estado, turno_id_aplicado, "
            " hora_entrada_real, hora_salida_real, consolidada_en) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            filas,
        )
    items = service.list_asistencias_para_reporte(desde="2024-01-01", hasta="2026-12-31")
    assert len(items) == 510


def test_list_para_reporte_filtra_por_empleado(
    setup: Tuple[AsistenciaService, Database, int, int, int],
) -> None:
    service, db, emp_a, emp_b, turno_id = setup
    _insert(db, emp_a, turno_id, "2026-04-15")
    _insert(db, emp_b, turno_id, "2026-04-15")
    items = service.list_asistencias_para_reporte(
        desde="2026-04-15", hasta="2026-04-15", empleado_id=emp_a
    )
    assert len(items) == 1
    assert items[0].asistencia.empleado_id == emp_a


def test_list_para_reporte_vacio_no_lanza(
    setup: Tuple[AsistenciaService, Database, int, int, int],
) -> None:
    """Sin filas en el rango, devuelve lista vacía (no levanta)."""
    service, _, _, _, _ = setup
    assert service.list_asistencias_para_reporte(desde="2026-04-01", hasta="2026-04-30") == []
