"""Interfaces del repositorio de Cargo.

Gemelo de ``departamento_repository`` — mismo contrato y semántica, distinta
tabla subyacente. Se mantienen separados (en vez de una abstracción
compartida) porque cada uno tiene su propio CRUD en la UI y mezclarlos
acoplaría dos dimensiones del negocio que conviene mantener independientes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from core.models.cargo import Cargo


class ICargoReadRepository(ABC):
    """Operaciones de solo lectura sobre la tabla ``cargos``."""

    @abstractmethod
    def get_by_id(self, cargo_id: int) -> Optional[Cargo]:
        """Devuelve el cargo con ese id, o ``None`` si no existe."""

    @abstractmethod
    def get_by_nombre(self, nombre: str) -> Optional[Cargo]:
        """Devuelve el cargo con ese nombre, o ``None`` si no existe.

        La búsqueda es case-sensitive (consistente con el UNIQUE de la tabla).
        """

    @abstractmethod
    def list_all(self) -> List[Cargo]:
        """Devuelve todos los cargos (activos y archivados).

        Ordenados por ``nombre`` ascendente.
        """

    @abstractmethod
    def list_active(self) -> List[Cargo]:
        """Devuelve solo los cargos con ``is_active = True``.

        Ordenados por ``nombre`` ascendente.
        """

    @abstractmethod
    def list_active_para_departamento(self, departamento_id: int) -> List[Cargo]:
        """Devuelve cargos disponibles para un departamento (Sub-3.2.A).

        Incluye:
            - Cargos globales (``departamento_id IS NULL``).
            - Cargos específicos del departamento dado.

        Excluye archivados. Ordenados por ``nombre`` ascendente.
        """


class ICargoWriteRepository(ABC):
    """Operaciones de escritura sobre la tabla ``cargos``."""

    @abstractmethod
    def create(self, cargo: Cargo) -> Cargo:
        """Inserta un cargo nuevo.

        Persiste también ``cargo.departamento_id`` (puede ser ``None``).

        Returns:
            Nueva instancia con el ``id`` ya asignado.

        Raises:
            sqlite3.IntegrityError: Si ``nombre`` ya existe (UNIQUE) o
                el ``departamento_id`` no apunta a una fila válida.
        """

    @abstractmethod
    def rename(self, cargo_id: int, nuevo_nombre: str) -> None:
        """Renombra un cargo existente.

        Raises:
            sqlite3.IntegrityError: Si ``nuevo_nombre`` colisiona con el UNIQUE.
        """

    @abstractmethod
    def update_departamento(self, cargo_id: int, departamento_id: Optional[int]) -> None:
        """Cambia (o desasigna con ``None``) el departamento del cargo.

        ``None`` lo vuelve a "global" (aplica a cualquier departamento).
        """

    @abstractmethod
    def archive(self, cargo_id: int) -> None:
        """Archiva el cargo (``is_active = False``)."""

    @abstractmethod
    def unarchive(self, cargo_id: int) -> None:
        """Reactiva el cargo (``is_active = True``)."""

    @abstractmethod
    def archive_moviendo(self, origen_id: int, destino_id: int) -> int:
        """Pasa los empleados ACTIVOS del cargo ``origen_id`` a ``destino_id`` y lo archiva.

        Todo en UNA transacción. Devuelve cuántos empleados se movieron.
        """
