"""Interfaz e infra de soporte para exportar reportes de asistencia.

Este módulo es la "frontera" entre el dominio (qué datos vamos a
exportar) y la infraestructura (cómo se materializa el archivo).
Vive bajo ``core/services/`` porque las dataclasses y la interfaz
forman parte del contrato del servicio — la implementación concreta
(openpyxl) vive en ``infrastructure/exporters/``.

Diseño (SOLID):
    - O (Open/Closed): nuevos formatos (CSV, PDF) implementan
      ``IAsistenciaExporter`` sin tocar ``ReporteService``.
    - S: cada exportador solo sabe escribir su formato; el cálculo
      del resumen es una función pura compartida (``calcular_resumen``).
    - D: ``ReporteService`` depende de la abstracción
      (``IAsistenciaExporter``), no de openpyxl.

Reglas de cálculo del resumen:
    Un día cuenta para una sola categoría EXCEPTO ``TARDE_Y_SALIDA_TEMPRANA``
    que cuenta para AMBAS — ``dias_tarde`` y ``dias_salida_temprana``.
    Esta es la convención del negocio: "el empleado tuvo 4 días tarde,
    incluyendo 1 día que también salió temprano".

    ``minutos_tarde`` y ``minutos_salida_temprana`` se acumulan tal cual
    los reporta la consolidación — no se duplican ni se cruzan.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from core.models.asistencia import EstadoAsistencia
from core.services.asistencia_service import AsistenciaVista
from core.services.balance_tiempo import (
    HorarioDia,
    TiempoDia,
    TiempoEmpleado,
    sumar_dias,
    tiempo_del_dia,
)


@dataclass(frozen=True)
class ResumenEmpleado:
    """Conteo agregado de días por estado para un empleado en el rango.

    Un día con ``TARDE_Y_SALIDA_TEMPRANA`` se cuenta en AMBAS columnas
    (``dias_tarde`` + ``dias_salida_temprana``). El resto de estados
    son mutuamente excluyentes.

    Atributos:
        empleado_id: PK del empleado.
        empleado_nombre: ``"apellidos nombres"`` ya formateado.
        empleado_dni: DNI o ``None`` si el empleado fue borrado.
        dias_presente: Conteo de ``PRESENTE``.
        dias_tarde: Conteo de ``TARDE`` + ``TARDE_Y_SALIDA_TEMPRANA``.
        dias_salida_temprana: Conteo de ``SALIDA_TEMPRANA`` +
            ``TARDE_Y_SALIDA_TEMPRANA``.
        dias_ausente: Conteo de ``AUSENTE``.
        dias_feriado: Conteo de ``FERIADO``.
        dias_sin_turno: Conteo de ``SIN_TURNO``.
        dias_incompleto: Conteo de ``INCOMPLETO``.
        total_minutos_tarde: Suma de ``minutos_tarde`` del rango.
        total_minutos_salida_temprana: Suma de
            ``minutos_salida_temprana`` del rango.
        dias_vacaciones / dias_libre / dias_permiso / dias_incapacidad:
            Conteo de días cubiertos por cada tipo de ausencia registrada.
    """

    empleado_id: int
    empleado_nombre: str
    empleado_dni: Optional[str]
    dias_presente: int
    dias_tarde: int
    dias_salida_temprana: int
    dias_ausente: int
    dias_feriado: int
    dias_sin_turno: int
    dias_incompleto: int
    total_minutos_tarde: int
    total_minutos_salida_temprana: int
    dias_vacaciones: int = 0
    dias_libre: int = 0
    dias_permiso: int = 0
    dias_incapacidad: int = 0
    tiempo: Optional[TiempoEmpleado] = None


@dataclass(frozen=True)
class EmpleadoReporte:
    """Datos de ficha del empleado que el reporte muestra junto a su nombre.

    Atributos:
        nombre: ``"apellidos nombres"``.
        dni: DNI o ``None``.
        id_reloj: ID del empleado en el reloj (``zkteco_id``) o ``None``.
        departamento / cargo: Nombres (vacíos si no se resolvieron).
    """

    nombre: str
    dni: Optional[str]
    id_reloj: Optional[int]
    departamento: str = ""
    cargo: str = ""


@dataclass(frozen=True)
class MarcaReporte:
    """Una marca tal como llegó del reloj (huella, rostro o tarjeta).

    Atributos:
        momento: ``"YYYY-MM-DDTHH:MM:SS"`` en hora local del reloj.
        id_reloj: ID de usuario en el reloj.
        empleado_id: Empleado con ese ID de reloj, o ``None`` si el ID no
            es de ningún empleado registrado.
        reloj: Nombre del reloj que la registró.
    """

    momento: str
    id_reloj: int
    empleado_id: Optional[int]
    reloj: str


@dataclass(frozen=True)
class AusenciaReporte:
    """Vacaciones, día libre, permiso o incapacidad registrados.

    Atributos:
        empleado_id: A quién.
        tipo: ``TipoAusencia.*.value``.
        desde / hasta: Rango completo registrado (ISO, inclusivo).
        nota: Motivo o detalle que escribió quien lo registró.
        registrado_por: Usuario que lo registró (``None`` si no se sabe).
        registrado_el: Momento del registro (ISO UTC) o ``None``.
    """

    empleado_id: int
    tipo: str
    desde: str
    hasta: str
    nota: Optional[str] = None
    registrado_por: Optional[str] = None
    registrado_el: Optional[str] = None


@dataclass(frozen=True)
class ExtrasReporte:
    """Lo que el reporte detallado muestra además de las asistencias.

    Atributos:
        empleados: ``empleado_id → EmpleadoReporte``.
        marcas: TODAS las marcas guardadas del período (y del empleado, si
            se filtró), ordenadas por momento.
        horarios: ``(turno_id, día de la semana 0=lunes) → HorarioDia``.
        notas_ausencia: ``(empleado_id, fecha ISO) → "Vacaciones: nota"``
            para los días cubiertos por vacaciones o permisos.
        ausencias: Vacaciones y permisos registrados que tocan el período.
    """

    empleados: Dict[int, EmpleadoReporte] = field(default_factory=dict)
    marcas: List[MarcaReporte] = field(default_factory=list)
    horarios: Dict[Tuple[int, int], HorarioDia] = field(default_factory=dict)
    notas_ausencia: Dict[Tuple[int, str], str] = field(default_factory=dict)
    ausencias: List[AusenciaReporte] = field(default_factory=list)


@dataclass(frozen=True)
class DatosReporteAsistencia:
    """Bundle de datos listos para exportar a un archivo.

    Lo arma ``ReporteService`` y lo consume ``IAsistenciaExporter``.
    Es ``frozen`` porque el exporter no debe mutar el contenido.

    Atributos:
        items: Filas crudas enriquecidas (una por empleado-fecha).
        resumen_por_empleado: Resumen agregado, ordenado por nombre.
        rango_desde: ISO ``YYYY-MM-DD``, inclusivo.
        rango_hasta: ISO ``YYYY-MM-DD``, inclusivo.
        empleado_id_filtro: Si se filtró por un empleado específico,
            su id; ``None`` si el reporte abarca a todos.
        empleado_nombre_filtro: Nombre del empleado filtrado para
            mostrar en la hoja de Metadatos. ``None`` cuando no hay
            filtro.
        actor_username: Usuario que generó el reporte (para Metadatos).
        timestamp_iso: Marca temporal del reporte (UTC ISO-8601).
        generado_local: Fecha y hora local de generación
            (``"YYYY-MM-DDTHH:MM:SS"``) para mostrar; ``None`` usa la UTC.
        extras: Ficha de empleados, marcas, horarios y notas (vacío si
            el servicio no tiene de dónde sacarlos).
    """

    items: List[AsistenciaVista]
    resumen_por_empleado: List[ResumenEmpleado]
    rango_desde: str
    rango_hasta: str
    empleado_id_filtro: Optional[int]
    empleado_nombre_filtro: Optional[str]
    actor_username: str
    timestamp_iso: str
    generado_local: Optional[str] = None
    extras: ExtrasReporte = field(default_factory=ExtrasReporte)


class IAsistenciaExporter(ABC):
    """Interfaz de exportador de reportes de asistencia.

    Cada implementación (xlsx, csv, pdf) decide su propio layout. El
    contrato común es: "recibe datos + ruta destino, escribe el archivo
    y devuelve cuántas filas de detalle escribió".
    """

    @abstractmethod
    def exportar(self, datos: DatosReporteAsistencia, output_path: str) -> int:
        """Escribe el archivo con los datos recibidos.

        Args:
            datos: Bundle ya armado por ``ReporteService``.
            output_path: Ruta absoluta del archivo a generar. La
                implementación lo crea (sobrescribe si existe).

        Returns:
            Cantidad de filas de detalle escritas (== ``len(items)``).
            Útil para registrar en ``descargas_reportes.filas_exportadas``.

        Raises:
            OSError: cualquier fallo de I/O — sin permisos, disco lleno,
                ruta inválida, archivo bloqueado por otro proceso.
                ``ReporteService`` lo traduce a ``ReporteIOError``.
        """


# ── Función pura compartida ──────────────────────────────────────────────────


def calcular_resumen(
    items: List[AsistenciaVista], extras: Optional[ExtrasReporte] = None
) -> List[ResumenEmpleado]:
    """Agrupa por empleado y calcula contadores de días + minutos.

    Función pura: no toca BD ni I/O. Se invoca desde
    ``ReporteService.exportar_asistencia`` y se testea de forma aislada.

    Args:
        items: Filas enriquecidas. Pueden venir desordenadas; el
            resultado se ordena por nombre ascendente.
        extras: Horarios y marcas del período. Con ellos cada resumen
            trae además su ``tiempo`` (atraso, extra, balance, jornada).

    Returns:
        Lista de ``ResumenEmpleado`` ordenada por nombre. Si ``items``
        está vacío, devuelve ``[]``.
    """
    acumulador: Dict[int, _AcumEmpleado] = {}
    for vista in items:
        emp_id = vista.asistencia.empleado_id
        slot = acumulador.get(emp_id)
        if slot is None:
            slot = _AcumEmpleado(
                empleado_nombre=vista.empleado_nombre_completo,
                empleado_dni=vista.empleado_dni,
            )
            acumulador[emp_id] = slot
        _sumar_dia(slot, vista)
    resumenes = [_slot_a_resumen(emp_id, slot) for emp_id, slot in acumulador.items()]
    if extras is not None and extras.empleados:
        por_dia = tiempos_por_dia(items, extras)
        dias: Dict[int, List[Tuple[TiempoDia, Optional[str]]]] = defaultdict(list)
        for vista in items:
            a = vista.asistencia
            dias[a.empleado_id].append((por_dia[(a.empleado_id, a.fecha)], a.hora_entrada_real))
        resumenes = [replace(r, tiempo=sumar_dias(dias[r.empleado_id])) for r in resumenes]
    resumenes.sort(key=lambda r: r.empleado_nombre.lower())
    return resumenes


def horas_marcadas_por_dia(extras: ExtrasReporte) -> Dict[Tuple[int, str], List[str]]:
    """``(empleado_id, fecha ISO) → ["07:58:12", "17:03:40"]`` en orden."""
    por_dia: Dict[Tuple[int, str], List[str]] = defaultdict(list)
    for marca in sorted(extras.marcas, key=lambda m: m.momento):
        if marca.empleado_id is None:
            continue
        dia, hora = marca.momento.split("T", 1)
        por_dia[(marca.empleado_id, dia)].append(hora[:8])
    return por_dia


def tiempos_por_dia(
    items: List[AsistenciaVista], extras: ExtrasReporte
) -> Dict[Tuple[int, str], TiempoDia]:
    """``(empleado_id, fecha ISO) → TiempoDia`` de cada renglón del período."""
    marcas = horas_marcadas_por_dia(extras)
    salidas = {
        (v.asistencia.empleado_id, v.asistencia.fecha): v.asistencia.hora_salida_real for v in items
    }
    tiempos: Dict[Tuple[int, str], TiempoDia] = {}
    for vista in items:
        a = vista.asistencia
        dia = date.fromisoformat(a.fecha)
        horario = (
            extras.horarios.get((a.turno_id_aplicado, dia.weekday()))
            if a.turno_id_aplicado is not None
            else None
        )
        anterior = (dia - timedelta(days=1)).isoformat()
        tiempos[(a.empleado_id, a.fecha)] = tiempo_del_dia(
            a,
            horario,
            marcas.get((a.empleado_id, a.fecha), []),
            salidas.get((a.empleado_id, anterior)),
        )
    return tiempos


# ── Helpers privados de cálculo ──────────────────────────────────────────────


@dataclass
class _AcumEmpleado:
    """Bucket mutable para acumular conteos durante el agrupamiento."""

    empleado_nombre: str
    empleado_dni: Optional[str]
    dias_presente: int = 0
    dias_tarde: int = 0
    dias_salida_temprana: int = 0
    dias_ausente: int = 0
    dias_feriado: int = 0
    dias_sin_turno: int = 0
    dias_incompleto: int = 0
    total_minutos_tarde: int = 0
    total_minutos_salida_temprana: int = 0
    dias_vacaciones: int = 0
    dias_libre: int = 0
    dias_permiso: int = 0
    dias_incapacidad: int = 0


# Mapa estado → atributos del bucket a incrementar. ``TARDE_Y_SALIDA_TEMPRANA``
# es el único caso múltiple: cuenta como tarde y como salida temprana.
_INCREMENTOS_POR_ESTADO: Dict[str, Tuple[str, ...]] = {
    EstadoAsistencia.PRESENTE.value: ("dias_presente",),
    EstadoAsistencia.TARDE.value: ("dias_tarde",),
    EstadoAsistencia.SALIDA_TEMPRANA.value: ("dias_salida_temprana",),
    EstadoAsistencia.TARDE_Y_SALIDA_TEMPRANA.value: (
        "dias_tarde",
        "dias_salida_temprana",
    ),
    EstadoAsistencia.AUSENTE.value: ("dias_ausente",),
    EstadoAsistencia.FERIADO.value: ("dias_feriado",),
    EstadoAsistencia.SIN_TURNO.value: ("dias_sin_turno",),
    EstadoAsistencia.INCOMPLETO.value: ("dias_incompleto",),
    EstadoAsistencia.VACACIONES.value: ("dias_vacaciones",),
    EstadoAsistencia.DIA_LIBRE.value: ("dias_libre",),
    EstadoAsistencia.PERMISO.value: ("dias_permiso",),
    EstadoAsistencia.INCAPACIDAD.value: ("dias_incapacidad",),
}


def _sumar_dia(slot: _AcumEmpleado, vista: AsistenciaVista) -> None:
    """Incrementa los contadores del bucket según el estado del día."""
    asist = vista.asistencia
    for atributo in _INCREMENTOS_POR_ESTADO.get(asist.estado, ()):
        setattr(slot, atributo, getattr(slot, atributo) + 1)
    slot.total_minutos_tarde += asist.minutos_tarde
    slot.total_minutos_salida_temprana += asist.minutos_salida_temprana


def _slot_a_resumen(empleado_id: int, slot: _AcumEmpleado) -> ResumenEmpleado:
    """Convierte el bucket mutable al dataclass público inmutable."""
    return ResumenEmpleado(
        empleado_id=empleado_id,
        empleado_nombre=slot.empleado_nombre,
        empleado_dni=slot.empleado_dni,
        dias_presente=slot.dias_presente,
        dias_tarde=slot.dias_tarde,
        dias_salida_temprana=slot.dias_salida_temprana,
        dias_ausente=slot.dias_ausente,
        dias_feriado=slot.dias_feriado,
        dias_sin_turno=slot.dias_sin_turno,
        dias_incompleto=slot.dias_incompleto,
        total_minutos_tarde=slot.total_minutos_tarde,
        total_minutos_salida_temprana=slot.total_minutos_salida_temprana,
        dias_vacaciones=slot.dias_vacaciones,
        dias_libre=slot.dias_libre,
        dias_permiso=slot.dias_permiso,
        dias_incapacidad=slot.dias_incapacidad,
    )
