"""Tests del balance de tiempo: atraso contra tiempo extra.

Regla del negocio: se cuenta desde la hora EXACTA del turno. Entrar 07:05
con entrada 07:00 son 5 min de atraso; salir 17:05 con salida 17:00 son
5 min de extra; los dos se compensan (balance 0).
"""

from __future__ import annotations

from typing import Optional

from core.models.asistencia import Asistencia, EstadoAsistencia
from core.services.asistencia_exporter import (
    EmpleadoReporte,
    ExtrasReporte,
    MarcaReporte,
    calcular_resumen,
    tiempos_por_dia,
)
from core.services.asistencia_service import AsistenciaVista
from core.services.balance_tiempo import (
    HorarioDia,
    TiempoDia,
    sumar_dias,
    texto_duracion,
    tiempo_del_dia,
)

E = EstadoAsistencia
_7_A_17 = HorarioDia("07:00", "17:00")


def _dia(
    estado: str = E.PRESENTE.value,
    entrada: Optional[str] = "07:00:00",
    salida: Optional[str] = "17:00:00",
    tarde: int = 0,
    temprana: int = 0,
    fecha: str = "2026-11-02",
) -> Asistencia:
    return Asistencia(
        id=None,
        empleado_id=1,
        fecha=fecha,
        estado=estado,
        turno_id_aplicado=1,
        hora_entrada_real=entrada,
        hora_salida_real=salida,
        minutos_tarde=tarde,
        minutos_salida_temprana=temprana,
    )


# ── Un día ────────────────────────────────────────────────────────────────────


def test_cinco_minutos_tarde_y_cinco_de_extra_se_compensan() -> None:
    t = tiempo_del_dia(_dia(entrada="07:05:00", salida="17:05:00", tarde=5), _7_A_17)
    assert (t.tarde, t.extra, t.salida_temprana) == (5, 5, 0)
    assert t.balance == 0
    assert t.trabajado == 600
    assert t.programado == 600


def test_quedarse_mas_tiempo_suma_extra_a_favor() -> None:
    t = tiempo_del_dia(_dia(salida="18:30:40"), _7_A_17)
    assert t.extra == 90  # los segundos se truncan, igual que la consolidación
    assert t.balance == 90


def test_salir_antes_resta() -> None:
    t = tiempo_del_dia(_dia(salida="16:40:00", temprana=20), _7_A_17)
    assert (t.extra, t.salida_temprana, t.balance) == (0, 20, -20)


def test_salida_asumida_no_suma_extra() -> None:
    # Entró 07:12, no marcó salida: la consolidación tomó la salida del turno.
    t = tiempo_del_dia(_dia(entrada="07:12:00", salida="17:00:00", tarde=12), _7_A_17)
    assert (t.tarde, t.extra, t.balance) == (12, 0, -12)


def test_sin_marca_de_salida_el_atraso_ya_cuenta() -> None:
    t = tiempo_del_dia(_dia(E.INCOMPLETO.value, entrada="07:20:00", salida=None), _7_A_17)
    assert (t.tarde, t.extra, t.programado, t.trabajado) == (20, 0, 0, None)
    assert t.balance == -20


def test_turno_nocturno_cuenta_el_extra_despues_de_medianoche() -> None:
    noche = HorarioDia("22:00", "06:00", cruza_medianoche=True)
    t = tiempo_del_dia(_dia(entrada="21:55:00", salida="06:45:00"), noche)
    assert (t.extra, t.trabajado, t.programado) == (45, 530, 480)


def test_falta_programa_la_jornada_sin_trabajarla() -> None:
    t = tiempo_del_dia(_dia(E.AUSENTE.value, entrada=None, salida=None), _7_A_17)
    assert (t.programado, t.trabajado, t.balance) == (600, 0, 0)


def test_vacaciones_y_permisos_no_cuentan() -> None:
    for estado in (E.VACACIONES.value, E.PERMISO.value, E.DIA_LIBRE.value):
        assert tiempo_del_dia(_dia(estado, None, None), _7_A_17) == TiempoDia()


