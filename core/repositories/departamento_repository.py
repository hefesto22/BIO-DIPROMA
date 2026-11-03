"""Interfaces del repositorio de Departamento.

Se separan en ``IDepartamentoReadRepository`` e ``IDepartamentoWriteRepository``
siguiendo ISP (regla SOLID-I del proyecto). Mismo patrón y semántica que
``cargo_repository`` — son tablas gemelas por diseño (D3 del PRD de Fase 2).

Ambas interfaces son ``abc.ABC`` (consistencia con Fase 1, ver
``usuario_repository.py``): el proyecto usa inyección explícita y prefiere
mocks por subclase, no ``typing.Protocol``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional, Tuple

from core.models.departamento import Departamento


class IDepartamentoReadRepository(ABC):
    """Operaciones de solo lectura sobre la tabla ``departamentos``."""

    @abstractmethod
    def get_by_id(self, departamento_id: int) -> Optional[Departamento]:
        """Devuelve el departamento con ese id, o ``None`` si no existe."""

    @abstractmethod
    def get_by_nombre(self, nombre: str) -> Optional[Departamento]:
        """Devuelve el departamento con ese nombre, o ``None`` si no existe.

        La búsqueda es case-sensitive (consistente con el UNIQUE de la tabla).
        """

    @abstractmethod
    def list_all(self) -> List[Departamento]:
        """Devuelve todos los departamentos (activos y archivados).

        Ordenados por ``nombre`` ascendente. Útil para la vista
        "mostrar archivados".
        """

    @abstractmethod
    def list_active(self) -> List[Departamento]:
        """Devuelve solo los departamentos con ``is_active = True``.

        Ordenados por ``nombre`` ascendente. Es el método default para
        poblar dropdowns de alta y edición de empleados.
        """


class IDepartamentoWriteRepository(ABC):
    """Operaciones de escritura sobre la tabla ``departamentos``."""

    @abstractmethod
    def create(self, departamento: Departamento) -> Departamento:
        """Inserta un departamento nuevo.

        Args:
            departamento: Instancia con ``id=None``. ``is_active`` se
                respeta (típicamente ``True``).

        Returns:
            Nueva instancia con el ``id`` ya asignado.

        Raises:
            sqlite3.IntegrityError: Si ``nombre`` ya existe (UNIQUE).
        """

    @abstractmethod
    def rename(self, departamento_id: int, nuevo_nombre: str) -> None:
        """Renombra un departamento existente.

        Se expone como método estrecho (no un ``update`` genérico) porque
        ``nombre`` es el único campo mutable del catálogo — ``is_active``
        tiene sus propios verbos ``archive`` / ``unarchive`` para semántica
        explícita.

        Raises:
            sqlite3.IntegrityError: Si ``nuevo_nombre`` colisiona con el UNIQUE.
        """

    @abstractmethod
    def archive(self, departamento_id: int) -> None:
        """Archiva el departamento (``is_active = False``).

        No afecta las FKs de empleados existentes — solo impide que aparezca
        en dropdowns de alta/edición. Si el id no existe, no es error
        (0 filas afectadas — consistente con SQL).
        """

    @abstractmethod
    def unarchive(self, departamento_id: int) -> None:
        """Reactiva el departamento (``is_active = True``)."""

    @abstractmethod
    def archive_moviendo(self, origen_id: int, destino_id: int) -> Tuple[int, int]:
        """Pasa todo lo del departamento ``origen_id`` a ``destino_id`` y lo archiva.

        En UNA transacción: sus cargos propios (activos o no) y sus
        empleados ACTIVOS pasan al destino, y el origen queda archivado.
        Devuelve ``(empleados_movidos, cargos_movidos)``.
        """
