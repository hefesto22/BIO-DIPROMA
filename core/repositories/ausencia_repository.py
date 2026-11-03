"""Interfaces del repositorio de Ausencia (vacaciones, días libres, permisos).

Se separan en ``IAusenciaReadRepository`` e ``IAusenciaWriteRepository``
siguiendo ISP. Ambas son ``abc.ABC`` (consistencia con el resto).

Semántica:
    - Un registro es un rango de fechas inclusivo ``[fecha_desde, fecha_hasta]``.
    - Se pueden ELIMINAR físicamente (``delete``): no hay filas que
      dependan de ellas; las asistencias se recalculan al borrar.
    - Orden canónico: ``fecha_desde`` descendente (lo más reciente arriba).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from core.models.ausencia import Ausencia


class IAusenciaReadRepository(ABC):
    """Operaciones de solo lectura sobre la tabla ``ausencias``."""

    @abstractmethod
    def get_by_id(self, ausencia_id: int) -> Optional[Ausencia]:
        """Devuelve la ausencia con ese id, o ``None`` si no existe."""

    @abstractmethod
    def list_all(self) -> List[Ausencia]:
        """Todas las ausencias, ordenadas por ``fecha_desde`` descendente."""

    @abstractmethod
    def list_by_empleado(self, empleado_id: int) -> List[Ausencia]:
        """Las ausencias del empleado, ordenadas por ``fecha_desde`` descendente."""

    @abstractmethod
    def list_en_rango(self, desde: str, hasta: str) -> List[Ausencia]:
        """Ausencias que comparten al menos un día con ``[desde, hasta]``.

        Pensado para la consolidación: precarga las del rango y resuelve
        cada día en memoria.
        """


class IAusenciaWriteRepository(ABC):
    """Operaciones de escritura sobre la tabla ``ausencias``."""

    @abstractmethod
    def create(self, ausencia: Ausencia) -> Ausencia:
        """Inserta la ausencia (``id=None``) y la devuelve con su ``id``.

        Raises:
            sqlite3.IntegrityError: FK de empleado inexistente o un CHECK
                (tipo desconocido, rango invertido).
        """

    @abstractmethod
    def update(self, ausencia: Ausencia) -> None:
        """Sobrescribe tipo, fechas y nota. No toca ``creado_*``.

        Raises:
            ValueError: si ``ausencia.id is None``.
        """

    @abstractmethod
    def delete(self, ausencia_id: int) -> None:
        """Borra la ausencia. No falla si ya no existe."""
