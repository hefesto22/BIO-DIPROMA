"""Interfaces del repositorio de Feriado.

Se separan en ``IFeriadoReadRepository`` e ``IFeriadoWriteRepository``
siguiendo ISP. Ambas son ``abc.ABC`` (consistencia con Fase 1/2).

Semántica:
    - Tabla plana sin FKs entrantes: un feriado se puede ELIMINAR
      físicamente (``delete``) — no hay registros dependientes que
      proteger. Si un feriado fue registrado por error o la municipalidad
      decide que ya no aplica, el operador lo borra.
    - ``fecha`` es única a nivel BD (un día = un feriado). Si se necesitan
      varios eventos el mismo día, se concatenan en ``descripcion``.
    - Ordenamiento canónico por ``fecha`` ascendente — coincide con el
      orden cronológico natural esperado en la UI.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from core.models.feriado import Feriado


class IFeriadoReadRepository(ABC):
    """Operaciones de solo lectura sobre la tabla ``feriados``."""

    @abstractmethod
    def get_by_id(self, feriado_id: int) -> Optional[Feriado]:
        """Devuelve el feriado con ese id, o ``None`` si no existe."""

    @abstractmethod
    def get_by_fecha(self, fecha: str) -> Optional[Feriado]:
        """Devuelve el feriado registrado en esa fecha, o ``None``.

        Args:
            fecha: ISO ``YYYY-MM-DD``.

        Es el lookup crítico del servicio de consolidación (Fase 3.3):
        para cada día del rango, ``get_by_fecha`` decide si aplica el
        estado ``FERIADO`` en vez de calcular asistencia normal.
        """

    @abstractmethod
    def list_all(self) -> List[Feriado]:
        """Devuelve todos los feriados, ordenados por ``fecha`` ascendente."""

    @abstractmethod
    def list_by_rango(self, desde: str, hasta: str) -> List[Feriado]:
        """Devuelve los feriados dentro del rango inclusive ``[desde, hasta]``.

        Ordenados por ``fecha`` ascendente. Pensado para la consolidación
        mensual: en vez de consultar ``get_by_fecha`` día por día, el
        servicio precarga todos los feriados del rango y hace lookup en
        memoria.

        Args:
            desde: ISO ``YYYY-MM-DD``. Límite inferior inclusivo.
            hasta: ISO ``YYYY-MM-DD``. Límite superior inclusivo.
        """


class IFeriadoWriteRepository(ABC):
    """Operaciones de escritura sobre la tabla ``feriados``."""

    @abstractmethod
    def create(self, feriado: Feriado) -> Feriado:
        """Inserta un feriado nuevo.

        Args:
            feriado: Instancia con ``id=None``. ``fecha`` y ``descripcion``
                requeridos.

        Returns:
            Nueva instancia con el ``id`` ya asignado.

        Raises:
            sqlite3.IntegrityError: Si ya existe un feriado con la misma
                ``fecha`` (UNIQUE).
        """

    @abstractmethod
    def update_descripcion(self, feriado_id: int, nueva_descripcion: str) -> None:
        """Actualiza solo la descripción del feriado.

        La ``fecha`` es inmutable una vez creada — si el operador se
        equivocó de fecha, debe borrar la fila y crear una nueva (así el
        UNIQUE sobre ``fecha`` no se viola temporalmente).
        """

    @abstractmethod
    def delete(self, feriado_id: int) -> None:
        """Borra físicamente el feriado.

        Seguro porque la tabla no tiene FKs entrantes. Si el id no existe,
        no es error (0 filas afectadas).
        """
