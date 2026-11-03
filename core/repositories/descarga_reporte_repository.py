"""Interfaces del repositorio de DescargaReporte.

Se separan en ``IDescargaReporteReadRepository`` e
``IDescargaReporteWriteRepository`` siguiendo ISP. Ambas son ``abc.ABC``.

Semántica append-only:
    No hay ``update`` ni ``delete``. El historial es inmutable —
    cada descarga genera una fila nueva. Si el archivo subyacente se
    mueve o borra del disco, la fila queda con ``ruta_archivo``
    apuntando a algo inexistente: es información histórica, no un
    handle activo.

Uso esperado:
    - ``ReporteService`` invoca ``insert`` tras escribir el .xlsx.
    - La vista de "Historial de descargas" llama ``list_recientes``
      para pintar las últimas N descargas (con paginación trivial vía
      ``limit`` y ``offset`` si la cantidad crece).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from core.models.descarga_reporte import DescargaReporte


class IDescargaReporteReadRepository(ABC):
    """Operaciones de solo lectura sobre la tabla ``descargas_reportes``."""

    @abstractmethod
    def get_by_id(self, descarga_id: int) -> Optional[DescargaReporte]:
        """Devuelve la descarga con ese id, o ``None`` si no existe."""

    @abstractmethod
    def list_recientes(self, limit: int = 50) -> List[DescargaReporte]:
        """Devuelve las descargas más recientes, ordenadas por fecha desc.

        Args:
            limit: Máximo de filas a devolver. Default 50 alcanza para la
                vista de historial típica.
        """

    @abstractmethod
    def list_by_user(self, user_id: int, limit: int = 50) -> List[DescargaReporte]:
        """Devuelve las descargas de un usuario específico, ordenadas desc."""


class IDescargaReporteWriteRepository(ABC):
    """Operaciones de escritura sobre la tabla ``descargas_reportes``.

    Solo expone ``insert`` — el historial es append-only. No hay
    ``update`` ni ``delete``.
    """

    @abstractmethod
    def insert(self, descarga: DescargaReporte) -> DescargaReporte:
        """Inserta una nueva fila de historial.

        Args:
            descarga: Instancia con ``id=None``. Todos los campos
                obligatorios requeridos.

        Returns:
            Nueva instancia con el ``id`` ya asignado.

        Raises:
            sqlite3.IntegrityError: Si ``user_id`` referencia un
                usuario inexistente (al insertar; el SET NULL aplica
                al borrado posterior), si ``empleado_id_filtro``
                referencia un empleado inexistente, si el CHECK
                ``rango_hasta >= rango_desde`` falla, o si
                ``tipo_reporte`` no está en ``ALL_TIPOS_REPORTE``.
        """
