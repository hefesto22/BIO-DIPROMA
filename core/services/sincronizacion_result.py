"""Dataclasses de resultado para ``SincronizacionService`` y ``ConsolidacionService``.

Ambos servicios devuelven objetos estructurados — nunca tuplas anónimas
— para que los controladores de UI puedan inspeccionar campos nombrados
y decidir qué mostrar (toast de éxito, banner de advertencia,
diálogo de error, tabla resumen).

La capa UI compone estos resultados: tras una sincronización + consolidación
in-line (Decisión 2 aprobada), el controller recibe ``ResultadoConsolidacion``
envuelto en ``ResultadoSincronizacion.consolidacion``.

Ninguno de estos dataclasses importa SQLite ni pyzk — viven en ``core`` y
son puros.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class MarcadaDesconocida:
    """Marcada recibida de un ``zkteco_user_id`` no mapeado a ningún empleado.

    Se registra en ``registros_raw`` (preserva auditoría) pero se omite
    de la consolidación. La UI muestra el listado al operador para que
    revise el mapeo (típicamente: empleado nuevo sin ``zkteco_id``
    asignado, o typo al registrarlo).

    Attributes:
        zkteco_user_id: ID reportado por el reloj.
        cantidad_marcadas: Cuántas marcadas del rango pertenecen a ese ID.
    """

    zkteco_user_id: int
    cantidad_marcadas: int


@dataclass
class ResultadoConsolidacion:
    """Resultado de una pasada de consolidación raw → asistencias.

    Attributes:
        asistencias_upsertadas: Total de filas en ``asistencias`` creadas
            o actualizadas por la pasada (cada par empleado/fecha aporta 1).
        empleados_procesados: Cantidad de empleados activos que entraron
            al cálculo (con o sin turno vigente — SIN_TURNO también cuenta).
        marcadas_desconocidas: IDs del reloj que no mapean a empleado —
            se omiten de la consolidación pero quedaron en ``registros_raw``.
        dias_procesados: Cantidad de días distintos dentro del rango
            consolidado (len del rango inclusive).
        errores_empleado: Lista de mensajes para empleados cuyo día falló
            individualmente sin abortar el batch (ej: historial de turnos
            inconsistente). Vacía en operación normal.
    """

    asistencias_upsertadas: int = 0
    empleados_procesados: int = 0
    marcadas_desconocidas: List[MarcadaDesconocida] = field(default_factory=list)
    dias_procesados: int = 0
    errores_empleado: List[str] = field(default_factory=list)


@dataclass
class ResultadoSincronizacion:
    """Resultado completo de una sincronización + consolidación in-line.

    La consolidación corre dentro del mismo método del servicio tras una
    ``marcar_ok`` exitosa (Decisión 2 aprobada: auto-inline con warnings).
    Si la consolidación falla, la sync queda OK igual — los raw están a
    salvo — y el campo ``error_consolidacion`` trae la razón para que la
    UI muestre un banner de advertencia sin ocultar que la descarga sí
    fue exitosa.

    Attributes:
        sincronizacion_id: PK de la fila ``sincronizaciones`` creada.
        dispositivo_id: FK del dispositivo sincronizado.
        registros_recibidos: Cantidad de filas efectivamente insertadas
            en ``registros_raw`` (post-dedupe por UNIQUE).
        consolidacion: Resultado del paso 2 si corrió; ``None`` si la sync
            falló o la consolidación no se intentó.
        error_consolidacion: Mensaje humano si la consolidación lanzó
            excepción tras la sync OK. ``None`` si todo salió bien.
    """

    sincronizacion_id: int
    dispositivo_id: int
    registros_recibidos: int
    consolidacion: Optional[ResultadoConsolidacion] = None
    error_consolidacion: Optional[str] = None
