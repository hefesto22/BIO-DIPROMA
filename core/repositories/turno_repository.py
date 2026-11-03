"""Interfaces del repositorio de Turno.

Se separan en ``ITurnoReadRepository`` e ``ITurnoWriteRepository`` siguiendo
ISP (regla SOLID-I del proyecto). Ambas son ``abc.ABC`` (consistencia con
Fase 1).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from core.models.turno import Turno


class ITurnoReadRepository(ABC):
    """Operaciones de solo lectura sobre la tabla ``turnos``."""

    @abstractmethod
    def get_by_id(self, turno_id: int) -> Optional[Turno]:
        """Devuelve el turno con ese id, o ``None`` si no existe."""

    @abstractmethod
    def get_by_nombre(self, nombre: str) -> Optional[Turno]:
        """Devuelve el turno con ese nombre, o ``None`` si no existe.

        La búsqueda es case-sensitive (consistente con el UNIQUE de la tabla).
        """

    @abstractmethod
    def list_all(self) -> List[Turno]:
        """Devuelve todos los turnos (activos y archivados).

        Ordenados por ``nombre`` ascendente.
        """

    @abstractmethod
    def list_active(self) -> List[Turno]:
        """Devuelve solo los turnos con ``is_active = True``.

        Ordenados por ``nombre`` ascendente. Es el método default para
        poblar dropdowns de asignación a empleados.
        """


class ITurnoWriteRepository(ABC):
    """Operaciones de escritura sobre la tabla ``turnos``."""

    @abstractmethod
    def create(self, turno: Turno) -> Turno:
        """Inserta un turno nuevo junto con sus ``horarios_extra``.

        Args:
            turno: Instancia con ``id=None``. El caller es responsable de
                haber seteado ``cruza_medianoche`` según las horas; el repo
                solo persiste lo que recibe (la lógica vive en el servicio).

        Returns:
            Nueva instancia con el ``id`` ya asignado.

        Raises:
            sqlite3.IntegrityError: Si ``nombre`` ya existe o un CHECK falla
                (``dias_semana`` fuera de 0..127, ``minutos_descanso`` < 0).
        """

    @abstractmethod
    def update(self, turno: Turno) -> None:
        """Actualiza todos los campos mutables del turno.

        Args:
            turno: Instancia con ``id`` ya asignado. Se sobrescriben
                ``nombre``, ``hora_entrada``, ``hora_salida``,
                ``minutos_descanso``, ``dias_semana``, ``cruza_medianoche``,
                ``is_active`` y los ``horarios_extra`` (se reemplazan todos).

        Raises:
            ValueError: Si ``turno.id is None``.
            sqlite3.IntegrityError: Si el ``nombre`` nuevo colisiona con
                otro turno o un CHECK falla.
        """

    @abstractmethod
    def archive(self, turno_id: int) -> None:
        """Archiva el turno (``is_active = False``).

        Preserva las asignaciones históricas en ``empleado_turnos`` — solo
        impide que el turno aparezca en dropdowns de nueva asignación.
        """

    @abstractmethod
    def unarchive(self, turno_id: int) -> None:
        """Reactiva el turno (``is_active = True``)."""
