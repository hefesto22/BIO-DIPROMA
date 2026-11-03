"""Tests del XlsxAsistenciaExporter (infraestructura openpyxl).

Cubre el archivo .xlsx de cuatro hojas:
    - Resumen, Detalle, Marcas e Información, con el mismo encabezado
      (título, empresa, período, empleado, quién lo generó).
    - Tabla en la fila 6 con filtros y panel congelado.
    - Estados legibles, fechas y horas como valores de Excel.
    - TODAS las marcas del período (y las de IDs sin empleado, resaltadas).
    - "Salida asumida" cuando la salida no coincide con ninguna marca.
    - ``exportar`` devuelve la cantidad de filas de detalle.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from openpyxl import load_workbook

from core.models.asistencia import Asistencia, EstadoAsistencia
from core.services.asistencia_exporter import (
    AusenciaReporte,
    DatosReporteAsistencia,
    EmpleadoReporte,
    ExtrasReporte,
    MarcaReporte,
    ResumenEmpleado,
)
from core.services.asistencia_service import AsistenciaVista
from core.services.balance_tiempo import HorarioDia, TiempoEmpleado
from infrastructure.exporters.xlsx_asistencia_exporter import XlsxAsistenciaExporter


def _vista(
    emp_id: int,
    nombre: str,
    fecha: str,
    estado: str = EstadoAsistencia.PRESENTE.value,
    entrada: Optional[str] = "08:00:00",
    salida: Optional[str] = "17:00:00",
    observaciones: Optional[str] = None,
) -> AsistenciaVista:
    return AsistenciaVista(
        asistencia=Asistencia(
            id=emp_id * 1000,
            empleado_id=emp_id,
            fecha=fecha,
            estado=estado,
            turno_id_aplicado=1,
            hora_entrada_real=entrada,
            hora_salida_real=salida,
            observaciones=observaciones,
        ),
        empleado_nombre_completo=nombre,
        empleado_dni="0801-1990-12345",
        turno_nombre="Admin 8-5",
    )


def _resumen(emp_id: int, nombre: str) -> ResumenEmpleado:
    return ResumenEmpleado(
        empleado_id=emp_id,
        empleado_nombre=nombre,
        empleado_dni="0801-1990-12345",
        dias_presente=10,
        dias_tarde=2,
        dias_salida_temprana=1,
        dias_ausente=0,
        dias_feriado=0,
        dias_sin_turno=4,
        dias_incompleto=0,
        total_minutos_tarde=25,
        total_minutos_salida_temprana=10,
    )


def _datos(
    items: Optional[List[AsistenciaVista]] = None,
    resumen: Optional[List[ResumenEmpleado]] = None,
    empleado_id_filtro: Optional[int] = None,
    empleado_nombre_filtro: Optional[str] = None,
    extras: Optional[ExtrasReporte] = None,
) -> DatosReporteAsistencia:
    if items is None:
        items = [_vista(1, "Pérez Juan", "2026-04-15")]
    if resumen is None:
        resumen = [_resumen(1, "Pérez Juan")]
    return DatosReporteAsistencia(
        items=items,
        resumen_por_empleado=resumen,
        rango_desde="2026-04-01",
        rango_hasta="2026-04-30",
        empleado_id_filtro=empleado_id_filtro,
        empleado_nombre_filtro=empleado_nombre_filtro,
        actor_username="alice",
        timestamp_iso="2026-04-24T16:00:00+00:00",
        generado_local="2026-04-24T10:00:05",
        extras=extras if extras is not None else ExtrasReporte(),
    )


def _extras(marcas: Optional[List[MarcaReporte]] = None) -> ExtrasReporte:
    return ExtrasReporte(
        empleados={
            1: EmpleadoReporte("Pérez Juan", "0801-1990-12345", 7, "Ventas", "Vendedor"),
            2: EmpleadoReporte("López Bob", "0801-1991-00001", 8, "Bodega", "Auxiliar"),
        },
        marcas=(
            marcas
            if marcas is not None
            else [
                MarcaReporte("2026-04-15T08:00:00", 7, 1, "Reloj Principal"),
                MarcaReporte("2026-04-15T12:01:10", 7, 1, "Reloj Principal"),
                MarcaReporte("2026-04-15T17:00:00", 7, 1, "Reloj Principal"),
                MarcaReporte("2026-04-15T09:30:00", 98, None, "Reloj Principal"),
            ]
        ),
        horarios={(1, 2): HorarioDia("08:00", "17:00")},  # 15/04/2026 es miércoles
        notas_ausencia={(2, "2026-04-15"): "Vacaciones: Programadas."},
    )


_TABLA = 6  # fila de títulos de las tablas


def _exportar(tmp_path: Path, datos: DatosReporteAsistencia) -> Any:
    out = tmp_path / "rep.xlsx"
    XlsxAsistenciaExporter(organizacion="DIPROMA", sistema="BIO-DIPROMA").exportar(datos, str(out))
    return load_workbook(str(out))


def _fila(ws: Any, fila: int) -> Dict[str, Any]:
    """``{título de columna: valor}`` de una fila de la tabla."""
    titulos = [c.value for c in ws[_TABLA]]
    return {t: ws.cell(row=fila, column=i).value for i, t in enumerate(titulos, start=1) if t}


# ── Archivo ───────────────────────────────────────────────────────────────────


def test_exportar_devuelve_cantidad_de_filas_de_detalle(tmp_path: Path) -> None:
    items = [
        _vista(1, "Pérez Juan", "2026-04-15"),
        _vista(1, "Pérez Juan", "2026-04-16"),
        _vista(2, "López Bob", "2026-04-15"),
    ]
    out = tmp_path / "rep.xlsx"
    cantidad = XlsxAsistenciaExporter().exportar(_datos(items=items), str(out))
    assert cantidad == 3
    assert out.exists()


def test_exportar_crea_todas_las_hojas(tmp_path: Path) -> None:
    wb = _exportar(tmp_path, _datos())
    assert wb.sheetnames == [
        "Resumen",
        "Estadísticas",
        "Balance de tiempo",
        "Ausencias y permisos",
        "Permisos registrados",
        "Detalle",
        "Marcas",
        "Información",
    ]


def test_cada_hoja_lleva_titulo_empresa_periodo_y_quien_lo_genero(tmp_path: Path) -> None:
    wb = _exportar(tmp_path, _datos(empleado_id_filtro=1, empleado_nombre_filtro="Pérez Juan"))
    for nombre in ("Resumen", "Detalle", "Información"):
        ws = wb[nombre]
        assert ws["A1"].value == "Reporte de asistencia"
        assert ws["A2"].value == "DIPROMA · BIO-DIPROMA"
        assert "del 01/04/2026 al 30/04/2026 (30 días)" in ws["A3"].value
        assert "Empleado: Pérez Juan" in ws["A3"].value
        assert ws["A4"].value == "Generado el 24/04/2026 a las 10:00 por alice"


def test_tablas_con_titulos_filtro_y_panel_congelado(tmp_path: Path) -> None:
    wb = _exportar(tmp_path, _datos())
    for nombre in ("Resumen", "Detalle", "Marcas"):
        ws = wb[nombre]
        assert ws.freeze_panes == "A7"
        assert ws.auto_filter.ref is not None and ws.auto_filter.ref.startswith("A6:")
        assert ws.cell(row=_TABLA, column=1).font.bold is True


# ── Resumen ───────────────────────────────────────────────────────────────────


def test_resumen_una_fila_por_empleado_con_ficha_y_totales(tmp_path: Path) -> None:
    resumen = [_resumen(1, "Pérez Juan"), _resumen(2, "López Bob")]
    items = [_vista(1, "Pérez Juan", "2026-04-15"), _vista(2, "López Bob", "2026-04-15")]
    ws = _exportar(tmp_path, _datos(items=items, resumen=resumen, extras=_extras()))["Resumen"]
    juan = _fila(ws, _TABLA + 1)
    assert juan["Empleado"] == "Pérez Juan"
    assert juan["ID reloj"] == 7
    assert juan["Departamento"] == "Ventas"
    assert juan["Cargo"] == "Vendedor"
    assert juan["A tiempo"] == 10
    assert juan["Tarde"] == 2
    # Duraciones de Excel ([h]:mm): openpyxl las lee como timedelta.
    assert juan["Atraso total (h:mm)"] == timedelta(minutes=25)
    assert juan["Permanencia total (h:mm)"] == timedelta(hours=9)
    assert juan["Marcas"] == 3
    assert _fila(ws, _TABLA + 2)["Empleado"] == "López Bob"
    total = _fila(ws, _TABLA + 3)
    assert total["Empleado"] == "TOTAL (2 empleados)"
    assert total["A tiempo"] == 20
    assert total["Marcas"] == 3


# ── Detalle ───────────────────────────────────────────────────────────────────


def test_detalle_con_fecha_dia_horario_estado_legible_y_marcas(tmp_path: Path) -> None:
    ws = _exportar(tmp_path, _datos(extras=_extras()))["Detalle"]
    fila = _fila(ws, _TABLA + 1)
    assert fila["Fecha"] == datetime(2026, 4, 15)
    assert ws.cell(row=_TABLA + 1, column=1).number_format == "dd/mm/yyyy"
    assert fila["Día"] == "miércoles"
    assert fila["ID reloj"] == 7
    assert fila["Horario"] == "08:00–17:00"
    assert fila["Entrada"] == time(8, 0)
    assert fila["Salida"] == time(17, 0)
    assert fila["Permanencia (h:mm)"] == timedelta(hours=9)
    assert fila["Estado"] == "A tiempo"
    assert fila["Marcas del día"] == "08:00  ·  12:01  ·  17:00"
    assert fila["Observaciones"] in ("", None)


def test_detalle_marca_la_salida_asumida_si_no_hay_marca_a_esa_hora(tmp_path: Path) -> None:
    extras = _extras([MarcaReporte("2026-04-15T15:56:27", 7, 1, "Reloj Principal")])
    items = [_vista(1, "Pérez Juan", "2026-04-15", EstadoAsistencia.TARDE.value, "15:56:27")]
    ws = _exportar(tmp_path, _datos(items=items, extras=extras))["Detalle"]
    fila = _fila(ws, _TABLA + 1)
    assert fila["Estado"] == "Tarde"
    assert fila["Marcas del día"] == "15:56"
    assert "Salida asumida" in fila["Observaciones"]


def test_detalle_no_repite_la_nota_de_salida_asumida(tmp_path: Path) -> None:
    extras = _extras([MarcaReporte("2026-04-15T08:54:00", 7, 1, "Reloj Principal")])
    items = [
        _vista(
            1,
            "Pérez Juan",
            "2026-04-15",
            entrada="08:54:00",
            observaciones="Salida asumida según turno: sin segunda marcada.",
        )
    ]
    ws = _exportar(tmp_path, _datos(items=items, extras=extras))["Detalle"]
    assert _fila(ws, _TABLA + 1)["Observaciones"].count("asumida") == 1


def test_detalle_lleva_la_nota_de_vacaciones_y_permisos(tmp_path: Path) -> None:
    items = [_vista(2, "López Bob", "2026-04-15", EstadoAsistencia.VACACIONES.value, None, None)]
    ws = _exportar(tmp_path, _datos(items=items, extras=_extras()))["Detalle"]
    fila = _fila(ws, _TABLA + 1)
    assert fila["Estado"] == "Vacaciones"
    assert fila["Observaciones"] == "Vacaciones: Programadas."
    assert fila["Entrada"] is None


def test_detalle_agrupa_marcas_repetidas_en_el_mismo_minuto(tmp_path: Path) -> None:
    extras = _extras(
        [
            MarcaReporte("2026-04-15T08:00:10", 7, 1, "Reloj"),
            MarcaReporte("2026-04-15T08:00:40", 7, 1, "Reloj"),
            MarcaReporte("2026-04-15T17:00:00", 7, 1, "Reloj"),
        ]
    )
    ws = _exportar(tmp_path, _datos(extras=extras))["Detalle"]
    assert _fila(ws, _TABLA + 1)["Marcas del día"] == "08:00 ×2  ·  17:00"


def test_detalle_sin_contexto_no_inventa_salida_asumida(tmp_path: Path) -> None:
    ws = _exportar(tmp_path, _datos())["Detalle"]
    fila = _fila(ws, _TABLA + 1)
    assert fila["Observaciones"] in ("", None)
    assert fila["ID reloj"] == "—"


# ── Marcas ────────────────────────────────────────────────────────────────────


def test_marcas_lista_todas_incluso_las_de_ids_sin_empleado(tmp_path: Path) -> None:
    ws = _exportar(tmp_path, _datos(extras=_extras()))["Marcas"]
    assert "4 marcas en el período (1 de IDs que no son de ningún empleado" in ws["A4"].value
    filas = [_fila(ws, r) for r in range(_TABLA + 1, _TABLA + 5)]
    assert [f["Hora"] for f in filas] == [time(8, 0), time(9, 30), time(12, 1, 10), time(17, 0)]
    assert filas[0]["Fecha"] == datetime(2026, 4, 15)
    assert filas[0]["Empleado"] == "Pérez Juan"
    assert filas[0]["Reloj"] == "Reloj Principal"
    assert filas[1]["ID reloj"] == 98
    assert filas[1]["Empleado"] == "Sin empleado registrado"
    assert ws.cell(row=_TABLA + 2, column=1).fill.fgColor.rgb.endswith("FFF4DE")


def test_marcas_vacias_lo_dice(tmp_path: Path) -> None:
    ws = _exportar(tmp_path, _datos(extras=_extras([])))["Marcas"]
    assert ws.cell(row=_TABLA + 1, column=1).value == "No hay marcas guardadas en este período."


# ── Información ───────────────────────────────────────────────────────────────


def _pares(ws: Any) -> Dict[str, Any]:
    return {
        ws.cell(row=r, column=1).value: ws.cell(row=r, column=2).value
        for r in range(_TABLA + 1, ws.max_row + 1)
    }


def test_informacion_sin_filtro_dice_todos(tmp_path: Path) -> None:
    pares = _pares(_exportar(tmp_path, _datos(extras=_extras()))["Información"])
    assert pares["Empleados"] == "Todos (1 con asistencia en el período)"
    assert pares["Generado por"] == "alice"
    assert pares["Período"] == "01/04/2026 al 30/04/2026 (30 días)"
    assert pares["Marcas en el período"] == "4 (1 sin empleado registrado)"
    assert "no se borran" in pares["Sobre las marcas"]
    assert "Sin marca de salida" in pares  # leyenda de estados


def test_informacion_con_filtro_muestra_nombre(tmp_path: Path) -> None:
    datos = _datos(empleado_id_filtro=7, empleado_nombre_filtro="Pérez Juan")
    assert _pares(_exportar(tmp_path, datos)["Información"])["Empleados"] == "Pérez Juan"


def test_sin_hora_local_muestra_la_utc(tmp_path: Path) -> None:
    datos = DatosReporteAsistencia(
        items=[_vista(1, "Pérez Juan", "2026-04-15")],
        resumen_por_empleado=[_resumen(1, "Pérez Juan")],
        rango_desde="2026-04-15",
        rango_hasta="2026-04-15",
        empleado_id_filtro=None,
        empleado_nombre_filtro=None,
        actor_username="alice",
        timestamp_iso="2026-04-24T16:00:00+00:00",
    )
    ws = _exportar(tmp_path, datos)["Resumen"]
    assert ws["A4"].value == "Generado el 2026-04-24T16:00:00+00:00 (UTC) por alice"
    assert "(1 día)" in ws["A3"].value


# ── Balance de tiempo, estadísticas, ausencias y permisos ────────────────────


def _resumen_con_tiempo(emp_id: int, nombre: str, tiempo: TiempoEmpleado) -> ResumenEmpleado:
    return ResumenEmpleado(
        empleado_id=emp_id,
        empleado_nombre=nombre,
        empleado_dni=None,
        dias_presente=2,
        dias_tarde=1,
        dias_salida_temprana=0,
        dias_ausente=1,
        dias_feriado=0,
        dias_sin_turno=0,
        dias_incompleto=0,
        total_minutos_tarde=tiempo.tarde,
        total_minutos_salida_temprana=tiempo.salida_temprana,
        tiempo=tiempo,
    )


def _mes() -> DatosReporteAsistencia:
    """Juan: 2 días trabajados (1 tarde), 1 falta. Bob: vacaciones 2 días."""
    items = [
        _vista(1, "Pérez Juan", "2026-04-13", EstadoAsistencia.TARDE.value, "08:05:00"),
        _vista(1, "Pérez Juan", "2026-04-14", salida="17:45:00"),
        _vista(1, "Pérez Juan", "2026-04-15", EstadoAsistencia.AUSENTE.value, None, None),
        _vista(2, "López Bob", "2026-04-14", EstadoAsistencia.VACACIONES.value, None, None),
        _vista(2, "López Bob", "2026-04-15", EstadoAsistencia.VACACIONES.value, None, None),
    ]
    juan = TiempoEmpleado(
        programado=1620,
        trabajado=1060,
        tarde=5,
        extra=45,
        dias_con_atraso=1,
        dias_con_extra=1,
        entrada_promedio="08:02",
    )
    resumen = [
        _resumen_con_tiempo(1, "Pérez Juan", juan),
        _resumen_con_tiempo(2, "López Bob", TiempoEmpleado()),
    ]
    extras = ExtrasReporte(
        empleados=_extras().empleados,
        horarios={(1, d): HorarioDia("08:00", "17:00") for d in range(5)},
        ausencias=[
            AusenciaReporte(
                2,
                "VACACIONES",
                "2026-04-14",
                "2026-04-17",
                "Programadas.",
                "admin",
                "2026-04-01T16:00:00+00:00",
            )
        ],
    )
    return _datos(items=items, resumen=resumen, extras=extras)


def test_balance_compensa_atraso_con_extra(tmp_path: Path) -> None:
    ws = _exportar(tmp_path, _mes())["Balance de tiempo"]
    assert "Balance = tiempo extra − atraso − salida temprana" in ws["A5"].value
    juan = _fila(ws, _TABLA + 1)
    assert juan["Días con atraso"] == 1
    assert juan["Atraso (h:mm)"] == timedelta(minutes=5)
    assert juan["Extra al salir (h:mm)"] == timedelta(minutes=45)
    assert juan["Balance (h:mm)"] == "+0:40"
    assert juan["Balance (min)"] == 40
    assert juan["Resultado"] == "A favor: 0:40 de tiempo extra"
    assert _fila(ws, _TABLA + 2)["Resultado"] == "Al día (se compensa)"
    assert _fila(ws, _TABLA + 3)["Empleado"] == "TOTAL (2 empleados)"


def test_estadisticas_por_empleado(tmp_path: Path) -> None:
    ws = _exportar(tmp_path, _mes())["Estadísticas"]
    juan = _fila(ws, _TABLA + 1)
    assert juan["Días laborables"] == 3
    assert juan["Días trabajados"] == 2
    assert juan["Faltas"] == 1
    assert juan["% Asistencia"] == 2 / 3
    assert juan["Llegadas tarde"] == 1
    assert juan["% Puntualidad"] == 0.5
    assert juan["Atraso promedio (min)"] == 5
    assert juan["Entrada promedio"] == "08:02"
    assert juan["% Cumplimiento de jornada"] == 1060 / 1620
    assert ws.cell(row=_TABLA + 1, column=7).number_format == "0%"
    bob = _fila(ws, _TABLA + 2)
    assert bob["Días con permiso o vacaciones"] == 2
    assert bob["% Asistencia"] is None  # no tenía días que trabajar


def test_estadisticas_trae_grafico_y_tabla_por_departamento(tmp_path: Path) -> None:
    ws = _exportar(tmp_path, _mes())["Estadísticas"]
    assert len(ws._charts) == 1
    valores = [ws.cell(row=r, column=1).value for r in range(1, ws.max_row + 1)]
    assert "Por departamento" in valores
    assert "Ventas" in valores and "Bodega" in valores


def test_ausencias_dice_las_fechas_de_cada_falta_y_permiso(tmp_path: Path) -> None:
    ws = _exportar(tmp_path, _mes())["Ausencias y permisos"]
    juan, bob = _fila(ws, _TABLA + 1), _fila(ws, _TABLA + 2)
    assert juan["Faltas"] == 1
    assert juan["Fechas de las faltas"] == "15/04"
    assert bob["Vacaciones"] == 2
    assert bob["Fechas de vacaciones"] == "14/04 al 15/04"
    assert bob["Total días fuera"] == 2


def test_permisos_registrados_con_fechas_nota_y_quien(tmp_path: Path) -> None:
    ws = _exportar(tmp_path, _mes())["Permisos registrados"]
    assert ws["A5"].value == "1 registro: Vacaciones 1"
    fila = _fila(ws, _TABLA + 1)
    assert fila["Empleado"] == "López Bob"
    assert fila["Tipo"] == "Vacaciones"
    assert fila["Desde"] == datetime(2026, 4, 14)
    assert fila["Hasta"] == datetime(2026, 4, 17)
    assert fila["Días en el período"] == 2
    assert fila["Fechas en el período"] == "14/04 al 15/04"
    assert fila["Motivo o nota"] == "Programadas."
    assert fila["Registrado por"] == "admin"


def test_un_solo_empleado_no_lleva_fila_de_total(tmp_path: Path) -> None:
    wb = _exportar(tmp_path, _datos(empleado_id_filtro=1, empleado_nombre_filtro="Pérez Juan"))
    for nombre in ("Resumen", "Balance de tiempo", "Ausencias y permisos"):
        ws = wb[nombre]
        assert all(
            not str(ws.cell(row=r, column=1).value or "").startswith("TOTAL")
            for r in range(_TABLA + 1, ws.max_row + 1)
        )


def test_fechas_compactas_en_rangos() -> None:
    from datetime import date

    from infrastructure.exporters.xlsx_asistencia_exporter import _fechas_compactas

    fechas = [date(2026, 10, d) for d in (5, 12, 13, 14, 20)]
    assert _fechas_compactas(fechas) == "05/10, 12/10 al 14/10, 20/10"
    assert _fechas_compactas([date(2025, 12, 31), date(2026, 1, 1)], con_anio=True) == (
        "31/12/2025 al 01/01/2026"
    )
