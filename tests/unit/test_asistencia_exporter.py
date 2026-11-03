"""Tests de ``calcular_resumen`` (función pura del módulo asistencia_exporter).

Cubre la lógica de agrupamiento por empleado:
    - Conteo correcto por estado.
    - ``TARDE_Y_SALIDA_TEMPRANA`` cuenta para AMBAS columnas
      (``dias_tarde`` + ``dias_salida_temprana``).
    - Suma correcta de ``minutos_tarde`` y ``minutos_salida_temprana``.
    - Orden alfabético por nombre.
    - Lista vacía → ``[]``.
"""

from __future__ import annotations

from typing import Optional

from core.models.asistencia import Asistencia, EstadoAsistencia
from core.services.asistencia_exporter import calcular_resumen
from core.services.asistencia_service import AsistenciaVista


def _vista(
    emp_id: int,
    nombre: str,
    estado: str,
    minutos_tarde: int = 0,
    minutos_st: int = 0,
    fecha: str = "2026-04-15",
    dni: Optional[str] = "0000-0000-00000",
) -> AsistenciaVista:
    return AsistenciaVista(
        asistencia=Asistencia(
            id=None,
            empleado_id=emp_id,
            fecha=fecha,
            estado=estado,
            turno_id_aplicado=1,
            hora_entrada_real="08:00:00",
            hora_salida_real="17:00:00",
            minutos_tarde=minutos_tarde,
            minutos_salida_temprana=minutos_st,
        ),
        empleado_nombre_completo=nombre,
        empleado_dni=dni,
        turno_nombre="Admin",
    )


def test_resumen_lista_vacia_devuelve_vacio() -> None:
    assert calcular_resumen([]) == []


def test_resumen_agrupa_por_empleado() -> None:
    items = [
        _vista(1, "Pérez Juan", EstadoAsistencia.PRESENTE.value),
        _vista(1, "Pérez Juan", EstadoAsistencia.PRESENTE.value, fecha="2026-04-16"),
        _vista(2, "Alvarado Carlos", EstadoAsistencia.AUSENTE.value),
    ]
    resumen = calcular_resumen(items)
    assert len(resumen) == 2
    por_id = {r.empleado_id: r for r in resumen}
    assert por_id[1].dias_presente == 2
    assert por_id[2].dias_ausente == 1


def test_resumen_tarde_y_salida_temprana_cuenta_doble() -> None:
    """Un día con TARDE_Y_SALIDA_TEMPRANA cuenta en ambas columnas."""
    items = [
        _vista(
            1,
            "X",
            EstadoAsistencia.TARDE_Y_SALIDA_TEMPRANA.value,
            minutos_tarde=15,
            minutos_st=20,
        ),
    ]
    resumen = calcular_resumen(items)
    assert len(resumen) == 1
    r = resumen[0]
    assert r.dias_tarde == 1
    assert r.dias_salida_temprana == 1
    # Pero solo es UN día — NO duplicamos el total de días en otros conteos.
    assert r.dias_presente == 0
    assert r.dias_ausente == 0
    # Minutos se acumulan tal cual.
    assert r.total_minutos_tarde == 15
    assert r.total_minutos_salida_temprana == 20


def test_resumen_acumula_minutos() -> None:
    items = [
        _vista(1, "X", EstadoAsistencia.TARDE.value, minutos_tarde=10),
        _vista(
            1,
            "X",
            EstadoAsistencia.TARDE.value,
            minutos_tarde=20,
            fecha="2026-04-16",
        ),
        _vista(
            1,
            "X",
            EstadoAsistencia.SALIDA_TEMPRANA.value,
            minutos_st=5,
            fecha="2026-04-17",
        ),
    ]
    r = calcular_resumen(items)[0]
    assert r.dias_tarde == 2
    assert r.dias_salida_temprana == 1
    assert r.total_minutos_tarde == 30
    assert r.total_minutos_salida_temprana == 5


def test_resumen_orden_alfabetico_por_nombre() -> None:
    """Los empleados salen ordenados por nombre case-insensitive."""
    items = [
        _vista(1, "Zapata Ana", EstadoAsistencia.PRESENTE.value),
        _vista(2, "alvarado Carlos", EstadoAsistencia.PRESENTE.value),
        _vista(3, "Mora Diego", EstadoAsistencia.PRESENTE.value),
    ]
    nombres = [r.empleado_nombre for r in calcular_resumen(items)]
    assert nombres == ["alvarado Carlos", "Mora Diego", "Zapata Ana"]


def test_resumen_cuenta_todos_los_estados_excluyentes() -> None:
    items = [
        _vista(1, "X", EstadoAsistencia.PRESENTE.value),
        _vista(1, "X", EstadoAsistencia.AUSENTE.value, fecha="2026-04-16"),
        _vista(1, "X", EstadoAsistencia.FERIADO.value, fecha="2026-04-17"),
        _vista(1, "X", EstadoAsistencia.SIN_TURNO.value, fecha="2026-04-18"),
        _vista(1, "X", EstadoAsistencia.INCOMPLETO.value, fecha="2026-04-19"),
    ]
    r = calcular_resumen(items)[0]
    assert r.dias_presente == 1
    assert r.dias_ausente == 1
    assert r.dias_feriado == 1
    assert r.dias_sin_turno == 1
    assert r.dias_incompleto == 1
    assert r.dias_tarde == 0
    assert r.dias_salida_temprana == 0


def test_resumen_dni_y_nombre_se_toman_de_la_primera_fila_del_empleado() -> None:
    items = [
        _vista(1, "Pérez Juan", EstadoAsistencia.PRESENTE.value, dni="0801-1990-12345"),
        _vista(
            1,
            "Pérez Juan",
            EstadoAsistencia.PRESENTE.value,
            dni="0801-1990-12345",
            fecha="2026-04-16",
        ),
    ]
    r = calcular_resumen(items)[0]
    assert r.empleado_nombre == "Pérez Juan"
    assert r.empleado_dni == "0801-1990-12345"