def test_trabajo_en_dia_libre_es_todo_extra() -> None:
    domingo = _dia(E.SIN_TURNO.value, None, None, fecha="2026-11-01")
    t = tiempo_del_dia(domingo, None, ["08:00:00", "12:30:59"])
    assert (t.en_descanso, t.extra_total, t.balance) == (270, 270, 270)


def test_una_sola_marca_en_dia_libre_no_se_puede_medir() -> None:
    domingo = _dia(E.SIN_TURNO.value, None, None, fecha="2026-11-01")
    assert tiempo_del_dia(domingo, None, ["08:00:00"]).en_descanso == 0


def test_la_salida_nocturna_del_dia_anterior_no_es_trabajo_en_dia_libre() -> None:
    domingo = _dia(E.SIN_TURNO.value, None, None, fecha="2026-11-01")
    t = tiempo_del_dia(domingo, None, ["06:02:00", "09:00:00"], salida_del_dia_anterior="06:02:00")
    assert t.en_descanso == 0


# ── Varios días ───────────────────────────────────────────────────────────────


def test_suma_del_mes_compensa_atrasos_con_extra() -> None:
    dias = [
        (tiempo_del_dia(_dia(entrada="07:05:00", salida="17:00:00", tarde=5), _7_A_17), "07:05"),
        (tiempo_del_dia(_dia(entrada="07:10:00", salida="17:30:00", tarde=10), _7_A_17), "07:10"),
        (
            tiempo_del_dia(_dia(entrada="06:55:00", salida="16:50:00", temprana=10), _7_A_17),
            "06:55",
        ),
        (tiempo_del_dia(_dia(E.AUSENTE.value, None, None), _7_A_17), None),
    ]
    t = sumar_dias(dias)
    assert (t.tarde, t.extra, t.salida_temprana) == (15, 30, 10)
    assert t.balance == 5
    assert (t.dias_con_atraso, t.dias_con_extra, t.dias_con_salida_temprana) == (2, 1, 1)
    assert t.programado == 4 * 600  # tres días trabajados + la falta
    assert t.trabajado == 595 + 620 + 595
    assert t.entrada_promedio == "07:03"
    assert t.cumplimiento is not None and round(t.cumplimiento, 3) == round(1810 / 2400, 3)


def test_texto_de_duracion_con_signo() -> None:
    assert texto_duracion(125) == "2:05"
    assert texto_duracion(125, con_signo=True) == "+2:05"
    assert texto_duracion(-45, con_signo=True) == "−0:45"
    assert texto_duracion(0, con_signo=True) == "0:00"


# ── Integrado al resumen del reporte ─────────────────────────────────────────


def _vista(a: Asistencia) -> AsistenciaVista:
    return AsistenciaVista(
        asistencia=a,
        empleado_nombre_completo="Pérez Juan",
        empleado_dni="0801-1990-00001",
        turno_nombre="Administrativo",
    )


def test_resumen_trae_el_balance_cuando_hay_horarios() -> None:
    items = [
        _vista(_dia(entrada="07:05:00", salida="17:05:00", tarde=5, fecha="2026-11-02")),
        _vista(_dia(entrada="07:00:00", salida="18:00:00", fecha="2026-11-03")),
        _vista(_dia(E.SIN_TURNO.value, None, None, fecha="2026-11-01")),
    ]
    extras = ExtrasReporte(
        empleados={1: EmpleadoReporte("Pérez Juan", None, 7)},
        marcas=[
            MarcaReporte("2026-11-01T09:00:00", 7, 1, "Reloj"),
            MarcaReporte("2026-11-01T11:00:00", 7, 1, "Reloj"),
        ],
        horarios={(1, 0): _7_A_17, (1, 1): _7_A_17},  # lunes y martes
    )
    (r,) = calcular_resumen(items, extras)
    assert r.tiempo is not None
    assert (r.tiempo.tarde, r.tiempo.extra, r.tiempo.en_descanso) == (5, 65, 120)
    assert r.tiempo.balance == 180
    assert tiempos_por_dia(items, extras)[(1, "2026-11-03")].extra == 60


def test_resumen_sin_contexto_no_inventa_balance() -> None:
    (r,) = calcular_resumen([_vista(_dia())])
    assert r.tiempo is None
