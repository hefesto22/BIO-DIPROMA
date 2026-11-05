"""Implementación xlsx de ``IAsistenciaExporter`` con openpyxl.

Un reporte de control detallado, en ocho hojas con el mismo encabezado
(título, empresa, período, empleado y quién lo generó). Cada tabla tiene
filtros, panel congelado y queda lista para imprimir (ajustada al ancho,
numeración de páginas):

    1. ``Resumen``              — una fila por empleado: ficha, días por
                                  estado, atraso, salida temprana, tiempo
                                  extra, balance, permanencia y marcas.
    2. ``Estadísticas``         — por empleado y por departamento: % de
                                  asistencia, % de puntualidad, entrada
                                  promedio, horas programadas y trabajadas,
                                  % de cumplimiento de jornada; con gráfico.
    3. ``Balance de tiempo``    — atraso contra tiempo extra (se compensan):
                                  cuánto queda a favor o debe cada uno.
    4. ``Ausencias y permisos`` — por empleado, las FECHAS de cada falta,
                                  vacación, día libre, permiso e incapacidad.
    5. ``Permisos registrados`` — cada vacación o permiso registrado: tipo,
                                  rango, días que tocan el período, nota y
                                  quién lo registró.
    6. ``Detalle``              — una fila por empleado y día con horario,
                                  entrada, salida, minutos, extra, balance,
                                  estado con color, marcas y observaciones.
    7. ``Marcas``               — cada marca guardada del período.
    8. ``Información``          — datos del reporte y qué significa cada
                                  estado y cada indicador.

Fechas y horas se escriben como fechas y horas de Excel (no texto), así
se pueden ordenar, filtrar y sumar; los tiempos usan ``[h]:mm``. El
balance (que puede ser negativo) va como texto con signo y, al lado, en
minutos para sumar u ordenar.

I/O:
    Cualquier ``OSError`` durante ``wb.save`` (permisos, disco lleno,
    archivo abierto en Excel) se propaga sin envolver. El servicio de
    reportes la traduce a ``ReporteIOError`` con mensaje en español.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.properties import PageSetupProperties
from openpyxl.worksheet.worksheet import Worksheet

from core.models.asistencia import EstadoAsistencia
from core.models.ausencia import TIPO_AUSENCIA_TEXTO
from core.services.asistencia_exporter import (
    AusenciaReporte,
    DatosReporteAsistencia,
    EmpleadoReporte,
    IAsistenciaExporter,
    MarcaReporte,
    ResumenEmpleado,
    horas_marcadas_por_dia,
    tiempos_por_dia,
)
from core.services.asistencia_service import AsistenciaVista
from core.services.balance_tiempo import (
    JUSTIFICADOS,
    TRABAJADOS,
    TiempoDia,
    TiempoEmpleado,
    texto_duracion,
)

E = EstadoAsistencia

# ── Textos ───────────────────────────────────────────────────────────────────

ESTADO_TEXTO: Dict[str, str] = {
    E.PRESENTE.value: "A tiempo",
    E.TARDE.value: "Tarde",
    E.SALIDA_TEMPRANA.value: "Salida temprana",
    E.TARDE_Y_SALIDA_TEMPRANA.value: "Tarde y salida temprana",
    E.INCOMPLETO.value: "Sin marca de salida",
    E.AUSENTE.value: "Ausente",
    E.VACACIONES.value: "Vacaciones",
    E.DIA_LIBRE.value: "Día libre",
    E.PERMISO.value: "Permiso",
    E.INCAPACIDAD.value: "Incapacidad",
    E.FERIADO.value: "Feriado",
    E.SIN_TURNO.value: "Sin turno",
}

_SIGNIFICADO: Tuple[Tuple[str, str], ...] = (
    ("A tiempo", "Entró y salió dentro de su horario (con la tolerancia del turno)."),
    ("Tarde", "Entró después de la hora de entrada más la tolerancia del turno."),
    ("Salida temprana", "Salió antes de la hora de salida del turno."),
    ("Tarde y salida temprana", "Las dos cosas el mismo día (cuenta en ambas columnas)."),
    (
        "Sin marca de salida",
        "Marcó la entrada pero todavía no la salida (el día no había cerrado).",
    ),
    ("Ausente / Falta", "Tenía turno ese día, no marcó y no tenía permiso registrado."),
    (
        "Vacaciones, Día libre, Permiso, Incapacidad",
        "Registrado en Turnos › Vacaciones y permisos.",
    ),
    ("Feriado", "Feriado cargado en Configuración › Feriados."),
    ("Sin turno", "Ese día no le tocaba trabajar."),
    (
        "Salida asumida",
        "No hubo marca de salida y el día ya cerró: se tomó la hora de salida del turno "
        "(ese día no suma tiempo extra).",
    ),
    (
        "Atraso",
        "Minutos desde la hora EXACTA de entrada: con entrada 07:00, marcar 07:05 son "
        "5 min. Se suman aunque estén dentro de la tolerancia.",
    ),
    ("Salida temprana (min)", "Minutos que salió antes de la hora exacta de salida."),
    (
        "Tiempo extra",
        "Minutos después de la hora de salida (salir 17:05 con salida 17:00 son 5 min), "
        "más lo trabajado en días libres o feriados (de la primera a la última marca).",
    ),
    (
        "Balance",
        "Tiempo extra − atraso − salida temprana. Positivo: tiempo a favor del empleado. "
        "Negativo: tiempo que debe. Llegar 5 min tarde y quedarse 5 min más da 0.",
    ),
    ("% Asistencia", "Días trabajados ÷ (días trabajados + faltas). Los permisos no restan."),
    ("% Puntualidad", "Días trabajados sin llegar tarde ÷ días trabajados."),
    (
        "% Cumplimiento de jornada",
        "Horas trabajadas (entrada a salida) ÷ horas que le tocaban, en los días con las "
        "dos marcas y en las faltas. La hora de almuerzo del turno no cuenta en ninguna "
        "de las dos.",
    ),
    ("Entrada promedio", "Hora promedio a la que marcó la entrada en los días con turno."),
)

_DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
_OBS_SALIDA_ASUMIDA = "Salida asumida según turno: no hubo marca de salida."
_SIN_EMPLEADO = "Sin empleado registrado"

# ── Estilo ───────────────────────────────────────────────────────────────────

_AZUL = "1E4FA3"
_GRIS_TEXTO = "5B6472"
_VERDE = "15803D"
_ROJO = "B91C1C"
_BORDE = Side(style="thin", color="D9DEE7")
_CELDA_BORDE = Border(left=_BORDE, right=_BORDE, top=_BORDE, bottom=_BORDE)
_FUENTE_TITULO = Font(size=16, bold=True, color="1F2937")
_FUENTE_SUBTITULO = Font(size=11, color=_GRIS_TEXTO)
_FUENTE_SECCION = Font(size=12, bold=True, color="1F2937")
_FUENTE_NOTA = Font(size=10, italic=True, color=_GRIS_TEXTO)
_FUENTE_ENCABEZADO = Font(bold=True, color="FFFFFF")
_RELLENO_ENCABEZADO = PatternFill("solid", fgColor=_AZUL)
_RELLENO_TOTAL = PatternFill("solid", fgColor="EEF2F8")
_RELLENO_AVISO = PatternFill("solid", fgColor="FFF4DE")
_CENTRO = Alignment(horizontal="center", vertical="center")
_DERECHA = Alignment(horizontal="right", vertical="center")
_IZQUIERDA = Alignment(horizontal="left", vertical="center")
_AJUSTAR = Alignment(horizontal="left", vertical="center", wrap_text=True)

# Color (relleno, texto) del estado en el Detalle.
_COLOR_ESTADO: Dict[str, Tuple[str, str]] = {
    E.PRESENTE.value: ("E8F5EC", _VERDE),
    E.TARDE.value: ("FFF4DE", "B45309"),
    E.SALIDA_TEMPRANA.value: ("FFF4DE", "B45309"),
    E.TARDE_Y_SALIDA_TEMPRANA.value: ("FFF4DE", "B45309"),
    E.INCOMPLETO.value: ("FFF4DE", "B45309"),
    E.AUSENTE.value: ("FDECEC", _ROJO),
    E.VACACIONES.value: ("E6F6F4", "0F766E"),
    E.DIA_LIBRE.value: ("E6F6F4", "0F766E"),
    E.PERMISO.value: ("E6F6F4", "0F766E"),
    E.INCAPACIDAD.value: ("E6F6F4", "0F766E"),
    E.FERIADO.value: ("F3F4F6", "6B7280"),
    E.SIN_TURNO.value: ("F3F4F6", "6B7280"),
}

_FMT_FECHA = "dd/mm/yyyy"
_FMT_FECHA_HORA = "dd/mm/yyyy hh:mm"
_FMT_HORA = "hh:mm"
_FMT_HORA_SEG = "hh:mm:ss"
_FMT_DURACION = "[h]:mm"
_FMT_ENTERO_SIN_CERO = '0;-0;"";@'
_FMT_CON_SIGNO = "+0;-0;0;@"
_FMT_PORCENTAJE = "0%"

# Filas del encabezado: título, empresa, período, generado; nota en la 5;
# la tabla en la 6.
_FILA_TABLA = 6

# Ausencias por tipo, en el orden de las columnas.
_TIPOS_AUSENCIA: Tuple[Tuple[str, str], ...] = (
    (E.AUSENTE.value, "Faltas"),
    (E.VACACIONES.value, "Vacaciones"),
    (E.DIA_LIBRE.value, "Días libres"),
    (E.PERMISO.value, "Permisos"),
    (E.INCAPACIDAD.value, "Incapacidad"),
)


@dataclass(frozen=True)
class _Columna:
    titulo: str
    ancho: float
    alinear: Alignment = _CENTRO
    formato: Optional[str] = None


class XlsxAsistenciaExporter(IAsistenciaExporter):
    """Exporta un reporte de asistencia detallado a un archivo .xlsx."""

    def __init__(self, organizacion: str = "", sistema: str = "") -> None:
        """``organizacion`` y ``sistema`` salen en el encabezado de cada hoja."""
        self._organizacion = organizacion
        self._sistema = sistema

    def exportar(self, datos: DatosReporteAsistencia, output_path: str) -> int:
        """Genera el archivo y devuelve la cantidad de filas de detalle."""
        calc = _Calculos(datos)
        wb = Workbook()
        hoja = wb.active
        if hoja is None:  # defensa: openpyxl siempre crea la hoja
            hoja = wb.create_sheet("Resumen")
        hoja.title = "Resumen"
        enc = self._encabezado(datos)
        _llenar_resumen(hoja, datos, calc, enc)
        _llenar_estadisticas(wb.create_sheet("Estadísticas"), datos, calc, enc)
        _llenar_balance(wb.create_sheet("Balance de tiempo"), datos, calc, enc)
        _llenar_ausencias(wb.create_sheet("Ausencias y permisos"), datos, calc, enc)
        _llenar_permisos(wb.create_sheet("Permisos registrados"), datos, calc, enc)
        _llenar_detalle(wb.create_sheet("Detalle"), datos, calc, enc)
        _llenar_marcas(wb.create_sheet("Marcas"), datos, calc, enc)
        _llenar_informacion(wb.create_sheet("Información"), datos, calc, enc)
        wb.save(output_path)
        return len(datos.items)

    def _encabezado(self, datos: DatosReporteAsistencia) -> List[Tuple[str, Font]]:
        empresa = " · ".join(t for t in (self._organizacion, self._sistema) if t)
        desde, hasta = date.fromisoformat(datos.rango_desde), date.fromisoformat(datos.rango_hasta)
        dias = (hasta - desde).days + 1
        empleado = datos.empleado_nombre_filtro or "Todos los empleados"
        return [
            ("Reporte de asistencia", _FUENTE_TITULO),
            (empresa, _FUENTE_SUBTITULO),
            (
                f"Período: del {_fecha(desde)} al {_fecha(hasta)} "
                f"({_plural(dias, 'día', 'días')})   ·   Empleado: {empleado}",
                Font(size=11, bold=True, color="1F2937"),
            ),
            (
                f"Generado el {_momento_legible(datos)} por {datos.actor_username}",
                _FUENTE_SUBTITULO,
            ),
        ]


# ── Cálculos compartidos por las hojas ───────────────────────────────────────


@dataclass
class _Indicadores:
    """Lo que la hoja Estadísticas muestra de un empleado o un grupo."""

    empleados: int = 0
    trabajados: int = 0
    faltas: int = 0
    justificados: int = 0
    llegadas_tarde: int = 0
    atraso: int = 0
    dias_con_atraso: int = 0
    programado: int = 0
    trabajado: int = 0
    extra: int = 0
    balance: int = 0

    @property
    def laborables(self) -> int:
        return self.trabajados + self.faltas + self.justificados

    @property
    def asistencia(self) -> Optional[float]:
        base = self.trabajados + self.faltas
        return self.trabajados / base if base else None

    @property
    def puntualidad(self) -> Optional[float]:
        if not self.trabajados:
            return None
        return (self.trabajados - self.llegadas_tarde) / self.trabajados

    @property
    def cumplimiento(self) -> Optional[float]:
        return self.trabajado / self.programado if self.programado else None

    @property
    def atraso_promedio(self) -> Optional[int]:
        return round(self.atraso / self.dias_con_atraso) if self.dias_con_atraso else None

    def sumar(self, otro: "_Indicadores") -> None:
        for campo in self.__dataclass_fields__:
            setattr(self, campo, getattr(self, campo) + getattr(otro, campo))


class _Calculos:
    """Agrupa marcas, tiempos y fechas una sola vez para todas las hojas."""

    def __init__(self, datos: DatosReporteAsistencia) -> None:
        self.datos = datos
        self.extras = datos.extras
        self.con_contexto = bool(datos.extras.empleados)
        self.marcas_por_dia = horas_marcadas_por_dia(datos.extras)
        self.marcas_por_empleado: Dict[int, int] = defaultdict(int)
        for marca in datos.extras.marcas:
            if marca.empleado_id is not None:
                self.marcas_por_empleado[marca.empleado_id] += 1
        self.tiempos: Dict[Tuple[int, str], TiempoDia] = (
            tiempos_por_dia(datos.items, datos.extras) if self.con_contexto else {}
        )
        self.permanencia_por_empleado: Dict[int, int] = defaultdict(int)
        # empleado → estado → fechas (en orden).
        self.fechas: Dict[int, Dict[str, List[date]]] = defaultdict(lambda: defaultdict(list))
        for vista in sorted(datos.items, key=lambda v: v.asistencia.fecha):
            a = vista.asistencia
            minutos = _permanencia(vista)
            if minutos is not None:
                self.permanencia_por_empleado[a.empleado_id] += minutos
            self.fechas[a.empleado_id][a.estado].append(date.fromisoformat(a.fecha))
        desde, hasta = date.fromisoformat(datos.rango_desde), date.fromisoformat(datos.rango_hasta)
        self.con_anio = desde.year != hasta.year

    def ficha(self, empleado_id: int, nombre: str, dni: Optional[str]) -> EmpleadoReporte:
        return self.extras.empleados.get(
            empleado_id, EmpleadoReporte(nombre=nombre, dni=dni, id_reloj=None)
        )

    def tiempo(self, r: ResumenEmpleado) -> TiempoEmpleado:
        return r.tiempo if r.tiempo is not None else TiempoEmpleado()

    def tiempo_del_dia(self, vista: AsistenciaVista) -> Optional[TiempoDia]:
        a = vista.asistencia
        return self.tiempos.get((a.empleado_id, a.fecha))

    def dias_con(self, empleado_id: int, estado: str) -> List[date]:
        return self.fechas.get(empleado_id, {}).get(estado, [])

    def indicadores(self, r: ResumenEmpleado) -> _Indicadores:
        cuenta = {estado: len(f) for estado, f in self.fechas.get(r.empleado_id, {}).items()}
        t = self.tiempo(r)
        return _Indicadores(
            empleados=1,
            trabajados=sum(cuenta.get(e, 0) for e in TRABAJADOS),
            faltas=cuenta.get(E.AUSENTE.value, 0),
            justificados=sum(cuenta.get(e, 0) for e in JUSTIFICADOS),
            llegadas_tarde=cuenta.get(E.TARDE.value, 0)
            + cuenta.get(E.TARDE_Y_SALIDA_TEMPRANA.value, 0),
            atraso=t.tarde,
            dias_con_atraso=t.dias_con_atraso,
            programado=t.programado,
            trabajado=t.trabajado,
            extra=t.extra_total,
            balance=t.balance,
        )

    def horas_marcadas(self, empleado_id: int, fecha_iso: str) -> List[str]:
        return self.marcas_por_dia.get((empleado_id, fecha_iso), [])

    def salida_asumida(self, vista: AsistenciaVista) -> bool:
        """La salida no coincide con ninguna marca de ese día ni del siguiente."""
        a = vista.asistencia
        if not self.con_contexto or not a.hora_salida_real or not a.hora_entrada_real:
            return False
        siguiente = (date.fromisoformat(a.fecha) + timedelta(days=1)).isoformat()
        marcadas: Set[str] = set(self.horas_marcadas(a.empleado_id, a.fecha))
        marcadas.update(self.horas_marcadas(a.empleado_id, siguiente))
        return a.hora_salida_real[:8] not in marcadas

    def observaciones(self, vista: AsistenciaVista) -> str:
        a = vista.asistencia
        partes: List[str] = []
        nota = self.extras.notas_ausencia.get((a.empleado_id, a.fecha))
        if nota and a.estado in JUSTIFICADOS:
            partes.append(nota)
        propia = (a.observaciones or "").strip()
        if propia:
            partes.append(propia)
        if self.salida_asumida(vista) and not any("asumida" in p.lower() for p in partes):
            partes.append(_OBS_SALIDA_ASUMIDA)
        return " · ".join(partes)

    def fechas_texto(self, fechas: Sequence[date]) -> str:
        return _fechas_compactas(fechas, self.con_anio)


# ── Hoja: Resumen ────────────────────────────────────────────────────────────

_COLS_RESUMEN: Tuple[_Columna, ...] = (
    _Columna("Empleado", 32, _IZQUIERDA),
    _Columna("DNI", 17),
    _Columna("ID reloj", 9),
    _Columna("Departamento", 18, _IZQUIERDA),
    _Columna("Cargo", 22, _IZQUIERDA),
    _Columna("A tiempo", 9, formato=_FMT_ENTERO_SIN_CERO),
    _Columna("Tarde", 8, formato=_FMT_ENTERO_SIN_CERO),
    _Columna("Salida temprana", 10, formato=_FMT_ENTERO_SIN_CERO),
    _Columna("Sin marca de salida", 11, formato=_FMT_ENTERO_SIN_CERO),
    _Columna("Faltas", 9, formato=_FMT_ENTERO_SIN_CERO),
    _Columna("Vacaciones", 11, formato=_FMT_ENTERO_SIN_CERO),
    _Columna("Días libres", 9, formato=_FMT_ENTERO_SIN_CERO),
    _Columna("Permisos", 9, formato=_FMT_ENTERO_SIN_CERO),
    _Columna("Incapacidad", 12, formato=_FMT_ENTERO_SIN_CERO),
    _Columna("Feriados", 9, formato=_FMT_ENTERO_SIN_CERO),
    _Columna("Días sin turno", 9, formato=_FMT_ENTERO_SIN_CERO),
    _Columna("Atraso total (h:mm)", 11, formato=_FMT_DURACION),
    _Columna("Salida temprana total (h:mm)", 13, formato=_FMT_DURACION),
    _Columna("Tiempo extra (h:mm)", 11, formato=_FMT_DURACION),
    _Columna("Balance (h:mm)", 11, _DERECHA),
    _Columna("Permanencia total (h:mm)", 13, formato=_FMT_DURACION),
    _Columna("Marcas", 9, formato=_FMT_ENTERO_SIN_CERO),
)
_COL_RESUMEN_BALANCE = 20


def _llenar_resumen(
    ws: Worksheet,
    datos: DatosReporteAsistencia,
    calc: _Calculos,
    encabezado: Sequence[Tuple[str, Font]],
) -> None:
    _preparar_hoja(ws, encabezado, _COLS_RESUMEN)
    resumen = datos.resumen_por_empleado
    totales: List[int] = [0] * 17
    balance_total = 0
    fila = _FILA_TABLA
    for fila, r in enumerate(resumen, start=_FILA_TABLA + 1):
        ficha = calc.ficha(r.empleado_id, r.empleado_nombre, r.empleado_dni)
        t = calc.tiempo(r)
        dias = [
            r.dias_presente,
            r.dias_tarde,
            r.dias_salida_temprana,
            r.dias_incompleto,
            r.dias_ausente,
            r.dias_vacaciones,
            r.dias_libre,
            r.dias_permiso,
            r.dias_incapacidad,
            r.dias_feriado,
            r.dias_sin_turno,
        ]
        tiempos = [
            t.tarde if r.tiempo is not None else r.total_minutos_tarde,
            t.salida_temprana if r.tiempo is not None else r.total_minutos_salida_temprana,
            t.extra_total,
        ]
        resto = [
            calc.permanencia_por_empleado.get(r.empleado_id, 0),
            calc.marcas_por_empleado.get(r.empleado_id, 0),
        ]
        valores: List[Any] = [
            r.empleado_nombre,
            r.empleado_dni or "",
            ficha.id_reloj if ficha.id_reloj is not None else "—",
            ficha.departamento,
            ficha.cargo,
            *dias,
            *tiempos,
            texto_duracion(t.balance, con_signo=True),
            *resto,
        ]
        _escribir_fila(ws, fila, valores, _COLS_RESUMEN)
        _pintar_balance(ws.cell(row=fila, column=_COL_RESUMEN_BALANCE), t.balance)
        for i, valor in enumerate([*dias, *tiempos, *resto]):
            totales[i] += valor
        balance_total += t.balance
    if len(resumen) > 1:
        fila += 1
        valores_total: List[Any] = [
            f"TOTAL ({_plural(len(resumen), 'empleado', 'empleados')})",
            "",
            "",
            "",
            "",
            *totales[:14],
            texto_duracion(balance_total, con_signo=True),
            *totales[14:16],
        ]
        _fila_total(ws, fila, valores_total, _COLS_RESUMEN)
    _cerrar_tabla(ws, fila, _COLS_RESUMEN)


# ── Hoja: Estadísticas ───────────────────────────────────────────────────────

_COLS_ESTADISTICAS: Tuple[_Columna, ...] = (
    _Columna("Empleado", 32, _IZQUIERDA),
    _Columna("Departamento", 18, _IZQUIERDA),
    _Columna("Días laborables", 11, formato=_FMT_ENTERO_SIN_CERO),
    _Columna("Días trabajados", 11, formato=_FMT_ENTERO_SIN_CERO),
    _Columna("Faltas", 8, formato=_FMT_ENTERO_SIN_CERO),
    _Columna("Días con permiso o vacaciones", 13, formato=_FMT_ENTERO_SIN_CERO),
    _Columna("% Asistencia", 11, formato=_FMT_PORCENTAJE),
    _Columna("Llegadas tarde", 10, formato=_FMT_ENTERO_SIN_CERO),
    _Columna("% Puntualidad", 12, formato=_FMT_PORCENTAJE),
    _Columna("Atraso promedio (min)", 11, formato=_FMT_ENTERO_SIN_CERO),
    _Columna("Entrada promedio", 10),
    _Columna("Horas programadas", 13, formato=_FMT_DURACION),
    _Columna("Horas trabajadas", 13, formato=_FMT_DURACION),
    _Columna("% Cumplimiento de jornada", 14, formato=_FMT_PORCENTAJE),
    _Columna("Tiempo extra (h:mm)", 11, formato=_FMT_DURACION),
    _Columna("Balance (h:mm)", 11, _DERECHA),
)
_COL_EST_ASISTENCIA = 7
_COL_EST_PUNTUALIDAD = 9
_COL_EST_BALANCE = 16


def _valores_indicadores(nombre: str, segundo: Any, ind: _Indicadores, entrada: str) -> List[Any]:
    return [
        nombre,
        segundo,
        ind.laborables,
        ind.trabajados,
        ind.faltas,
        ind.justificados,
        ind.asistencia,
        ind.llegadas_tarde,
        ind.puntualidad,
        ind.atraso_promedio,
        entrada,
        ind.programado,
        ind.trabajado,
        ind.cumplimiento,
        ind.extra,
        texto_duracion(ind.balance, con_signo=True),
    ]


def _llenar_estadisticas(
    ws: Worksheet,
    datos: DatosReporteAsistencia,
    calc: _Calculos,
    encabezado: Sequence[Tuple[str, Font]],
) -> None:
    _preparar_hoja(
        ws,
        encabezado,
        _COLS_ESTADISTICAS,
        nota="Indicadores del período por empleado y por departamento. Ver la hoja "
        "Información para saber cómo se calcula cada uno.",
    )
    resumen = datos.resumen_por_empleado
    por_depto: Dict[str, _Indicadores] = defaultdict(_Indicadores)
    total = _Indicadores()
    fila = _FILA_TABLA
    for fila, r in enumerate(resumen, start=_FILA_TABLA + 1):
        ficha = calc.ficha(r.empleado_id, r.empleado_nombre, r.empleado_dni)
        ind = calc.indicadores(r)
        entrada = calc.tiempo(r).entrada_promedio or "—"
        _escribir_fila(
            ws,
            fila,
            _valores_indicadores(r.empleado_nombre, ficha.departamento, ind, entrada),
            _COLS_ESTADISTICAS,
        )
        _pintar_balance(ws.cell(row=fila, column=_COL_EST_BALANCE), ind.balance)
        por_depto[ficha.departamento or "Sin departamento"].sumar(ind)
        total.sumar(ind)
    ultima_empleado = fila
    if len(resumen) > 1:
        fila += 1
        _fila_total(
            ws,
            fila,
            _valores_indicadores(
                f"TOTAL ({_plural(len(resumen), 'empleado', 'empleados')})", "", total, ""
            ),
            _COLS_ESTADISTICAS,
        )
    _cerrar_tabla(ws, fila, _COLS_ESTADISTICAS)

    if len(por_depto) > 1 or (len(por_depto) == 1 and len(resumen) > 1):
        fila += 2
        ws.cell(row=fila, column=1, value="Por departamento").font = _FUENTE_SECCION
        fila += 1
        titulos = [c.titulo for c in _COLS_ESTADISTICAS]
        titulos[0], titulos[1], titulos[10] = "Departamento", "Empleados", ""
        _celdas_titulo(ws, fila, titulos)
        ws.row_dimensions[fila].height = 46
        for depto in sorted(por_depto, key=str.casefold):
            fila += 1
            ind = por_depto[depto]
            _escribir_fila(
                ws, fila, _valores_indicadores(depto, ind.empleados, ind, ""), _COLS_ESTADISTICAS
            )
            _pintar_balance(ws.cell(row=fila, column=_COL_EST_BALANCE), ind.balance)

    if len(resumen) > 1:
        _grafico_asistencia(ws, ultima_empleado, fila + 3)


def _grafico_asistencia(ws: Worksheet, ultima_fila: int, fila_destino: int) -> None:
    """Barras horizontales: % de asistencia y de puntualidad por empleado."""
    grafico = BarChart()
    grafico.type = "bar"
    grafico.style = 10
    grafico.title = "Asistencia y puntualidad por empleado"
    grafico.y_axis.number_format = "0%"
    grafico.y_axis.scaling.min = 0
    grafico.y_axis.scaling.max = 1
    grafico.y_axis.majorGridlines = None
    grafico.x_axis.delete = False
    grafico.y_axis.delete = False
    # Empleados de arriba hacia abajo, en el mismo orden que la tabla.
    grafico.x_axis.scaling.orientation = "maxMin"
    for columna in (_COL_EST_ASISTENCIA, _COL_EST_PUNTUALIDAD):
        serie = Reference(
            ws, min_col=columna, max_col=columna, min_row=_FILA_TABLA, max_row=ultima_fila
        )
        grafico.add_data(serie, titles_from_data=True)
    grafico.set_categories(
        Reference(ws, min_col=1, max_col=1, min_row=_FILA_TABLA + 1, max_row=ultima_fila)
    )
    filas = ultima_fila - _FILA_TABLA
    grafico.height = max(7.5, 0.55 * filas + 3)
    grafico.width = 24
    ws.add_chart(grafico, f"A{fila_destino}")


# ── Hoja: Balance de tiempo ──────────────────────────────────────────────────

_COLS_BALANCE: Tuple[_Columna, ...] = (
    _Columna("Empleado", 32, _IZQUIERDA),
    _Columna("Departamento", 18, _IZQUIERDA),
    _Columna("Días con atraso", 10, formato=_FMT_ENTERO_SIN_CERO),
    _Columna("Atraso (h:mm)", 10, formato=_FMT_DURACION),
    _Columna("Días con salida temprana", 11, formato=_FMT_ENTERO_SIN_CERO),
    _Columna("Salida temprana (h:mm)", 11, formato=_FMT_DURACION),
    _Columna("Días con tiempo extra", 10, formato=_FMT_ENTERO_SIN_CERO),
    _Columna("Extra al salir (h:mm)", 11, formato=_FMT_DURACION),
    _Columna("Días libres o feriados trabajados", 12, formato=_FMT_ENTERO_SIN_CERO),
    _Columna("Trabajo en días libres (h:mm)", 12, formato=_FMT_DURACION),
    _Columna("Tiempo extra total (h:mm)", 11, formato=_FMT_DURACION),
    _Columna("Descuentos (atraso + salida temprana)", 14, formato=_FMT_DURACION),
    _Columna("Balance (h:mm)", 11, _DERECHA),
    _Columna("Balance (min)", 10, formato=_FMT_CON_SIGNO),
    _Columna("Resultado", 30, _IZQUIERDA),
)
_COL_BAL_TEXTO = 13
_COL_BAL_RESULTADO = 15


def _valores_balance(nombre: str, depto: str, t: TiempoEmpleado) -> List[Any]:
    return [
        nombre,
        depto,
        t.dias_con_atraso,
        t.tarde,
        t.dias_con_salida_temprana,
        t.salida_temprana,
        t.dias_con_extra,
        t.extra,
        t.dias_descanso_trabajados,
        t.en_descanso,
        t.extra_total,
        t.descuentos,
        texto_duracion(t.balance, con_signo=True),
        t.balance,
        _resultado(t.balance),
    ]


def _llenar_balance(
    ws: Worksheet,
    datos: DatosReporteAsistencia,
    calc: _Calculos,
    encabezado: Sequence[Tuple[str, Font]],
) -> None:
    _preparar_hoja(
        ws,
        encabezado,
        _COLS_BALANCE,
        nota="Balance = tiempo extra − atraso − salida temprana, desde la hora exacta del "
        "turno (entrar 07:05 con entrada 07:00 son 5 min de atraso; salir 17:05 con salida "
        "17:00 son 5 min de extra: se compensan).",
    )
    resumen = datos.resumen_por_empleado
    total = _SumaTiempo()
    fila = _FILA_TABLA
    for fila, r in enumerate(resumen, start=_FILA_TABLA + 1):
        ficha = calc.ficha(r.empleado_id, r.empleado_nombre, r.empleado_dni)
        t = calc.tiempo(r)
        _escribir_fila(
            ws, fila, _valores_balance(r.empleado_nombre, ficha.departamento, t), _COLS_BALANCE
        )
        _pintar_balance(ws.cell(row=fila, column=_COL_BAL_TEXTO), t.balance)
        _pintar_balance(ws.cell(row=fila, column=_COL_BAL_RESULTADO), t.balance)
        total.sumar(t)
    if len(resumen) > 1:
        fila += 1
        _fila_total(
            ws,
            fila,
            _valores_balance(
                f"TOTAL ({_plural(len(resumen), 'empleado', 'empleados')})", "", total.tiempo()
            ),
            _COLS_BALANCE,
        )
    _cerrar_tabla(ws, fila, _COLS_BALANCE)


class _SumaTiempo:
    def __init__(self) -> None:
        self._campos: Dict[str, int] = defaultdict(int)

    def sumar(self, t: TiempoEmpleado) -> None:
        for campo in (
            "programado",
            "trabajado",
            "tarde",
            "salida_temprana",
            "extra",
            "en_descanso",
            "dias_con_atraso",
            "dias_con_salida_temprana",
            "dias_con_extra",
            "dias_descanso_trabajados",
        ):
            self._campos[campo] += getattr(t, campo)

    def tiempo(self) -> TiempoEmpleado:
        c = self._campos
        return TiempoEmpleado(
            programado=c["programado"],
            trabajado=c["trabajado"],
            tarde=c["tarde"],
            salida_temprana=c["salida_temprana"],
            extra=c["extra"],
            en_descanso=c["en_descanso"],
            dias_con_atraso=c["dias_con_atraso"],
            dias_con_salida_temprana=c["dias_con_salida_temprana"],
            dias_con_extra=c["dias_con_extra"],
            dias_descanso_trabajados=c["dias_descanso_trabajados"],
        )


def _resultado(balance: int) -> str:
    if balance > 0:
        return f"A favor: {texto_duracion(balance)} de tiempo extra"
    if balance < 0:
        return f"Debe {texto_duracion(-balance)}"
    return "Al día (se compensa)"


# ── Hoja: Ausencias y permisos ───────────────────────────────────────────────

_COLS_AUSENCIAS: Tuple[_Columna, ...] = (
    _Columna("Empleado", 32, _IZQUIERDA),
    _Columna("Departamento", 18, _IZQUIERDA),
    _Columna("Faltas", 8, formato=_FMT_ENTERO_SIN_CERO),
    _Columna("Fechas de las faltas", 30, _AJUSTAR),
    _Columna("Vacaciones", 11, formato=_FMT_ENTERO_SIN_CERO),
    _Columna("Fechas de vacaciones", 26, _AJUSTAR),
    _Columna("Días libres", 9, formato=_FMT_ENTERO_SIN_CERO),
    _Columna("Fechas de días libres", 24, _AJUSTAR),
    _Columna("Permisos", 9, formato=_FMT_ENTERO_SIN_CERO),
    _Columna("Fechas de permisos", 24, _AJUSTAR),
    _Columna("Incapacidad", 12, formato=_FMT_ENTERO_SIN_CERO),
    _Columna("Fechas de incapacidad", 24, _AJUSTAR),
    _Columna("Total días fuera", 10, formato=_FMT_ENTERO_SIN_CERO),
)


def _llenar_ausencias(
    ws: Worksheet,
    datos: DatosReporteAsistencia,
    calc: _Calculos,
    encabezado: Sequence[Tuple[str, Font]],
) -> None:
    _preparar_hoja(
        ws,
        encabezado,
        _COLS_AUSENCIAS,
        nota="Fechas dentro del período. Falta = día con turno sin marcas y sin permiso "
        "registrado. Vacaciones y permisos cuentan solo días laborables.",
    )
    resumen = datos.resumen_por_empleado
    totales = [0] * len(_TIPOS_AUSENCIA)
    fila = _FILA_TABLA
    for fila, r in enumerate(resumen, start=_FILA_TABLA + 1):
        ficha = calc.ficha(r.empleado_id, r.empleado_nombre, r.empleado_dni)
        valores: List[Any] = [r.empleado_nombre, ficha.departamento]
        for i, (estado, _titulo) in enumerate(_TIPOS_AUSENCIA):
            fechas = calc.dias_con(r.empleado_id, estado)
            valores += [len(fechas), calc.fechas_texto(fechas)]
            totales[i] += len(fechas)
        valores.append(sum(v for v in valores[2::2] if isinstance(v, int)))
        _escribir_fila(ws, fila, valores, _COLS_AUSENCIAS)
        if valores[2]:
            ws.cell(row=fila, column=3).font = Font(bold=True, color=_ROJO)
    if len(resumen) > 1:
        fila += 1
        valores_total: List[Any] = [
            f"TOTAL ({_plural(len(resumen), 'empleado', 'empleados')})",
            "",
        ]
        for cantidad in totales:
            valores_total += [cantidad, ""]
        valores_total.append(sum(totales))
        _fila_total(ws, fila, valores_total, _COLS_AUSENCIAS)
    _cerrar_tabla(ws, fila, _COLS_AUSENCIAS)


# ── Hoja: Permisos registrados ───────────────────────────────────────────────

_COLS_PERMISOS: Tuple[_Columna, ...] = (
    _Columna("Empleado", 32, _IZQUIERDA),
    _Columna("Departamento", 18, _IZQUIERDA),
    _Columna("Tipo", 13),
    _Columna("Desde", 11, formato=_FMT_FECHA),
    _Columna("Hasta", 11, formato=_FMT_FECHA),
    _Columna("Días en el período", 10, formato=_FMT_ENTERO_SIN_CERO),
    _Columna("Fechas en el período", 28, _AJUSTAR),
    _Columna("Motivo o nota", 40, _AJUSTAR),
    _Columna("Registrado por", 14),
    _Columna("Registrado el", 16, formato=_FMT_FECHA_HORA),
)


def _llenar_permisos(
    ws: Worksheet,
    datos: DatosReporteAsistencia,
    calc: _Calculos,
    encabezado: Sequence[Tuple[str, Font]],
) -> None:
    registros = datos.extras.ausencias
    por_tipo: Dict[str, int] = defaultdict(int)
    for registro in registros:
        por_tipo[TIPO_AUSENCIA_TEXTO.get(registro.tipo, registro.tipo)] += 1
    nota = (
        f"{_plural(len(registros), 'registro', 'registros')}: "
        + ", ".join(f"{tipo} {n}" for tipo, n in sorted(por_tipo.items()))
        if registros
        else "No hay vacaciones ni permisos registrados en este período."
    )
    _preparar_hoja(ws, encabezado, _COLS_PERMISOS, nota=nota)
    fila = _FILA_TABLA
    nombres = {r.empleado_id: r.empleado_nombre for r in datos.resumen_por_empleado}
    for fila, registro in enumerate(registros, start=_FILA_TABLA + 1):
        _escribir_fila(ws, fila, _valores_permiso(registro, calc, nombres), _COLS_PERMISOS)
        colores = _COLOR_ESTADO.get(registro.tipo)
        if colores is not None:
            celda = ws.cell(row=fila, column=3)
            celda.fill = PatternFill("solid", fgColor=colores[0])
            celda.font = Font(bold=True, color=colores[1])
    _cerrar_tabla(ws, fila, _COLS_PERMISOS)


def _valores_permiso(
    registro: AusenciaReporte, calc: _Calculos, nombres: Dict[int, str]
) -> List[Any]:
    ficha = calc.extras.empleados.get(registro.empleado_id)
    nombre = nombres.get(registro.empleado_id) or (
        ficha.nombre if ficha is not None else f"Empleado #{registro.empleado_id}"
    )
    desde, hasta = date.fromisoformat(registro.desde), date.fromisoformat(registro.hasta)
    fechas = [f for f in calc.dias_con(registro.empleado_id, registro.tipo) if desde <= f <= hasta]
    return [
        nombre,
        ficha.departamento if ficha is not None else "",
        TIPO_AUSENCIA_TEXTO.get(registro.tipo, registro.tipo),
        desde,
        hasta,
        len(fechas),
        calc.fechas_texto(fechas),
        registro.nota or "",
        registro.registrado_por or "",
        _momento_local(registro.registrado_el),
    ]


# ── Hoja: Detalle ────────────────────────────────────────────────────────────

_COLS_DETALLE: Tuple[_Columna, ...] = (
    _Columna("Fecha", 11, formato=_FMT_FECHA),
    _Columna("Día", 10, _IZQUIERDA),
    _Columna("Empleado", 32, _IZQUIERDA),
    _Columna("ID reloj", 9),
    _Columna("Departamento", 18, _IZQUIERDA),
    _Columna("Turno", 16, _IZQUIERDA),
    _Columna("Horario", 13),
    _Columna("Entrada", 9, formato=_FMT_HORA),
    _Columna("Salida", 9, formato=_FMT_HORA),
    _Columna("Permanencia (h:mm)", 13, formato=_FMT_DURACION),
    _Columna("Min. tarde", 9, formato=_FMT_ENTERO_SIN_CERO),
    _Columna("Min. salida temprana", 11, formato=_FMT_ENTERO_SIN_CERO),
    _Columna("Min. extra", 9, formato=_FMT_ENTERO_SIN_CERO),
    _Columna("Balance del día (min)", 11, formato=_FMT_CON_SIGNO),
    _Columna("Estado", 22),
    _Columna("Marcas del día", 26, _AJUSTAR),
    _Columna("Observaciones", 48, _AJUSTAR),
)
_COL_DET_BALANCE = 14
_COL_ESTADO = 15


def _llenar_detalle(
    ws: Worksheet,
    datos: DatosReporteAsistencia,
    calc: _Calculos,
    encabezado: Sequence[Tuple[str, Font]],
) -> None:
    _preparar_hoja(ws, encabezado, _COLS_DETALLE)
    filas = sorted(
        datos.items,
        key=lambda v: (v.asistencia.fecha, v.empleado_nombre_completo.lower()),
    )
    fila = _FILA_TABLA
    for fila, vista in enumerate(filas, start=_FILA_TABLA + 1):
        a = vista.asistencia
        dia = date.fromisoformat(a.fecha)
        ficha = calc.ficha(a.empleado_id, vista.empleado_nombre_completo, vista.empleado_dni)
        horario = (
            calc.extras.horarios.get((a.turno_id_aplicado, dia.weekday()))
            if a.turno_id_aplicado is not None
            else None
        )
        tiempo = calc.tiempo_del_dia(vista)
        permanencia = _permanencia(vista)
        valores: List[Any] = [
            dia,
            _DIAS[dia.weekday()],
            vista.empleado_nombre_completo,
            ficha.id_reloj if ficha.id_reloj is not None else "—",
            ficha.departamento,
            vista.turno_nombre or "",
            horario.texto if horario is not None else "",
            _hora(a.hora_entrada_real),
            _hora(a.hora_salida_real),
            _duracion(permanencia) if permanencia is not None else None,
            tiempo.tarde if tiempo is not None else a.minutos_tarde,
            tiempo.salida_temprana if tiempo is not None else a.minutos_salida_temprana,
            tiempo.extra_total if tiempo is not None else None,
            tiempo.balance if tiempo is not None and _cuenta_en_balance(tiempo) else None,
            ESTADO_TEXTO.get(a.estado, a.estado),
            _lista_de_marcas(calc.horas_marcadas(a.empleado_id, a.fecha)),
            calc.observaciones(vista),
        ]
        _escribir_fila(ws, fila, valores, _COLS_DETALLE)
        if tiempo is not None and _cuenta_en_balance(tiempo):
            _pintar_balance(ws.cell(row=fila, column=_COL_DET_BALANCE), tiempo.balance)
        colores = _COLOR_ESTADO.get(a.estado)
        if colores is not None:
            celda = ws.cell(row=fila, column=_COL_ESTADO)
            celda.fill = PatternFill("solid", fgColor=colores[0])
            celda.font = Font(bold=True, color=colores[1])
        if a.estado in (E.SIN_TURNO.value, E.FERIADO.value) and not (
            tiempo is not None and tiempo.en_descanso
        ):
            for col in range(1, len(_COLS_DETALLE) + 1):
                if col != _COL_ESTADO:
                    ws.cell(row=fila, column=col).font = Font(color="8A94A3")
    _cerrar_tabla(ws, fila, _COLS_DETALLE)


def _cuenta_en_balance(t: TiempoDia) -> bool:
    return bool(t.tarde or t.salida_temprana or t.extra_total or t.trabajado)


# ── Hoja: Marcas ─────────────────────────────────────────────────────────────

_COLS_MARCAS: Tuple[_Columna, ...] = (
    _Columna("Fecha", 11, formato=_FMT_FECHA),
    _Columna("Día", 10, _IZQUIERDA),
    _Columna("Hora", 10, formato=_FMT_HORA_SEG),
    _Columna("ID reloj", 9),
    _Columna("Empleado", 32, _IZQUIERDA),
    _Columna("Departamento", 18, _IZQUIERDA),
    _Columna("Reloj", 24, _IZQUIERDA),
)


def _llenar_marcas(
    ws: Worksheet,
    datos: DatosReporteAsistencia,
    calc: _Calculos,
    encabezado: Sequence[Tuple[str, Font]],
) -> None:
    marcas = sorted(datos.extras.marcas, key=lambda m: (m.momento, m.id_reloj))
    sin_empleado = sum(1 for m in marcas if m.empleado_id is None)
    resumen = f"{_plural(len(marcas), 'marca', 'marcas')} en el período"
    if sin_empleado:
        resumen += f" ({sin_empleado} de IDs que no son de ningún empleado registrado)"
    _preparar_hoja(ws, [*encabezado[:3], (resumen, _FUENTE_SUBTITULO)], _COLS_MARCAS)
    fila = _FILA_TABLA
    for fila, marca in enumerate(marcas, start=_FILA_TABLA + 1):
        _escribir_fila(ws, fila, _valores_marca(marca, calc), _COLS_MARCAS)
        if marca.empleado_id is None:
            for col in range(1, len(_COLS_MARCAS) + 1):
                ws.cell(row=fila, column=col).fill = _RELLENO_AVISO
    if not marcas:
        ws.cell(
            row=_FILA_TABLA + 1,
            column=1,
            value="No hay marcas guardadas en este período.",
        ).font = Font(italic=True, color=_GRIS_TEXTO)
    _cerrar_tabla(ws, fila, _COLS_MARCAS, vertical=True)


def _valores_marca(marca: MarcaReporte, calc: _Calculos) -> List[Any]:
    momento = datetime.fromisoformat(marca.momento)
    ficha = calc.extras.empleados.get(marca.empleado_id) if marca.empleado_id is not None else None
    return [
        momento.date(),
        _DIAS[momento.weekday()],
        momento.time(),
        marca.id_reloj,
        ficha.nombre if ficha is not None else _SIN_EMPLEADO,
        ficha.departamento if ficha is not None else "",
        marca.reloj,
    ]


# ── Hoja: Información ────────────────────────────────────────────────────────


def _llenar_informacion(
    ws: Worksheet,
    datos: DatosReporteAsistencia,
    calc: _Calculos,
    encabezado: Sequence[Tuple[str, Font]],
) -> None:
    for fila, (texto, fuente) in enumerate(encabezado, start=1):
        ws.cell(row=fila, column=1, value=texto).font = fuente
    desde, hasta = date.fromisoformat(datos.rango_desde), date.fromisoformat(datos.rango_hasta)
    marcas = datos.extras.marcas
    sin_empleado = sum(1 for m in marcas if m.empleado_id is None)
    filas: List[Tuple[str, str]] = [
        ("Reporte", "Asistencia detallada"),
        (
            "Período",
            f"{_fecha(desde)} al {_fecha(hasta)} "
            f"({_plural((hasta - desde).days + 1, 'día', 'días')})",
        ),
        (
            "Empleados",
            datos.empleado_nombre_filtro
            or f"Todos ({len(datos.resumen_por_empleado)} con asistencia en el período)",
        ),
        ("Generado el", _momento_legible(datos)),
        ("Generado por", datos.actor_username),
        ("Renglones de detalle", str(len(datos.items))),
        ("Marcas en el período", f"{len(marcas)} ({sin_empleado} sin empleado registrado)"),
        ("Vacaciones y permisos registrados", str(len(datos.extras.ausencias))),
        (
            "Sobre las marcas",
            "Las marcas no se borran del reloj ni de la base de datos. Cada reporte se "
            "calcula con todas las marcas guardadas del período: uno sacado más adelante "
            "trae las mismas marcas más las nuevas.",
        ),
    ]
    fila = _FILA_TABLA
    _celdas_titulo(ws, fila, ("Dato", "Valor"))
    for fila, (campo, valor) in enumerate(filas, start=fila + 1):
        _par(ws, fila, campo, valor)
    fila += 2
    _celdas_titulo(ws, fila, ("Estado o indicador", "Qué significa"))
    for fila, (estado, texto) in enumerate(_SIGNIFICADO, start=fila + 1):
        _par(ws, fila, estado, texto)
    ws.column_dimensions["A"].width = 40
    ws.column_dimensions["B"].width = 95
    ws.sheet_view.showGridLines = False
    _preparar_impresion(ws, vertical=True)


def _par(ws: Worksheet, fila: int, campo: str, valor: str) -> None:
    celda = ws.cell(row=fila, column=1, value=campo)
    celda.font = Font(bold=True)
    celda.alignment = _AJUSTAR
    ws.cell(row=fila, column=2, value=valor).alignment = _AJUSTAR
    for col in (1, 2):
        ws.cell(row=fila, column=col).border = _CELDA_BORDE


# ── Helpers de formato ───────────────────────────────────────────────────────


def _preparar_hoja(
    ws: Worksheet,
    encabezado: Sequence[Tuple[str, Font]],
    columnas: Sequence[_Columna],
    nota: Optional[str] = None,
) -> None:
    for fila, (texto, fuente) in enumerate(encabezado, start=1):
        if texto:
            ws.cell(row=fila, column=1, value=texto).font = fuente
    if nota:
        ws.cell(row=_FILA_TABLA - 1, column=1, value=nota).font = _FUENTE_NOTA
    _celdas_titulo(ws, _FILA_TABLA, [c.titulo for c in columnas])
    ws.row_dimensions[_FILA_TABLA].height = 46
    for col, columna in enumerate(columnas, start=1):
        ws.column_dimensions[get_column_letter(col)].width = columna.ancho
    ws.freeze_panes = f"A{_FILA_TABLA + 1}"
    ws.sheet_view.showGridLines = False


def _celdas_titulo(ws: Worksheet, fila: int, titulos: Sequence[str]) -> None:
    for col, titulo in enumerate(titulos, start=1):
        celda = ws.cell(row=fila, column=col, value=titulo)
        celda.font = _FUENTE_ENCABEZADO
        celda.fill = _RELLENO_ENCABEZADO
        celda.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        celda.border = _CELDA_BORDE


def _escribir_fila(
    ws: Worksheet, fila: int, valores: Sequence[Any], columnas: Sequence[_Columna]
) -> None:
    for col, (valor, columna) in enumerate(zip(valores, columnas), start=1):
        if (
            columna.formato == _FMT_DURACION
            and isinstance(valor, int)
            and not isinstance(valor, bool)
        ):
            valor = _duracion(valor)
        celda = ws.cell(row=fila, column=col, value=valor)
        celda.alignment = columna.alinear
        celda.border = _CELDA_BORDE
        if columna.formato is not None:
            celda.number_format = columna.formato


def _fila_total(
    ws: Worksheet, fila: int, valores: Sequence[Any], columnas: Sequence[_Columna]
) -> None:
    _escribir_fila(ws, fila, valores, columnas)
    for col in range(1, len(columnas) + 1):
        celda = ws.cell(row=fila, column=col)
        celda.font = Font(bold=True, color=celda.font.color)
        celda.fill = _RELLENO_TOTAL


def _pintar_balance(celda: Any, balance: int) -> None:
    if balance:
        celda.font = Font(bold=True, color=_VERDE if balance > 0 else _ROJO)


def _cerrar_tabla(
    ws: Worksheet, ultima_fila: int, columnas: Sequence[_Columna], vertical: bool = False
) -> None:
    ultima = get_column_letter(len(columnas))
    ws.auto_filter.ref = f"A{_FILA_TABLA}:{ultima}{max(ultima_fila, _FILA_TABLA + 1)}"
    ws.print_title_rows = f"{_FILA_TABLA}:{_FILA_TABLA}"
    _preparar_impresion(ws, vertical=vertical)


def _preparar_impresion(ws: Worksheet, vertical: bool = False) -> None:
    ws.page_setup.orientation = "portrait" if vertical else "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.print_options.horizontalCentered = True
    ws.page_margins.left = ws.page_margins.right = 0.4
    ws.page_margins.top = ws.page_margins.bottom = 0.5
    pie = ws.oddFooter
    if pie is not None:
        pie.center.text = "Página &P de &N"
        pie.center.size = 9


# ── Conversión de valores ────────────────────────────────────────────────────


def _hora(valor: Optional[str]) -> Optional[time]:
    if not valor:
        return None
    try:
        return time.fromisoformat(valor[:8])
    except ValueError:
        return None


def _duracion(minutos: int) -> float:
    """Minutos → fracción de día (así Excel lo muestra como ``[h]:mm``)."""
    return minutos / 1440


def _permanencia(vista: AsistenciaVista) -> Optional[int]:
    """Minutos entre entrada y salida (cruza medianoche si la salida es menor)."""
    entrada = _hora(vista.asistencia.hora_entrada_real)
    salida = _hora(vista.asistencia.hora_salida_real)
    if entrada is None or salida is None:
        return None
    minutos = (salida.hour * 60 + salida.minute) - (entrada.hour * 60 + entrada.minute)
    return minutos + 1440 if minutos < 0 else minutos


def _lista_de_marcas(horas: Sequence[str]) -> str:
    """``["06:54:10", "06:54:40", "16:14:02"]`` → ``"06:54 ×2  ·  16:14"``."""
    grupos: List[Tuple[str, int]] = []
    for hora in horas:
        corta = hora[:5]
        if grupos and grupos[-1][0] == corta:
            grupos[-1] = (corta, grupos[-1][1] + 1)
        else:
            grupos.append((corta, 1))
    return "  ·  ".join(f"{h} ×{n}" if n > 1 else h for h, n in grupos)


def _fechas_compactas(fechas: Iterable[date], con_anio: bool = False) -> str:
    """Fechas en rangos: ``"05/10, 12/10 al 14/10, 20/10"``."""
    formato = "%d/%m/%Y" if con_anio else "%d/%m"
    tramos: List[Tuple[date, date]] = []
    for f in sorted(set(fechas)):
        if tramos and f - tramos[-1][1] == timedelta(days=1):
            tramos[-1] = (tramos[-1][0], f)
        else:
            tramos.append((f, f))
    return ", ".join(
        a.strftime(formato) if a == b else f"{a.strftime(formato)} al {b.strftime(formato)}"
        for a, b in tramos
    )


def _fecha(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def _momento_local(iso: Optional[str]) -> Optional[datetime]:
    """ISO (UTC u hora local) → fecha y hora local sin zona, para Excel."""
    if not iso:
        return None
    try:
        momento = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if momento.tzinfo is not None:
        momento = momento.astimezone().replace(tzinfo=None)
    return momento


def _momento_legible(datos: DatosReporteAsistencia) -> str:
    if datos.generado_local:
        momento = datetime.fromisoformat(datos.generado_local)
        return momento.strftime("%d/%m/%Y a las %H:%M")
    return f"{datos.timestamp_iso} (UTC)"
