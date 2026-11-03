"""Turnos con horario distinto por día (lun-vie 09-17 y sábado 09-12 en UN turno).

Cubre el modelo (``dias_totales``, ``horario_para``), la persistencia en
``turno_horarios``, las validaciones del servicio, la consolidación con el
horario del día y el choque con otros turnos de los empleados.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

import config
from bin import seed_demo
from core.models import permissions as perms
from core.models.turno import (
    DIAS_LABORALES,
    DOMINGO,
    LUNES,
    SABADO,
    HorarioTurno,
    Turno,
)
from core.services.errors import (
    HorariosSolapadosError,
    InvalidDescansoError,
    TurnoBitmaskSolapadoError,
    TurnoConflictoEmpleadosError,
)
from core.services.permission_service import PermissionService
from core.services.session import Session
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner
from ui import formato
from ui.controllers.turnos_controller import TurnosController

_SABADO_TARDE = HorarioTurno(SABADO, "09:00", "12:00")


# ── Fixtures ──────────────────────────────────────────────────────────────────


@dataclass
class _Entorno:
    db: Database
    s: seed_demo._Servicios
    actor: int
    dep_id: int
    cargo_id: int


@pytest.fixture
def entorno(tmp_path: Path) -> _Entorno:
    db = Database(tmp_path / "t.db")
    MigrationsRunner(db, config.MIGRATIONS_DIR).run()
    s = seed_demo._Servicios(db)
    actor = seed_demo._crear_usuarios(s)
    dep = s.catalogo.create_departamento("Administración", actor)
    assert dep.id is not None
    cargo = s.catalogo.create_cargo("Asistente", actor, departamento_id=dep.id)
    assert cargo.id is not None
    return _Entorno(db, s, actor, dep.id, cargo.id)


def _turno(e: _Entorno, nombre: str = "Administrativo", con_sabado: bool = True) -> Turno:
    return e.s.turnos.create_turno(
        nombre=nombre,
        hora_entrada="09:00",
        hora_salida="17:00",
        minutos_descanso=60,
        dias_semana=DIAS_LABORALES,
        actor_user_id=e.actor,
        horarios_extra=[_SABADO_TARDE] if con_sabado else [],
    )


def _empleado(e: _Entorno, n: int = 1, zkteco_id: int = 7) -> int:
    emp = e.s.empleados.create_empleado(
        dni=f"0801-1990-{n:05d}",
        nombres="Ana",
        apellidos=f"Lara {n}",
        departamento_id=e.dep_id,
        cargo_id=e.cargo_id,
        fecha_ingreso="2026-01-05",
        actor_user_id=e.actor,
        zkteco_id=zkteco_id,
    )
    assert emp.id is not None
    return emp.id


def _marcar(e: _Entorno, zkteco_id: int, *timestamps: str) -> None:
    with e.db.transaction() as conn:
        dispositivo = conn.execute("SELECT id FROM dispositivos").fetchone()
        if dispositivo is None:
            conn.execute(
                "INSERT INTO dispositivos (nombre, ip, puerto) VALUES ('R', '10.0.0.1', 4370)"
            )
            dispositivo = conn.execute("SELECT id FROM dispositivos").fetchone()
        cur = conn.execute(
            "INSERT INTO sincronizaciones (dispositivo_id, inicio, rango_desde, rango_hasta,"
            " estado) VALUES (?, '2026-01-01T00:00:00', '2026-01-01', '2026-01-31', 'OK')",
            (dispositivo["id"],),
        )
        for ts in timestamps:
            conn.execute(
                "INSERT INTO registros_raw (dispositivo_id, sincronizacion_id, zkteco_user_id,"
                " timestamp) VALUES (?, ?, ?, ?)",
                (dispositivo["id"], cur.lastrowid, zkteco_id, ts),
            )


def _estado(e: _Entorno, empleado_id: int, fecha: str) -> tuple[str, str | None]:
    with e.db.transaction() as conn:
        row = conn.execute(
            "SELECT estado, hora_salida_real FROM asistencias WHERE empleado_id = ? AND fecha = ?",
            (empleado_id, fecha),
        ).fetchone()
    assert row is not None, f"sin asistencia el {fecha}"
    return row["estado"], row["hora_salida_real"]


# ── Modelo ────────────────────────────────────────────────────────────────────


def test_dias_totales_une_todos_los_horarios() -> None:
    t = Turno(None, "A", "09:00", "17:00", 60, DIAS_LABORALES, horarios_extra=[_SABADO_TARDE])
    assert t.dias_totales == DIAS_LABORALES | SABADO
    assert t.aplica_en(5)  # sábado
    assert not t.aplica_en(6)  # domingo


def test_horario_para_devuelve_las_horas_de_ese_dia() -> None:
    t = Turno(None, "A", "09:00", "17:00", 60, DIAS_LABORALES, horarios_extra=[_SABADO_TARDE])
    lunes = t.horario_para(0)
    sabado = t.horario_para(5)
    assert lunes is t
    assert sabado is not None
    assert (sabado.hora_entrada, sabado.hora_salida, sabado.minutos_descanso) == (
        "09:00",
        "12:00",
        0,
    )
    assert sabado.dias_semana == SABADO and sabado.horarios_extra == []
    assert t.horario_para(6) is None


def test_bloques_lista_principal_y_alternos() -> None:
    t = Turno(None, "A", "09:00", "17:00", 60, DIAS_LABORALES, horarios_extra=[_SABADO_TARDE])
    assert [b.dias_semana for b in t.bloques()] == [DIAS_LABORALES, SABADO]


# ── Persistencia y servicio ───────────────────────────────────────────────────


def test_crear_turno_guarda_y_lee_el_horario_del_sabado(entorno: _Entorno) -> None:
    creado = _turno(entorno)
    assert creado.id is not None
    leido = entorno.s.turnos.get_turno(creado.id)
    assert leido.horarios_extra == [_SABADO_TARDE]
    assert [t.horarios_extra for t in entorno.s.turnos.list_turnos()] == [[_SABADO_TARDE]]


def test_update_con_lista_reemplaza_y_con_none_conserva(entorno: _Entorno) -> None:
    t = _turno(entorno)
    assert t.id is not None
    kwargs = dict(
        turno_id=t.id,
        nombre="Administrativo",
        hora_entrada="08:00",
        hora_salida="17:00",
        minutos_descanso=60,
        dias_semana=DIAS_LABORALES,
        actor_user_id=entorno.actor,
    )
    entorno.s.turnos.update_turno(**kwargs)  # type: ignore[arg-type]
    assert entorno.s.turnos.get_turno(t.id).horarios_extra == [_SABADO_TARDE]

    domingo = HorarioTurno(DOMINGO, "08:00", "11:00")
    entorno.s.turnos.update_turno(**kwargs, horarios_extra=[domingo])  # type: ignore[arg-type]
    assert entorno.s.turnos.get_turno(t.id).horarios_extra == [domingo]

    entorno.s.turnos.update_turno(**kwargs, horarios_extra=[])  # type: ignore[arg-type]
    assert entorno.s.turnos.get_turno(t.id).horarios_extra == []


def test_un_dia_no_puede_estar_en_dos_horarios(entorno: _Entorno) -> None:
    with pytest.raises(HorariosSolapadosError, match="lunes"):
        entorno.s.turnos.create_turno(
            nombre="Malo",
            hora_entrada="09:00",
            hora_salida="17:00",
            minutos_descanso=0,
            dias_semana=DIAS_LABORALES,
            actor_user_id=entorno.actor,
            horarios_extra=[HorarioTurno(LUNES | SABADO, "09:00", "12:00")],
        )


def test_el_horario_alterno_se_valida_como_el_principal(entorno: _Entorno) -> None:
    with pytest.raises(InvalidDescansoError):
        entorno.s.turnos.create_turno(
            nombre="Malo",
            hora_entrada="09:00",
            hora_salida="17:00",
            minutos_descanso=0,
            dias_semana=DIAS_LABORALES,
            actor_user_id=entorno.actor,
            horarios_extra=[HorarioTurno(SABADO, "09:00", "10:00", minutos_descanso=90)],
        )


def test_cruza_medianoche_se_recalcula_en_el_horario_alterno(entorno: _Entorno) -> None:
    t = entorno.s.turnos.create_turno(
        nombre="Vigilancia",
        hora_entrada="08:00",
        hora_salida="16:00",
        minutos_descanso=0,
        dias_semana=DIAS_LABORALES,
        actor_user_id=entorno.actor,
        horarios_extra=[HorarioTurno(SABADO, "22:00", "06:00", cruza_medianoche=False)],
    )
    assert t.horarios_extra[0].cruza_medianoche is True


def test_no_se_asigna_otro_turno_el_sabado(entorno: _Entorno) -> None:
    emp = _empleado(entorno)
    principal = _turno(entorno)
    sabado = entorno.s.turnos.create_turno(
        "Solo sábado", "08:00", "12:00", 0, SABADO, entorno.actor
    )
    assert principal.id is not None and sabado.id is not None
    entorno.s.empleados.asignar_turno(emp, principal.id, "2026-01-05", entorno.actor)
    with pytest.raises(TurnoBitmaskSolapadoError):
        entorno.s.empleados.asignar_turno(emp, sabado.id, "2026-01-05", entorno.actor)


# ── Consolidación ─────────────────────────────────────────────────────────────


def test_el_sabado_se_compara_contra_su_propio_horario(entorno: _Entorno) -> None:
    emp = _empleado(entorno)
    t = _turno(entorno)
    assert t.id is not None
    entorno.s.empleados.asignar_turno(emp, t.id, "2026-01-05", entorno.actor)
    # Sábado 10/01: sale a las 12:00. Con el horario de lunes a viernes
    # (hasta las 17:00) sería "salida temprana"; con el del sábado, presente.
    _marcar(entorno, 7, "2026-01-10T08:55:00", "2026-01-10T12:00:00")
    # Lunes 12/01: horario normal.
    _marcar(entorno, 7, "2026-01-12T08:57:00", "2026-01-12T17:00:00")

    entorno.s.consolidacion.consolidar_rango("2026-01-10", "2026-01-12", empleado_id=emp)

    assert _estado(entorno, emp, "2026-01-10") == ("PRESENTE", "12:00:00")
    assert _estado(entorno, emp, "2026-01-11")[0] == "SIN_TURNO"  # domingo
    assert _estado(entorno, emp, "2026-01-12") == ("PRESENTE", "17:00:00")


# ── Choque al agregar días a un turno ya asignado ─────────────────────────────


def _sesion() -> Session:
    return Session(
        user_id=1,
        username="admin",
        role_id=1,
        role_code=perms.ROLE_SUPERADMIN,
        permissions=frozenset({perms.MANAGE_EMPLOYEES}),
    )


def test_agregar_sabado_avisa_y_puede_quitar_el_turno_que_choca(entorno: _Entorno) -> None:
    emp = _empleado(entorno)
    lv = _turno(entorno, "Administrativo L-V", con_sabado=False)
    sab = entorno.s.turnos.create_turno("Admin Sábado", "09:00", "12:00", 0, SABADO, entorno.actor)
    assert lv.id is not None and sab.id is not None
    entorno.s.empleados.asignar_turno(emp, lv.id, "2026-01-05", entorno.actor)
    entorno.s.empleados.asignar_turno(emp, sab.id, "2026-01-05", entorno.actor)
    ctrl = TurnosController(
        session=_sesion(),
        permission_service=PermissionService(),
        turno_service=entorno.s.turnos,
        empleado_service=entorno.s.empleados,
    )
    datos = dict(
        turno_id=lv.id,
        nombre="Administrativo",
        hora_entrada="09:00",
        hora_salida="17:00",
        minutos_descanso=60,
        dias_semana=DIAS_LABORALES,
        horarios_extra=[_SABADO_TARDE],
    )

    with pytest.raises(TurnoConflictoEmpleadosError) as exc:
        ctrl.update_turno(**datos)  # type: ignore[arg-type]
    assert exc.value.turnos == ["Admin Sábado"] and exc.value.dias == ["sábado"]
    assert "Ana Lara 1" in str(exc.value)
    # No se guardó nada.
    assert entorno.s.turnos.get_turno(lv.id).horarios_extra == []

    cerradas = ctrl.update_turno(**datos, quitar_conflictos=True)  # type: ignore[arg-type]

    assert cerradas == 1
    assert entorno.s.turnos.get_turno(lv.id).horarios_extra == [_SABADO_TARDE]
    vigentes = entorno.s.empleados.list_turnos_vigentes(emp)
    assert [v.turno_id for v in vigentes] == [lv.id]


def test_sin_choque_guarda_directo(entorno: _Entorno) -> None:
    emp = _empleado(entorno)
    t = _turno(entorno, con_sabado=False)
    assert t.id is not None
    entorno.s.empleados.asignar_turno(emp, t.id, "2026-01-05", entorno.actor)
    ctrl = TurnosController(
        session=_sesion(),
        permission_service=PermissionService(),
        turno_service=entorno.s.turnos,
        empleado_service=entorno.s.empleados,
    )
    assert (
        ctrl.update_turno(
            turno_id=t.id,
            nombre="Administrativo",
            hora_entrada="09:00",
            hora_salida="17:00",
            minutos_descanso=60,
            dias_semana=DIAS_LABORALES,
            horarios_extra=[_SABADO_TARDE],
        )
        == 0
    )


# ── Formato para pantalla ─────────────────────────────────────────────────────


def test_textos_de_horario_y_horas_por_semana() -> None:
    t = Turno(None, "A", "09:00", "17:00", 60, DIAS_LABORALES, horarios_extra=[_SABADO_TARDE])
    assert formato.horario_texto(t) == "09:00–17:00  ·  Sáb 09:00–12:00"
    assert formato.horario_texto(t, con_dias=True) == "Lun a Vie 09:00–17:00  ·  Sáb 09:00–12:00"
    # 5 × 7 h + 3 h = 38 h
    assert formato.minutos_por_semana(t.bloques()) == 38 * 60
    assert formato.dias_corto(SABADO) == "Sáb"
    assert formato.dias_corto(DIAS_LABORALES) == "Lun a Vie"
