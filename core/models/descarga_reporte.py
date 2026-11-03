"""Modelo de dominio: DescargaReporte.

Una fila por archivo .xlsx que el sistema generó exitosamente. La
inserción es responsabilidad de ``ReporteService`` tras escribir el
archivo en disco — si el export falla, no se inserta (la tabla refleja
artefactos reales).

Diseño:
    - El modelo es ``@dataclass`` puro, sin imports externos (regla
      del proyecto: ``core/models`` solo stdlib).
    - El enum ``TipoReporte`` se hereda de ``str`` para comparación
      natural contra el TEXT persistido en BD (mismo patrón que
      ``EstadoAsistencia``, ``TipoMarcada``, etc.).
    - ``user_id`` es ``Optional`` porque la FK usa ON DELETE SET NULL
      — el historial sobrevive al borrado del usuario.
    - ``empleado_id_filtro`` es ``Optional`` porque ``None`` significa
      "el reporte cubrió a todos los empleados activos".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final, FrozenSet, Optional


class TipoReporte(str, Enum):
    """Catálogo cerrado de tipos de reporte exportable.

    Hoy solo existe ``ASISTENCIA``. Futuras sub-fases pueden añadir
    ``INDIVIDUAL``, ``POR_DEPARTAMENTO``, etc. — cada nuevo valor
    requiere también añadirlo al CHECK de la migración 005.
    """

    ASISTENCIA = "ASISTENCIA"


ALL_TIPOS_REPORTE: Final[FrozenSet[str]] = frozenset(t.value for t in TipoReporte)


@dataclass
class DescargaReporte:
    """Fila persistida del historial de descargas.

    Atributos:
        id: PK en la tabla ``descargas_reportes``. ``None`` si aún no
            fue persistida.
        user_id: FK al usuario que generó el reporte. ``None`` si el
            usuario fue eliminado posteriormente (FK SET NULL).
        fecha_hora_utc: Timestamp ISO-8601 UTC de cuándo se generó.
        tipo_reporte: Uno de ``TipoReporte.*.value``.
        rango_desde: ISO ``YYYY-MM-DD`` inclusivo del rango exportado.
        rango_hasta: ISO ``YYYY-MM-DD`` inclusivo del rango exportado.
        empleado_id_filtro: FK al empleado cuando el reporte fue
            filtrado a uno solo. ``None`` cuando cubrió a todos.
        ruta_archivo: Ruta absoluta del .xlsx generado en el momento
            de la descarga. El archivo puede haber sido movido/borrado
            por el usuario después; la ruta queda como referencia
            histórica, no como handle activo.
        filas_exportadas: Conteo de filas de detalle escritas (sin
            contar headers ni hojas auxiliares). >= 0.
    """

    id: Optional[int]
    user_id: Optional[int]
    fecha_hora_utc: str
    tipo_reporte: str
    rango_desde: str
    rango_hasta: str
    ruta_archivo: str
    filas_exportadas: int
    empleado_id_filtro: Optional[int] = None
