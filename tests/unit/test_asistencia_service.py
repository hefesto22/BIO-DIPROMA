"""Tests del AsistenciaService (Sub-3.4b + Sub-3.4c).

Integración con repos SQLite reales + AuditLogger real. Cubre:
    - list_asistencias (sin filtro, con filtro por empleado, orden,
      enriquecimiento nombre/DNI/turno, truncamiento).
    - list_empleados_para_filtro (solo activos, orden alfabético).
    - update_observaciones (happy path, id inexistente → error
      tipado, normalización de texto vacío, entrada en audit_log).
    - re_consolidar (delega al ConsolidacionService mockeado + entrada
      de audit_log con ``actor_user_id`` y ``empleado_id``).
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
from core.services.asistencia_service import (
    AsistenciaService,
    ResultadoBusquedaAsistencia,
)
from core.services.audit_logger import AuditLogger
from core.services.consolidacion_service import ConsolidacionService
from core.services.errors import AsistenciaNotFoundError
from core.services.sincronizacion_result import ResultadoConsolidacion
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "infrastructure" / "database" / "migrations"

ACTOR_ID = 1


def _seed_actor_user(db: Database, user_id: int = ACTOR_ID) -> None:
    """Seed de un usuario dummy para satisfacer la FK del audit_log."""
    with db.transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO usuarios "
            "(id, username, password_hash, full_name, role_id, created_at, updated_at) "
            "VALUES (?, 'test_actor', 'hash', 'Test Actor', 2, "
            "'2024-01-01T00:00:00', '2024-01-01T00:00:00')",
            (user_id,),
        )


@pytest.fixture
def setup(
    tmp_path: Path,
) -> Tuple[AsistenciaService, Database, int, int, int, int, MagicMock]:
    """Crea DB + servicio + seed de 2 empleados activos y 1 turno.

    Returns:
        (service, db, emp_a_id, emp_b_id, emp_c_inactivo_id, turno_id,
         consolidacion_mock) — el mock permite a los tests de
         ``re_consolidar`` configurar el retorno sin montar el pipeline
         completo de consolidación (cubierto en
         ``test_consolidacion_service.py``).
    """
    db = Database(tmp_path / "test_asistencia_service.db")
    MigrationsRunner(db, MIGRATIONS_DIR).run()
    _seed_actor_user(db)

    depto = DepartamentoRepositorySQLite(db).create(Departamento(id=None, nombre="Administración"))
    cargo = CargoRepositorySQLite(db).create(Cargo(id=None, nombre="Secretaria"))
    assert depto.id is not None and cargo.id is not None

    emp_repo = EmpleadoRepositorySQLite(db)
    emp_a = emp_repo.create(
        Empleado(
            id=None,
            dni="0501-1990-11111",
            nombres="Ana",
            apellidos="Zapata",  # apellidos con Z para testear orden
            departamento_id=depto.id,
            cargo_id=cargo.id,
            fecha_ingreso="2020-01-15",
        )
    )
    emp_b = emp_repo.create(
        Empleado(
            id=None,
            dni="0501-1990-22222",
            nombres="Carlos",
            apellidos="Alvarado",  # apellidos con A — debería salir primero
            departamento_id=depto.id,
            cargo_id=cargo.id,
            fecha_ingreso="2021-03-20",
        )
    )
    emp_c = emp_repo.create(
        Empleado(
            id=None,
            dni="0501-1990-33333",
            nombres="Diego",
            apellidos="Mora",
            departamento_id=depto.id,
            cargo_id=cargo.id,
            fecha_ingreso="2019-07-01",
        )
    )
    assert emp_a.id is not None and emp_b.id is not None and emp_c.id is not None
    # Desactivamos C para confirmar que queda FUERA del combo de filtro.
    emp_repo.deactivate(emp_c.id, "2025-12-31", "RENUNCIA", None)

    turno = TurnoRepositorySQLite(db).create(
        Turno(
            id=None,
            nombre="Administrativo 8-5",
            hora_entrada="08:00",
            hora_salida="17:00",
            dias_semana=DIAS_LABORALES,
        )
    )
    assert turno.id is not None

    consolidacion_mock = MagicMock(spec=ConsolidacionService)
    service = AsistenciaService(
        asistencia_read=AsistenciaRepositorySQLite(db),
        asistencia_write=AsistenciaRepositorySQLite(db),
        empleado_read=emp_repo,
        turno_read=TurnoRepositorySQLite(db),
        audit_logger=AuditLogger(AuditLogRepositorySQLite(db), machine_name="test-host"),
        consolidacion_service=consolidacion_mock,
    )
    return service, db, emp_a.id, emp_b.id, emp_c.id, turno.id, consolidacion_mock


def _insert_asistencia(
    db: Database,
    empleado_id: int,
    turno_id: int,
    fecha: str,
    estado: str = EstadoAsistencia.PRESENTE.value,
    observaciones: str | None = None,
) -> int:
    """Atajo: inserta una asistencia vía el repo y devuelve su id."""
    repo = AsistenciaRepositorySQLite(db)
    creada = repo.upsert(
        Asistencia(
            id=None,
            empleado_id=empleado_id,
            fecha=fecha,
            estado=estado,
            turno_id_aplicado=turno_id,
            hora_entrada_real="08:00:00",
            hora_salida_real="17:00:00",
            observaciones=observaciones,
            consolidada_en="2026-04-24T10:00:00",
        )
    )
    assert creada.id is not None
    return creada.id


# ── list_asistencias ──────────────────────────────────────────────────────────


def test_list_asistencias_enriquece_con_nombres_y_turno(
    setup: Tuple[AsistenciaService, Database, int, int, int, int, MagicMock],
) -> None:
    service, db, emp_a_id, _, _, turno_id, _ = setup
    _insert_asistencia(db, emp_a_id, turno_id, "2026-04-15")
    resultado = service.list_asistencias(desde="2026-04-15", hasta="2026-04-15")
    assert isinstance(resultado, ResultadoBusquedaAsistencia)
    assert len(resultado.items) == 1
    item = resultado.items[0]
    assert item.asistencia.empleado_id == emp_a_id
    assert item.empleado_nombre_completo == "Zapata Ana"
    assert item.empleado_dni == "0501-1990-11111"
    assert item.turno_nombre == "Administrativo 8-5"
    assert resultado.truncado is False


def test_list_asistencias_filtra_por_empleado(
    setup: Tuple[AsistenciaService, Database, int, int, int, int, MagicMock],
) -> None:
    service, db, emp_a_id, emp_b_id, _, turno_id, _ = setup
    _insert_asistencia(db, emp_a_id, turno_id, "2026-04-15")
    _insert_asistencia(db, emp_b_id, turno_id, "2026-04-15")
    resultado = service.list_asistencias(
        desde="2026-04-15", hasta="2026-04-15", empleado_id=emp_b_id
    )
    assert len(resultado.items) == 1
    assert resultado.items[0].asistencia.empleado_id == emp_b_id


def test_list_asistencias_sin_filtro_incluye_ambos(
    setup: Tuple[AsistenciaService, Database, int, int, int, int, MagicMock],
) -> None:
    service, db, emp_a_id, emp_b_id, _, turno_id, _ = setup
    _insert_asistencia(db, emp_a_id, turno_id, "2026-04-15")
    _insert_asistencia(db, emp_b_id, turno_id, "2026-04-15")
    resultado = service.list_asistencias(desde="2026-04-15", hasta="2026-04-15")
    ids_vistos = {item.asistencia.empleado_id for item in resultado.items}
    assert ids_vistos == {emp_a_id, emp_b_id}


def test_list_asistencias_turno_null_no_rompe_enriquecimiento(
    setup: Tuple[AsistenciaService, Database, int, int, int, int, MagicMock],
) -> None:
    """SIN_TURNO lleva ``turno_id_aplicado = NULL`` → ``turno_nombre = None``."""
    service, db, emp_a_id, _, _, _, _ = setup
    repo = AsistenciaRepositorySQLite(db)
    repo.upsert(
        Asistencia(
            id=None,
            empleado_id=emp_a_id,
            fecha="2026-04-19",  # domingo
            estado=EstadoAsistencia.SIN_TURNO.value,
            turno_id_aplicado=None,
            hora_entrada_real=None,
            hora_salida_real=None,
            consolidada_en="2026-04-24T10:00:00",
        )
    )
    resultado = service.list_asistencias(desde="2026-04-19", hasta="2026-04-19")
    assert len(resultado.items) == 1
    assert resultado.items[0].turno_nombre is None


def test_list_asistencias_truncado_marca_flag(
    setup: Tuple[AsistenciaService, Database, int, int, int, int, MagicMock],
) -> None:
    """Si hay más filas que ``limit``, se corta y ``truncado = True``."""
    service, db, emp_a_id, _, _, turno_id, _ = setup
    for dia in range(1, 6):  # 5 filas
        _insert_asistencia(db, emp_a_id, turno_id, f"2026-04-{dia:02d}")
    resultado = service.list_asistencias(desde="2026-04-01", hasta="2026-04-30", limit=3)
    assert len(resultado.items) == 3
    assert resultado.truncado is True


def test_list_asistencias_limit_default_no_trunca(
    setup: Tuple[AsistenciaService, Database, int, int, int, int, MagicMock],
) -> None:
    """Con el default (500) y pocas filas, no trunca."""
    service, db, emp_a_id, _, _, turno_id, _ = setup
    _insert_asistencia(db, emp_a_id, turno_id, "2026-04-15")
    resultado = service.list_asistencias(desde="2026-04-15", hasta="2026-04-15")
    assert resultado.truncado is False


def test_list_asistencias_rango_vacio(
    setup: Tuple[AsistenciaService, Database, int, int, int, int, MagicMock],
) -> None:
    service, _, _, _, _, _, _ = setup
    resultado = service.list_asistencias(desde="2026-04-01", hasta="2026-04-30")
    assert resultado.items == []
    assert resultado.truncado is False


# ── list_empleados_para_filtro ────────────────────────────────────────────────


def test_list_empleados_para_filtro_solo_activos_y_ordenados(
    setup: Tuple[AsistenciaService, Database, int, int, int, int, MagicMock],
) -> None:
    """Solo activos (C está archivado → fuera); orden por apellidos+nombres."""
    service, _, emp_a_id, emp_b_id, emp_c_id, _, _ = setup
    tuplas = service.list_empleados_para_filtro()
    ids_devueltos = [t[0] for t in tuplas]
    assert emp_c_id not in ids_devueltos  # archivado → fuera
    assert ids_devueltos == [emp_b_id, emp_a_id]  # Alvarado < Zapata
    assert [t[1] for t in tuplas] == ["Alvarado Carlos", "Zapata Ana"]


# ── update_observaciones ──────────────────────────────────────────────────────


def test_update_observaciones_happy_path(
    setup: Tuple[AsistenciaService, Database, int, int, int, int, MagicMock],
) -> None:
    service, db, emp_a_id, _, _, turno_id, _ = setup
    asist_id = _insert_asistencia(db, emp_a_id, turno_id, "2026-04-15")
    service.update_observaciones(asist_id, "Justificación médica", actor_user_id=ACTOR_ID)
    leida = AsistenciaRepositorySQLite(db).get_by_empleado_y_fecha(emp_a_id, "2026-04-15")
    assert leida is not None and leida.observaciones == "Justificación médica"


def test_update_observaciones_id_inexistente_traduce_a_error_tipado(
    setup: Tuple[AsistenciaService, Database, int, int, int, int, MagicMock],
) -> None:
    service, _, _, _, _, _, _ = setup
    with pytest.raises(AsistenciaNotFoundError) as exc_info:
        service.update_observaciones(9999, "X", actor_user_id=ACTOR_ID)
    assert exc_info.value.asistencia_id == 9999


def test_update_observaciones_normaliza_vacio_a_none(
    setup: Tuple[AsistenciaService, Database, int, int, int, int, MagicMock],
) -> None:
    service, db, emp_a_id, _, _, turno_id, _ = setup
    asist_id = _insert_asistencia(db, emp_a_id, turno_id, "2026-04-15", observaciones="Previa")
    # "   " (solo espacios) debe persistirse como NULL, no como "   ".
    service.update_observaciones(asist_id, "   ", actor_user_id=ACTOR_ID)
    leida = AsistenciaRepositorySQLite(db).get_by_empleado_y_fecha(emp_a_id, "2026-04-15")
    assert leida is not None and leida.observaciones is None


def test_update_observaciones_registra_en_audit_log(
    setup: Tuple[AsistenciaService, Database, int, int, int, int, MagicMock],
) -> None:
    service, db, emp_a_id, _, _, turno_id, _ = setup
    asist_id = _insert_asistencia(db, emp_a_id, turno_id, "2026-04-15")
    service.update_observaciones(asist_id, "Nota", actor_user_id=ACTOR_ID)
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT action, user_id, details FROM audit_log "
            "WHERE action = 'asistencia_observaciones_updated' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    assert row["user_id"] == ACTOR_ID
    assert f'"asistencia_id": {asist_id}' in row["details"]


# ── re_consolidar (Sub-3.4c) ──────────────────────────────────────────────────


def _fake_resultado_consolidacion(upsertadas: int = 3, dias: int = 3) -> ResultadoConsolidacion:
    """Construye un ``ResultadoConsolidacion`` mínimo para el stub."""
    return ResultadoConsolidacion(
        empleados_procesados=1,
        dias_procesados=dias,
        asistencias_upsertadas=upsertadas,
        marcadas_desconocidas=[],
    )


def test_re_consolidar_delega_al_consolidacion_service(
    setup: Tuple[AsistenciaService, Database, int, int, int, int, MagicMock],
) -> None:
    """``re_consolidar`` llama al servicio de consolidación con los mismos args."""
    service, _, emp_a_id, _, _, _, consolidacion_mock = setup
    consolidacion_mock.consolidar_rango.return_value = _fake_resultado_consolidacion()

    resultado = service.re_consolidar(
        desde="2026-04-13",
        hasta="2026-04-15",
        empleado_id=emp_a_id,
        actor_user_id=ACTOR_ID,
    )

    consolidacion_mock.consolidar_rango.assert_called_once_with(
        "2026-04-13", "2026-04-15", emp_a_id
    )
    assert resultado.asistencias_upsertadas == 3


def test_re_consolidar_registra_en_audit_log_con_actor_y_empleado(
    setup: Tuple[AsistenciaService, Database, int, int, int, int, MagicMock],
) -> None:
    """La acción queda como ``asistencia_re_consolidada`` con actor + detalles."""
    service, db, emp_a_id, _, _, _, consolidacion_mock = setup
    consolidacion_mock.consolidar_rango.return_value = _fake_resultado_consolidacion(
        upsertadas=5, dias=5
    )

    service.re_consolidar(
        desde="2026-04-13",
        hasta="2026-04-17",
        empleado_id=emp_a_id,
        actor_user_id=ACTOR_ID,
    )

    with db.transaction() as conn:
        row = conn.execute(
            "SELECT action, user_id, details FROM audit_log "
            "WHERE action = 'asistencia_re_consolidada' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    assert row["user_id"] == ACTOR_ID
    assert '"desde": "2026-04-13"' in row["details"]
    assert '"hasta": "2026-04-17"' in row["details"]
    assert f'"empleado_id": {emp_a_id}' in row["details"]
    assert '"asistencias_upsertadas": 5' in row["details"]


def test_re_consolidar_sin_empleado_audita_empleado_id_null(
    setup: Tuple[AsistenciaService, Database, int, int, int, int, MagicMock],
) -> None:
    """Si ``empleado_id`` es ``None``, el audit payload lo serializa como ``null``."""
    service, db, _, _, _, _, consolidacion_mock = setup
    consolidacion_mock.consolidar_rango.return_value = _fake_resultado_consolidacion()

    service.re_consolidar(
        desde="2026-04-13",
        hasta="2026-04-17",
        empleado_id=None,
        actor_user_id=ACTOR_ID,
    )

    with db.transaction() as conn:
        row = conn.execute(
            "SELECT details FROM audit_log "
            "WHERE action = 'asistencia_re_consolidada' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    assert '"empleado_id": null' in row["details"]
