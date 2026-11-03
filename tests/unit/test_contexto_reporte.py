"""Tests de ``ContextoReporte`` y de la "primera fecha" del período "Todo".

Con una BD real (migraciones aplicadas): el contexto debe traer la ficha
de cada empleado, TODAS las marcas guardadas del período (también las de
IDs sin empleado), el horario de cada día y la nota de las ausencias.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Tuple

import pytest

from core.models.asistencia import Asistencia, EstadoAsistencia
from core.models.ausencia import Ausencia
from core.models.cargo import Cargo
from core.models.departamento import Departamento
from core.models.dispositivo import Dispositivo
from core.models.empleado import Empleado
from core.models.registro_raw import RegistroRaw
from core.models.sincronizacion import Sincronizacion
from core.models.turno import DIAS_LABORALES, SABADO, HorarioTurno, Turno
from core.repositories.asistencia_repository_sqlite import AsistenciaRepositorySQLite
from core.repositories.ausencia_repository_sqlite import AusenciaRepositorySQLite
from core.repositories.cargo_repository_sqlite import CargoRepositorySQLite
from core.repositories.departamento_repository_sqlite import DepartamentoRepositorySQLite
from core.repositories.dispositivo_repository_sqlite import DispositivoRepositorySQLite
from core.repositories.empleado_repository_sqlite import EmpleadoRepositorySQLite
from core.repositories.registro_raw_repository_sqlite import RegistroRawRepositorySQLite
from core.repositories.sincronizacion_repository_sqlite import SincronizacionRepositorySQLite
from core.repositories.turno_repository_sqlite import TurnoRepositorySQLite
from core.services.contexto_reporte import ContextoReporte
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner
from ui import periodos

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "infrastructure" / "database" / "migrations"


@pytest.fixture
def bd(tmp_path: Path) -> Tuple[Database, ContextoReporte, int, int, int]:
    """BD con 2 empleados (IDs de reloj 7 y 8), un turno, un reloj y 4 marcas.

    Returns:
        (db, contexto, id de Juan, id de Ana, id del turno).
    """
    db = Database(tmp_path / "contexto.db")
    MigrationsRunner(db, MIGRATIONS_DIR).run()
    dep = DepartamentoRepositorySQLite(db).create(Departamento(id=None, nombre="Ventas"))
    cargo = CargoRepositorySQLite(db).create(Cargo(id=None, nombre="Vendedor"))
    assert dep.id is not None and cargo.id is not None
    empleados = EmpleadoRepositorySQLite(db)
    juan = empleados.create(
        Empleado(
            None, "0801-1990-00001", "Juan", "Pérez", dep.id, cargo.id, "2020-01-01", zkteco_id=7
        )
    )
    ana = empleados.create(
        Empleado(
            None, "0801-1990-00002", "Ana", "López", dep.id, cargo.id, "2020-01-01", zkteco_id=8
        )
    )
    turno = TurnoRepositorySQLite(db).create(
        Turno(
            id=None,
            nombre="Administrativo",
            hora_entrada="08:00",
            hora_salida="17:00",
            dias_semana=DIAS_LABORALES,
            horarios_extra=[HorarioTurno(SABADO, "08:00", "12:00")],
        )
    )
    reloj = DispositivoRepositorySQLite(db).create(
        Dispositivo(id=None, nombre="Reloj Principal", ip="192.168.1.201", puerto=4370)
    )
    assert juan.id and ana.id and turno.id and reloj.id
    sync = SincronizacionRepositorySQLite(db).create(
        Sincronizacion(
            None, reloj.id, None, "2026-11-02T22:00:00+00:00", "2026-11-02", "2026-11-02"
        )
    )
    assert sync.id is not None
    RegistroRawRepositorySQLite(db).create_bulk(
        [
            RegistroRaw(None, reloj.id, sync.id, 7, "2026-11-02T08:01:00"),
            RegistroRaw(None, reloj.id, sync.id, 7, "2026-11-02T17:05:00"),
            RegistroRaw(None, reloj.id, sync.id, 98, "2026-11-02T09:00:00"),
            RegistroRaw(None, reloj.id, sync.id, 8, "2026-10-31T08:00:00"),  # fuera del rango
        ]
    )
    AusenciaRepositorySQLite(db).create(
        Ausencia(
            None,
            ana.id,
            "VACACIONES",
            "2026-10-30",
            "2026-11-04",
            nota="Programadas.",
            creado_en="2026-10-29T15:00:00+00:00",
        )
    )
    contexto = ContextoReporte(
        empleado_read=empleados,
        departamento_read=DepartamentoRepositorySQLite(db),
        cargo_read=CargoRepositorySQLite(db),
        turno_read=TurnoRepositorySQLite(db),
        registro_raw_read=RegistroRawRepositorySQLite(db),
        dispositivo_read=DispositivoRepositorySQLite(db),
        ausencia_read=AusenciaRepositorySQLite(db),
    )
    return db, contexto, juan.id, ana.id, turno.id


def test_trae_la_ficha_de_cada_empleado(
    bd: Tuple[Database, ContextoReporte, int, int, int],
) -> None:
    _, contexto, juan, _, _ = bd
    ficha = contexto.cargar("2026-11-01", "2026-11-03").empleados[juan]
    assert (ficha.nombre, ficha.id_reloj, ficha.departamento, ficha.cargo) == (
        "Pérez Juan",
        7,
        "Ventas",
        "Vendedor",
    )


def test_trae_todas_las_marcas_del_periodo_en_orden(
    bd: Tuple[Database, ContextoReporte, int, int, int],
) -> None:
    _, contexto, juan, _, _ = bd
    marcas = contexto.cargar("2026-11-01", "2026-11-03").marcas
    assert [(m.momento[11:16], m.id_reloj, m.empleado_id) for m in marcas] == [
        ("08:01", 7, juan),
        ("09:00", 98, None),  # ID que no es de ningún empleado: también va
        ("17:05", 7, juan),
    ]
    assert {m.reloj for m in marcas} == {"Reloj Principal"}


def test_con_empleado_solo_trae_sus_marcas_y_notas(
    bd: Tuple[Database, ContextoReporte, int, int, int],
) -> None:
    _, contexto, juan, ana, _ = bd
    extras = contexto.cargar("2026-11-01", "2026-11-03", empleado_id=juan)
    assert {m.empleado_id for m in extras.marcas} == {juan}
    assert extras.notas_ausencia == {}
    assert contexto.cargar("2026-11-01", "2026-11-03", empleado_id=ana).marcas == []


def test_horario_de_cada_dia_del_turno(bd: Tuple[Database, ContextoReporte, int, int, int]) -> None:
    _, contexto, _, _, turno = bd
    horarios = contexto.cargar("2026-11-01", "2026-11-03").horarios
    assert horarios[(turno, 0)].texto == "08:00–17:00"  # lunes
    assert horarios[(turno, 5)].texto == "08:00–12:00"  # sábado (horario alterno)
    assert horarios[(turno, 0)].minutos == 9 * 60
    assert (turno, 6) not in horarios  # domingo no le toca


def test_nota_de_la_ausencia_solo_dentro_del_periodo(
    bd: Tuple[Database, ContextoReporte, int, int, int],
) -> None:
    _, contexto, _, ana, _ = bd
    notas = contexto.cargar("2026-11-01", "2026-11-03").notas_ausencia
    assert notas == {
        (ana, "2026-11-01"): "Vacaciones: Programadas.",
        (ana, "2026-11-02"): "Vacaciones: Programadas.",
        (ana, "2026-11-03"): "Vacaciones: Programadas.",
    }


def test_trae_los_permisos_registrados_con_su_nota_y_quien_los_registro(
    bd: Tuple[Database, ContextoReporte, int, int, int],
) -> None:
    _, contexto, juan, ana, _ = bd
    registros = contexto.cargar("2026-11-01", "2026-11-03").ausencias
    assert len(registros) == 1
    r = registros[0]
    assert (r.empleado_id, r.tipo, r.desde, r.hasta, r.nota) == (
        ana,
        "VACACIONES",
        "2026-10-30",
        "2026-11-04",
        "Programadas.",
    )
    assert r.registrado_el == "2026-10-29T15:00:00+00:00"
    assert contexto.cargar("2026-11-01", "2026-11-03", empleado_id=juan).ausencias == []


def test_primera_fecha_con_asistencia(bd: Tuple[Database, ContextoReporte, int, int, int]) -> None:
    db, _, juan, ana, turno = bd
    repo = AsistenciaRepositorySQLite(db)
    assert repo.primera_fecha() is None
    for empleado, fecha in ((juan, "2026-10-05"), (ana, "2026-09-03"), (juan, "2026-11-02")):
        repo.upsert(
            Asistencia(
                id=None,
                empleado_id=empleado,
                fecha=fecha,
                estado=EstadoAsistencia.PRESENTE.value,
                turno_id_aplicado=turno,
                hora_entrada_real="08:00:00",
                hora_salida_real="17:00:00",
                consolidada_en="2026-11-02T22:00:00+00:00",
            )
        )
    assert repo.primera_fecha() == "2026-09-03"
    assert repo.primera_fecha(juan) == "2026-10-05"


def test_periodo_todo_va_del_primer_dia_con_datos_a_hoy() -> None:
    hoy = date(2026, 11, 2)
    assert periodos.rango_todo(date(2026, 9, 3), hoy) == (date(2026, 9, 3), hoy)
    assert periodos.rango_todo(None, hoy) == (hoy, hoy)
