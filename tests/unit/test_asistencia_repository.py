"""Tests del AsistenciaRepositorySQLite.

Cubre el contrato crítico del repositorio de consolidación:

    - ``upsert`` inserta cuando no existe (empleado, fecha).
    - ``upsert`` ACTUALIZA los campos calculados si ya existe la fila
      (empleado, fecha) — **pero PRESERVA ``observaciones``** (regla
      de consolidación idempotente: las anotaciones manuales del
      operador no se pierden en re-consolidaciones).
    - ``update_observaciones`` mueve sólo ``observaciones`` y valida id.
    - Lecturas canónicas: por (empleado, fecha), por rango de empleado,
      por fecha, por rango. Todas con ordenamiento determinístico.
    - FKs (empleado, turno) + CHECK del estado + CHECK de minutos >= 0.
    - UNIQUE (empleado_id, fecha) protege contra inserts duplicados
      que no pasen por UPSERT.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional, Tuple

import pytest

from core.models.asistencia import OBSERVACION_SALIDA_ASUMIDA, Asistencia, EstadoAsistencia
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


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def setup(
    tmp_path: Path,
) -> Tuple[AsistenciaRepositorySQLite, int, int, Database]:
    """DB + depto + cargo + 1 empleado + 1 turno seedeados.

    Returns:
        (repo, empleado_id, turno_id, db).
    """
    db = Database(tmp_path / "test_asistencia.db")
    MigrationsRunner(db, MIGRATIONS_DIR).run()

    depto = DepartamentoRepositorySQLite(db).create(Departamento(id=None, nombre="Administración"))
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
    turno = TurnoRepositorySQLite(db).create(
        Turno(
            id=None,
            nombre="Administrativo 8-5",
            hora_entrada="08:00",
            hora_salida="17:00",
            dias_semana=DIAS_LABORALES,
        )
    )
    assert emp.id is not None and turno.id is not None
    return AsistenciaRepositorySQLite(db), emp.id, turno.id, db


def _nueva_asistencia(
    empleado_id: int,
    turno_id: int,
    fecha: str = "2026-04-15",
    estado: str = EstadoAsistencia.PRESENTE.value,
    hora_entrada: str = "08:00:00",
    hora_salida: str = "17:00:00",
    minutos_tarde: int = 0,
    minutos_salida_temprana: int = 0,
    observaciones: Optional[str] = None,
) -> Asistencia:
    return Asistencia(
        id=None,
        empleado_id=empleado_id,
        fecha=fecha,
        estado=estado,
        turno_id_aplicado=turno_id,
        hora_entrada_real=hora_entrada,
        hora_salida_real=hora_salida,
        minutos_tarde=minutos_tarde,
        minutos_salida_temprana=minutos_salida_temprana,
        observaciones=observaciones,
        consolidada_en="2026-04-24T10:00:00",
    )


def _seed_segundo_empleado(db: Database, depto_id: int, cargo_id: int) -> int:
    """Crea un segundo empleado y devuelve su id. Para tests de listados."""
    emp = EmpleadoRepositorySQLite(db).create(
        Empleado(
            id=None,
            dni="0501-1990-99999",
            nombres="María",
            apellidos="López",
            departamento_id=depto_id,
            cargo_id=cargo_id,
            fecha_ingreso="2021-06-01",
        )
    )
    assert emp.id is not None
    return emp.id


# ── Tests: upsert (insert path) ───────────────────────────────────────────────


def test_upsert_inserta_cuando_no_existe(
    setup: Tuple[AsistenciaRepositorySQLite, int, int, Database],
) -> None:
    repo, emp_id, turno_id, _ = setup
    resultado = repo.upsert(_nueva_asistencia(emp_id, turno_id))
    assert resultado.id is not None and resultado.id > 0
    assert resultado.empleado_id == emp_id
    assert resultado.estado == EstadoAsistencia.PRESENTE.value
    assert resultado.hora_entrada_real == "08:00:00"


def test_upsert_persiste_observaciones_en_insert(
    setup: Tuple[AsistenciaRepositorySQLite, int, int, Database],
) -> None:
    """Si la fila es nueva, observaciones pasadas sí se guardan."""
    repo, emp_id, turno_id, _ = setup
    r = repo.upsert(_nueva_asistencia(emp_id, turno_id, observaciones="Justificación médica"))
    assert r.observaciones == "Justificación médica"


# ── Tests: upsert (conflict path) — REGLA CRÍTICA ─────────────────────────────


def test_upsert_actualiza_campos_calculados_en_conflicto(
    setup: Tuple[AsistenciaRepositorySQLite, int, int, Database],
) -> None:
    """En conflicto (mismo empleado, misma fecha) se sobrescriben los
    campos calculados por la consolidación."""
    repo, emp_id, turno_id, _ = setup
    original = repo.upsert(
        _nueva_asistencia(
            emp_id,
            turno_id,
            estado=EstadoAsistencia.TARDE.value,
            hora_entrada="08:20:00",
            minutos_tarde=20,
        )
    )
    # Re-consolidación: el empleado llegó dentro de tolerancia después
    # de ajustar el turno — el estado recalculado es PRESENTE.
    reconsolidada = repo.upsert(
        _nueva_asistencia(
            emp_id,
            turno_id,
            estado=EstadoAsistencia.PRESENTE.value,
            hora_entrada="08:05:00",
            minutos_tarde=0,
        )
    )
    # Misma PK (no duplica fila).
    assert reconsolidada.id == original.id
    assert reconsolidada.estado == EstadoAsistencia.PRESENTE.value
    assert reconsolidada.hora_entrada_real == "08:05:00"
    assert reconsolidada.minutos_tarde == 0


def test_upsert_preserva_observaciones_en_conflicto(
    setup: Tuple[AsistenciaRepositorySQLite, int, int, Database],
) -> None:
    """Invariante del ON CONFLICT: ``observaciones`` NO se sobrescribe.

    Escenario típico:
        1. Consolidación inicial crea la fila (sin observaciones).
        2. Operador anota manualmente "Justificación médica".
        3. Se re-consolida el día tras ajustar tolerancia → NO debe
           perder la anotación manual.
    """
    repo, emp_id, turno_id, _ = setup
    # Paso 1: insert inicial.
    creada = repo.upsert(_nueva_asistencia(emp_id, turno_id))
    assert creada.id is not None
    # Paso 2: anotación manual.
    repo.update_observaciones(creada.id, "Justificación médica")
    # Paso 3: re-consolidación con observaciones=None → NO debe limpiar.
    reconsolidada = repo.upsert(
        _nueva_asistencia(
            emp_id,
            turno_id,
            estado=EstadoAsistencia.TARDE.value,
            minutos_tarde=15,
            observaciones=None,
        )
    )
    assert reconsolidada.id == creada.id
    assert reconsolidada.estado == EstadoAsistencia.TARDE.value
    # ↓ REGLA CRÍTICA: la anotación manual sobrevive.
    assert reconsolidada.observaciones == "Justificación médica"


def test_upsert_no_permite_sobrescribir_observaciones_desde_el_caller(
    setup: Tuple[AsistenciaRepositorySQLite, int, int, Database],
) -> None:
    """Aun pasando ``observaciones="X"`` en el UPSERT de conflicto, el
    valor previo se preserva. El caller debe usar
    ``update_observaciones`` para modificarlas explícitamente."""
    repo, emp_id, turno_id, _ = setup
    creada = repo.upsert(_nueva_asistencia(emp_id, turno_id, observaciones="Primera nota"))
    assert creada.id is not None
    reconsolidada = repo.upsert(
        _nueva_asistencia(emp_id, turno_id, observaciones="Intento de sobrescritura")
    )
    assert reconsolidada.observaciones == "Primera nota"


def test_upsert_pone_la_nota_de_salida_asumida_cuando_el_dia_cierra(
    setup: Tuple[AsistenciaRepositorySQLite, int, int, Database],
) -> None:
    """De tarde quedó "sin marca de salida"; al cerrar el día se asume la salida.

    La nota automática debe guardarse aunque la fila ya existiera (si no,
    el reporte mostraría la hora del turno como si hubiera marcado).
    """
    repo, emp_id, turno_id, _ = setup
    repo.upsert(
        _nueva_asistencia(
            emp_id, turno_id, estado=EstadoAsistencia.INCOMPLETO.value, hora_salida=""
        )
    )
    cerrada = repo.upsert(
        _nueva_asistencia(emp_id, turno_id, observaciones=OBSERVACION_SALIDA_ASUMIDA)
    )
    assert cerrada.observaciones == OBSERVACION_SALIDA_ASUMIDA


def test_upsert_quita_la_nota_automatica_si_llega_la_marca_de_salida(
    setup: Tuple[AsistenciaRepositorySQLite, int, int, Database],
) -> None:
    repo, emp_id, turno_id, _ = setup
    repo.upsert(_nueva_asistencia(emp_id, turno_id, observaciones=OBSERVACION_SALIDA_ASUMIDA))
    con_marca = repo.upsert(_nueva_asistencia(emp_id, turno_id, hora_salida="17:04:00"))
    assert con_marca.observaciones is None


def test_upsert_no_pisa_la_nota_del_operador_con_la_automatica(
    setup: Tuple[AsistenciaRepositorySQLite, int, int, Database],
) -> None:
    repo, emp_id, turno_id, _ = setup
    creada = repo.upsert(_nueva_asistencia(emp_id, turno_id))
    assert creada.id is not None
    repo.update_observaciones(creada.id, "Salió a una diligencia")
    cerrada = repo.upsert(
        _nueva_asistencia(emp_id, turno_id, observaciones=OBSERVACION_SALIDA_ASUMIDA)
    )
    assert cerrada.observaciones == "Salió a una diligencia"


def test_upsert_respeta_unique_empleado_fecha(
    setup: Tuple[AsistenciaRepositorySQLite, int, int, Database],
) -> None:
    """Dos upserts con la misma (empleado, fecha) mantienen una sola fila."""
    repo, emp_id, turno_id, _ = setup
    repo.upsert(_nueva_asistencia(emp_id, turno_id, fecha="2026-04-15"))
    repo.upsert(_nueva_asistencia(emp_id, turno_id, fecha="2026-04-15"))
    asistencias = repo.list_by_fecha("2026-04-15")
    assert len(asistencias) == 1


# ── Tests: upsert (integrity) ─────────────────────────────────────────────────


def test_upsert_empleado_inexistente_falla(
    setup: Tuple[AsistenciaRepositorySQLite, int, int, Database],
) -> None:
    repo, _, turno_id, _ = setup
    with pytest.raises(sqlite3.IntegrityError):
        repo.upsert(_nueva_asistencia(9999, turno_id))


def test_upsert_turno_inexistente_falla(
    setup: Tuple[AsistenciaRepositorySQLite, int, int, Database],
) -> None:
    repo, emp_id, _, _ = setup
    with pytest.raises(sqlite3.IntegrityError):
        repo.upsert(_nueva_asistencia(emp_id, 9999))


def test_upsert_estado_invalido_falla_por_check(
    setup: Tuple[AsistenciaRepositorySQLite, int, int, Database],
) -> None:
    repo, emp_id, turno_id, _ = setup
    with pytest.raises(sqlite3.IntegrityError):
        repo.upsert(_nueva_asistencia(emp_id, turno_id, estado="INVENTADO"))


def test_upsert_minutos_negativos_falla_por_check(
    setup: Tuple[AsistenciaRepositorySQLite, int, int, Database],
) -> None:
    repo, emp_id, turno_id, _ = setup
    with pytest.raises(sqlite3.IntegrityError):
        repo.upsert(_nueva_asistencia(emp_id, turno_id, minutos_tarde=-1))
    with pytest.raises(sqlite3.IntegrityError):
        repo.upsert(_nueva_asistencia(emp_id, turno_id, minutos_salida_temprana=-1))


def test_upsert_turno_null_permitido(
    setup: Tuple[AsistenciaRepositorySQLite, int, int, Database],
) -> None:
    """SIN_TURNO/FERIADO llevan ``turno_id_aplicado = NULL`` — válido por schema."""
    repo, emp_id, _, _ = setup
    resultado = repo.upsert(
        Asistencia(
            id=None,
            empleado_id=emp_id,
            fecha="2026-04-19",  # domingo
            estado=EstadoAsistencia.SIN_TURNO.value,
            turno_id_aplicado=None,
            hora_entrada_real=None,
            hora_salida_real=None,
            consolidada_en="2026-04-24T10:00:00",
        )
    )
    assert resultado.id is not None
    assert resultado.turno_id_aplicado is None


# ── Tests: get_by_empleado_y_fecha ────────────────────────────────────────────


def test_get_by_empleado_y_fecha_inexistente_devuelve_none(
    setup: Tuple[AsistenciaRepositorySQLite, int, int, Database],
) -> None:
    repo, emp_id, _, _ = setup
    assert repo.get_by_empleado_y_fecha(emp_id, "2026-04-15") is None


def test_get_by_empleado_y_fecha_encuentra(
    setup: Tuple[AsistenciaRepositorySQLite, int, int, Database],
) -> None:
    repo, emp_id, turno_id, _ = setup
    repo.upsert(_nueva_asistencia(emp_id, turno_id, fecha="2026-04-15"))
    leida = repo.get_by_empleado_y_fecha(emp_id, "2026-04-15")
    assert leida is not None
    assert leida.fecha == "2026-04-15"


# ── Tests: list_by_empleado_y_rango ───────────────────────────────────────────


def test_list_by_empleado_y_rango_ordena_asc_inclusive(
    setup: Tuple[AsistenciaRepositorySQLite, int, int, Database],
) -> None:
    repo, emp_id, turno_id, _ = setup
    # Insertados en orden no cronológico.
    repo.upsert(_nueva_asistencia(emp_id, turno_id, fecha="2026-04-30"))
    repo.upsert(_nueva_asistencia(emp_id, turno_id, fecha="2026-04-01"))
    repo.upsert(_nueva_asistencia(emp_id, turno_id, fecha="2026-04-15"))
    # Fuera de rango.
    repo.upsert(_nueva_asistencia(emp_id, turno_id, fecha="2026-03-31"))
    repo.upsert(_nueva_asistencia(emp_id, turno_id, fecha="2026-05-01"))

    resultado = repo.list_by_empleado_y_rango(emp_id, "2026-04-01", "2026-04-30")
    assert [a.fecha for a in resultado] == [
        "2026-04-01",
        "2026-04-15",
        "2026-04-30",
    ]


def test_list_by_empleado_y_rango_vacio(
    setup: Tuple[AsistenciaRepositorySQLite, int, int, Database],
) -> None:
    repo, emp_id, _, _ = setup
    assert repo.list_by_empleado_y_rango(emp_id, "2026-04-01", "2026-04-30") == []


def test_list_by_empleado_y_rango_respeta_limit(
    setup: Tuple[AsistenciaRepositorySQLite, int, int, Database],
) -> None:
    """Con ``limit=N`` se devuelven solo las primeras N filas tras el ORDER BY."""
    repo, emp_id, turno_id, _ = setup
    # Insertamos 5 filas; pedimos las 3 primeras en orden cronológico.
    for fecha in ("2026-04-05", "2026-04-01", "2026-04-03", "2026-04-04", "2026-04-02"):
        repo.upsert(_nueva_asistencia(emp_id, turno_id, fecha=fecha))
    resultado = repo.list_by_empleado_y_rango(emp_id, "2026-04-01", "2026-04-30", limit=3)
    assert [a.fecha for a in resultado] == ["2026-04-01", "2026-04-02", "2026-04-03"]


def test_list_by_empleado_y_rango_limit_none_trae_todos(
    setup: Tuple[AsistenciaRepositorySQLite, int, int, Database],
) -> None:
    """``limit=None`` (default) no corta."""
    repo, emp_id, turno_id, _ = setup
    for fecha in ("2026-04-01", "2026-04-02", "2026-04-03"):
        repo.upsert(_nueva_asistencia(emp_id, turno_id, fecha=fecha))
    resultado = repo.list_by_empleado_y_rango(emp_id, "2026-04-01", "2026-04-30")
    assert len(resultado) == 3


# ── Tests: list_by_fecha ──────────────────────────────────────────────────────


def test_list_by_fecha_ordena_por_empleado_id(
    setup: Tuple[AsistenciaRepositorySQLite, int, int, Database],
) -> None:
    repo, emp_id_1, turno_id, db = setup
    # Recuperamos los ids seeded para crear un segundo empleado.
    depto_seed = DepartamentoRepositorySQLite(db).get_by_nombre("Administración")
    cargo_seed = CargoRepositorySQLite(db).get_by_nombre("Secretaria")
    assert depto_seed is not None and cargo_seed is not None
    depto_id = depto_seed.id
    cargo_id = cargo_seed.id
    assert depto_id is not None and cargo_id is not None
    emp_id_2 = _seed_segundo_empleado(db, depto_id, cargo_id)

    repo.upsert(_nueva_asistencia(emp_id_2, turno_id, fecha="2026-04-15"))
    repo.upsert(_nueva_asistencia(emp_id_1, turno_id, fecha="2026-04-15"))

    resultado = repo.list_by_fecha("2026-04-15")
    assert [a.empleado_id for a in resultado] == sorted([emp_id_1, emp_id_2])


# ── Tests: list_by_rango ──────────────────────────────────────────────────────


def test_list_by_rango_ordena_por_fecha_y_empleado(
    setup: Tuple[AsistenciaRepositorySQLite, int, int, Database],
) -> None:
    repo, emp_id_1, turno_id, db = setup
    depto_seed = DepartamentoRepositorySQLite(db).get_by_nombre("Administración")
    cargo_seed = CargoRepositorySQLite(db).get_by_nombre("Secretaria")
    assert depto_seed is not None and cargo_seed is not None
    depto_id = depto_seed.id
    cargo_id = cargo_seed.id
    assert depto_id is not None and cargo_id is not None
    emp_id_2 = _seed_segundo_empleado(db, depto_id, cargo_id)

    # Orden deliberadamente mezclado en el insert.
    repo.upsert(_nueva_asistencia(emp_id_2, turno_id, fecha="2026-04-16"))
    repo.upsert(_nueva_asistencia(emp_id_1, turno_id, fecha="2026-04-16"))
    repo.upsert(_nueva_asistencia(emp_id_2, turno_id, fecha="2026-04-15"))
    repo.upsert(_nueva_asistencia(emp_id_1, turno_id, fecha="2026-04-15"))

    resultado = repo.list_by_rango("2026-04-15", "2026-04-16")
    # Primero por fecha ASC, luego por empleado_id ASC.
    tuplas = [(a.fecha, a.empleado_id) for a in resultado]
    esperado = sorted(
        [
            ("2026-04-15", emp_id_1),
            ("2026-04-15", emp_id_2),
            ("2026-04-16", emp_id_1),
            ("2026-04-16", emp_id_2),
        ]
    )
    assert tuplas == esperado


def test_list_by_rango_respeta_limit(
    setup: Tuple[AsistenciaRepositorySQLite, int, int, Database],
) -> None:
    """``list_by_rango`` corta a N tras ORDER BY fecha, empleado_id."""
    repo, emp_id_1, turno_id, db = setup
    depto_seed = DepartamentoRepositorySQLite(db).get_by_nombre("Administración")
    cargo_seed = CargoRepositorySQLite(db).get_by_nombre("Secretaria")
    assert depto_seed is not None and cargo_seed is not None
    assert depto_seed.id is not None and cargo_seed.id is not None
    emp_id_2 = _seed_segundo_empleado(db, depto_seed.id, cargo_seed.id)

    # 4 filas: (15, emp1), (15, emp2), (16, emp1), (16, emp2).
    repo.upsert(_nueva_asistencia(emp_id_2, turno_id, fecha="2026-04-16"))
    repo.upsert(_nueva_asistencia(emp_id_1, turno_id, fecha="2026-04-16"))
    repo.upsert(_nueva_asistencia(emp_id_2, turno_id, fecha="2026-04-15"))
    repo.upsert(_nueva_asistencia(emp_id_1, turno_id, fecha="2026-04-15"))

    resultado = repo.list_by_rango("2026-04-15", "2026-04-16", limit=2)
    tuplas = [(a.fecha, a.empleado_id) for a in resultado]
    esperado = sorted([("2026-04-15", emp_id_1), ("2026-04-15", emp_id_2)])
    assert tuplas == esperado


# ── Tests: update_observaciones ───────────────────────────────────────────────


def test_update_observaciones_cambia_texto(
    setup: Tuple[AsistenciaRepositorySQLite, int, int, Database],
) -> None:
    repo, emp_id, turno_id, _ = setup
    creada = repo.upsert(_nueva_asistencia(emp_id, turno_id))
    assert creada.id is not None
    repo.update_observaciones(creada.id, "Anotación nueva")
    leida = repo.get_by_empleado_y_fecha(emp_id, "2026-04-15")
    assert leida is not None and leida.observaciones == "Anotación nueva"


def test_update_observaciones_a_none_limpia(
    setup: Tuple[AsistenciaRepositorySQLite, int, int, Database],
) -> None:
    repo, emp_id, turno_id, _ = setup
    creada = repo.upsert(_nueva_asistencia(emp_id, turno_id, observaciones="Por limpiar"))
    assert creada.id is not None
    repo.update_observaciones(creada.id, None)
    leida = repo.get_by_empleado_y_fecha(emp_id, "2026-04-15")
    assert leida is not None and leida.observaciones is None


def test_update_observaciones_no_toca_campos_calculados(
    setup: Tuple[AsistenciaRepositorySQLite, int, int, Database],
) -> None:
    repo, emp_id, turno_id, _ = setup
    creada = repo.upsert(
        _nueva_asistencia(
            emp_id,
            turno_id,
            estado=EstadoAsistencia.TARDE.value,
            minutos_tarde=15,
        )
    )
    assert creada.id is not None
    repo.update_observaciones(creada.id, "X")
    leida = repo.get_by_empleado_y_fecha(emp_id, "2026-04-15")
    assert leida is not None
    assert leida.estado == EstadoAsistencia.TARDE.value
    assert leida.minutos_tarde == 15


def test_update_observaciones_id_inexistente_lanza_value_error(
    setup: Tuple[AsistenciaRepositorySQLite, int, int, Database],
) -> None:
    repo, _, _, _ = setup
    with pytest.raises(ValueError, match="No existe"):
        repo.update_observaciones(9999, "X")
