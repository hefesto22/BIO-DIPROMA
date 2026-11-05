"""Hora de almuerzo en los turnos (de 12:00 a 13:00) y su efecto en las horas.

Cubre la validación (completa, al derecho y dentro del horario), que los
minutos de descanso salen del almuerzo, la persistencia, la migración 014
(turnos viejos con minutos pasan a tener hora; ADMIN con acceso total), el
cálculo de horas programadas y trabajadas sin el almuerzo y lo que muestra
la pantalla de Turnos.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

import pytest

import config
from bin import seed_demo
from core.models.asistencia import Asistencia, EstadoAsistencia
from core.models.turno import DIAS_LABORALES, SABADO, HorarioTurno
from core.repositories.rol_repository_sqlite import RolRepositorySQLite
from core.repositories.turno_repository_sqlite import TurnoRepositorySQLite
from core.services.balance_tiempo import HorarioDia, tiempo_del_dia
from core.services.errors import InvalidAlmuerzoError
from core.services.validators import minutos_almuerzo
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner
from ui import formato
from ui.components.turno_form_dialog import almuerzo_inicial

E = EstadoAsistencia


@pytest.fixture
def servicios(tmp_path: Path) -> seed_demo._Servicios:
    db = Database(tmp_path / "t.db")
    MigrationsRunner(db, config.MIGRATIONS_DIR).run()
    return seed_demo._Servicios(db)


def _crear(
    s: seed_demo._Servicios,
    inicio: Optional[str] = "12:00",
    fin: Optional[str] = "13:00",
    entrada: str = "07:00",
    salida: str = "17:00",
    extras: tuple[HorarioTurno, ...] = (),
    minutos: int = 0,
) -> int:
    turno = s.turnos.create_turno(
        nombre="Operativo",
        hora_entrada=entrada,
        hora_salida=salida,
        minutos_descanso=minutos,
        dias_semana=DIAS_LABORALES,
        actor_user_id=None,
        horarios_extra=list(extras),
        almuerzo_inicio=inicio,
        almuerzo_fin=fin,
    )
    assert turno.id is not None
    return turno.id


# ── Validación ────────────────────────────────────────────────────────────────


def test_minutos_del_almuerzo() -> None:
    assert minutos_almuerzo("07:00", "17:00", "12:00", "13:00") == 60
    assert minutos_almuerzo("07:00", "17:00", "12:30", "13:15") == 45
    assert minutos_almuerzo("07:00", "17:00", None, "") == 0
    # Turno de noche: almuerzo pasada la medianoche.
    assert minutos_almuerzo("22:00", "06:00", "02:00", "02:30") == 30


@pytest.mark.parametrize(
    ("inicio", "fin", "texto"),
    [
        ("12:00", "", "inicio y la de fin"),
        ("", "13:00", "inicio y la de fin"),
        ("13:00", "12:00", "terminar después"),
        ("12:00", "12:00", "terminar después"),
        ("06:00", "06:30", "dentro del horario"),
        ("16:30", "17:30", "dentro del horario"),
    ],
)
def test_almuerzo_invalido(inicio: str, fin: str, texto: str) -> None:
    with pytest.raises(InvalidAlmuerzoError, match=texto):
        minutos_almuerzo("07:00", "17:00", inicio, fin)


# ── Servicio y persistencia ───────────────────────────────────────────────────


def test_el_descanso_sale_del_almuerzo_y_se_guarda(servicios: seed_demo._Servicios) -> None:
    turno_id = _crear(servicios, minutos=999)  # los minutos mandados se ignoran
    t = servicios.turnos.get_turno(turno_id)
    assert (t.almuerzo_inicio, t.almuerzo_fin, t.minutos_descanso) == ("12:00", "13:00", 60)
    assert t.bloques()[0].almuerzo_texto == "12:00–13:00"


def test_horario_del_sabado_sin_almuerzo(servicios: seed_demo._Servicios) -> None:
    sabado = HorarioTurno(SABADO, "07:00", "12:00", 0)
    con_almuerzo = HorarioTurno(SABADO, "07:00", "15:00", 0, False, " 11:00 ", "11:30")
    t = servicios.turnos.get_turno(_crear(servicios, extras=(sabado,)))
    assert t.horarios_extra[0].almuerzo_texto is None
    assert t.horarios_extra[0].minutos_descanso == 0

    assert t.id is not None
    servicios.turnos.update_turno(
        t.id,
        "Operativo",
        "07:00",
        "17:00",
        0,
        DIAS_LABORALES,
        None,
        [con_almuerzo],
        "12:00",
        "13:00",
    )
    sab = servicios.turnos.get_turno(t.id).horarios_extra[0]
    assert (sab.almuerzo_inicio, sab.almuerzo_fin, sab.minutos_descanso) == ("11:00", "11:30", 30)
    # El día sábado "visto" por la consolidación trae su propio almuerzo.
    proyectado = servicios.turnos.get_turno(t.id).horario_para(5)
    assert proyectado is not None and proyectado.almuerzo_inicio == "11:00"


def test_quitar_el_almuerzo(servicios: seed_demo._Servicios) -> None:
    turno_id = _crear(servicios)
    servicios.turnos.update_turno(
        turno_id, "Operativo", "07:00", "17:00", 0, DIAS_LABORALES, None, None, "", " "
    )
    t = servicios.turnos.get_turno(turno_id)
    assert (t.almuerzo_inicio, t.almuerzo_fin, t.minutos_descanso) == (None, None, 0)


def test_almuerzo_fuera_del_horario_no_se_guarda(servicios: seed_demo._Servicios) -> None:
    with pytest.raises(InvalidAlmuerzoError):
        _crear(servicios, inicio="18:00", fin="19:00")
    with pytest.raises(InvalidAlmuerzoError):
        _crear(
            servicios, extras=(HorarioTurno(SABADO, "07:00", "12:00", 0, False, "12:00", "13:00"),)
        )
    assert servicios.turnos.list_turnos(solo_activos=False) == []


# ── Migración 014 ─────────────────────────────────────────────────────────────


def test_migracion_pone_hora_a_los_descansos_viejos_y_admin_total(tmp_path: Path) -> None:
    viejas = tmp_path / "migraciones"
    viejas.mkdir()
    for sql in sorted(config.MIGRATIONS_DIR.glob("*.sql")):
        if sql.name < "014":
            shutil.copy(sql, viejas / sql.name)
    db = Database(tmp_path / "t.db")
    MigrationsRunner(db, viejas).run()
    with db.transaction() as conn:
        filas = [
            ("Administrativo", "09:00", "17:00", 60, 0),
            ("Noche", "22:00", "06:00", 60, 1),
            ("Mañana", "06:00", "11:00", 30, 0),
            ("Corrido", "07:00", "16:00", 0, 0),
        ]
        for nombre, entrada, salida, descanso, cruza in filas:
            conn.execute(
                "INSERT INTO turnos (nombre, hora_entrada, hora_salida, minutos_descanso, "
                "dias_semana, cruza_medianoche) VALUES (?, ?, ?, ?, ?, ?)",
                (nombre, entrada, salida, descanso, DIAS_LABORALES, cruza),
            )
        turno_id = conn.execute("SELECT id FROM turnos WHERE nombre = 'Administrativo'").fetchone()
        conn.execute(
            "INSERT INTO turno_horarios (turno_id, dias_semana, hora_entrada, hora_salida, "
            "minutos_descanso) VALUES (?, ?, '08:00', '14:00', 45)",
            (turno_id[0], SABADO),
        )

    shutil.copy(config.MIGRATIONS_DIR / "014_almuerzo_y_admin_total.sql", viejas)
    MigrationsRunner(db, viejas).run()

    turnos = {t.nombre: t for t in TurnoRepositorySQLite(db).list_all()}
    assert turnos["Administrativo"].bloques()[0].almuerzo_texto == "12:00–13:00"
    assert turnos["Administrativo"].horarios_extra[0].almuerzo_texto == "12:00–12:45"
    assert turnos["Noche"].almuerzo_inicio is None  # de noche: queda con sus minutos
    assert turnos["Noche"].minutos_descanso == 60
    assert turnos["Mañana"].almuerzo_inicio is None  # termina antes de las 12:30
    assert turnos["Corrido"].almuerzo_inicio is None

    roles = RolRepositorySQLite(db)
    admin, superadmin = roles.get_by_code("ADMIN"), roles.get_by_code("SUPERADMIN")
    assert admin is not None and superadmin is not None
    assert admin.permissions == superadmin.permissions
    assert "manage_roles" in admin.permissions


# ── Horas sin el almuerzo ─────────────────────────────────────────────────────

_CON_ALMUERZO = HorarioDia("07:00", "17:00", almuerzo_inicio="12:00", almuerzo_fin="13:00")


def _dia(entrada: str, salida: Optional[str], estado: str = E.PRESENTE.value) -> Asistencia:
    return Asistencia(
        id=None,
        empleado_id=1,
        fecha="2026-11-02",
        estado=estado,
        turno_id_aplicado=1,
        hora_entrada_real=entrada,
        hora_salida_real=salida,
    )


def test_dia_completo_no_cuenta_el_almuerzo() -> None:
    t = tiempo_del_dia(_dia("07:00:00", "17:00:00"), _CON_ALMUERZO)
    assert (t.programado, t.trabajado, t.balance) == (540, 540, 0)


def test_solo_resta_la_parte_del_almuerzo_que_estuvo() -> None:
    se_fue_antes = tiempo_del_dia(_dia("07:00:00", "11:00:00"), _CON_ALMUERZO)
    assert se_fue_antes.trabajado == 240
    a_media_hora = tiempo_del_dia(_dia("07:00:00", "12:30:00"), _CON_ALMUERZO)
    assert a_media_hora.trabajado == 300


def test_falta_cuenta_la_jornada_sin_almuerzo() -> None:
    t = tiempo_del_dia(_dia("", None, E.AUSENTE.value), _CON_ALMUERZO)
    assert (t.programado, t.trabajado) == (540, 0)


def test_turno_de_noche_con_almuerzo() -> None:
    noche = HorarioDia("22:00", "06:00", True, "02:00", "03:00")
    t = tiempo_del_dia(_dia("22:00:00", "06:00:00"), noche)
    assert (t.programado, t.trabajado) == (420, 420)
    tarde = tiempo_del_dia(_dia("00:30:00", "06:00:00"), noche)
    assert tarde.trabajado == 270


def test_sin_hora_de_almuerzo_queda_como_antes() -> None:
    t = tiempo_del_dia(_dia("07:00:00", "17:00:00"), HorarioDia("07:00", "17:00"))
    assert (t.programado, t.trabajado) == (600, 600)


# ── Pantalla ──────────────────────────────────────────────────────────────────


def test_texto_de_la_jornada() -> None:
    assert formato.almuerzo_texto(
        HorarioTurno(1, "07:00", "17:00", 60, False, "12:00", "13:00")
    ) == (" (almuerzo 12:00–13:00)")
    assert formato.almuerzo_texto(HorarioTurno(1, "22:00", "06:00", 30)) == " (descanso 30 min)"
    assert formato.almuerzo_texto(HorarioTurno(1, "07:00", "12:00", 0)) == ""


def test_formulario_sugiere_hora_para_descansos_viejos() -> None:
    assert almuerzo_inicial(HorarioTurno(1, "07:00", "17:00", 60, False, "12:30", "13:30")) == (
        "12:30",
        "13:30",
    )
    assert almuerzo_inicial(HorarioTurno(1, "09:00", "17:00", 60)) == ("12:00", "13:00")
    assert almuerzo_inicial(HorarioTurno(1, "22:00", "06:00", 60)) == ("01:30", "02:30")
    assert almuerzo_inicial(HorarioTurno(1, "07:00", "12:00", 0)) == ("", "")
