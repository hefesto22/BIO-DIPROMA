"""Interfaces del repositorio de EmpleadoTurno.

Se separan en ``IEmpleadoTurnoReadRepository`` e
``IEmpleadoTurnoWriteRepository`` siguiendo ISP. Ambas son ``abc.ABC``
(consistencia con Fase 1).

La tabla ``empleado_turnos`` tiene una semántica temporal: cada empleado
tiene una cadena de asignaciones, y a lo sumo UNA de ellas está vigente
(``fecha_fin IS NULL``). El índice parcial único
``idx_empleado_turnos_vigente_unico`` de la migración 003 garantiza esta
invariante a nivel BD.

El método ``cerrar_vigente_y_asignar`` encapsula el cambio atómico de
turno vigente (cierra la fila anterior + inserta la nueva en la misma
transacción).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from core.models.empleado_turno import EmpleadoTurno


class IEmpleadoTurnoReadRepository(ABC):
    """Operaciones de solo lectura sobre la tabla ``empleado_turnos``."""

    @abstractmethod
    def get_vigente(self, empleado_id: int) -> Optional[EmpleadoTurno]:
        """Devuelve UNA asignación vigente del empleado (compat).

        Sub-3.2.B: ahora un empleado puede tener varias asignaciones
        vigentes (lun-vie + sáb). Esta función devuelve la primera que
        encuentre y se mantiene solo para flujos legacy de "un turno
        único" (``cambiar_turno``). Los flujos nuevos deben usar
        ``list_vigentes``.

        Returns:
            Una fila vigente, o ``None`` si el empleado no tiene
            ninguna asignación abierta. Si hay varias vigentes, el
            criterio de cuál devolver es indeterminado por contrato.
        """

    @abstractmethod
    def list_vigentes(self, empleado_id: int) -> List[EmpleadoTurno]:
        """Devuelve TODAS las asignaciones vigentes del empleado.

        Sub-3.2.B: con la migración 008, un empleado puede tener
        varias asignaciones abiertas que cubren días distintos de la
        semana. Este método las devuelve todas, ordenadas por
        ``fecha_inicio`` ascendente.
        """

    @abstractmethod
    def list_historial(self, empleado_id: int) -> List[EmpleadoTurno]:
        """Devuelve todas las asignaciones del empleado.

        Ordenadas por ``fecha_inicio`` descendente (la más reciente primero).
        """

    @abstractmethod
    def list_by_turno(self, turno_id: int) -> List[EmpleadoTurno]:
        """Devuelve todas las asignaciones asociadas a un turno dado.

        Ordenadas por ``fecha_inicio`` descendente. Útil para responder
        "¿qué empleados tienen este turno vigente o lo tuvieron?" desde
        la UI de turnos.
        """


class IEmpleadoTurnoWriteRepository(ABC):
    """Operaciones de escritura sobre la tabla ``empleado_turnos``.

    El write no expone ``update`` genérico ni ``delete`` — la semántica de
    la tabla es histórica y append-only con un único punto de mutación
    (cerrar una fila vigente al cambiar de turno).
    """

    @abstractmethod
    def asignar(self, empleado_id: int, turno_id: int, fecha_inicio: str) -> EmpleadoTurno:
        """Crea una nueva asignación vigente (``fecha_fin = NULL``).

        Pensado para:
            - La primera asignación de turno a un empleado nuevo.
            - La re-asignación tras reactivar un empleado que estuvo
              archivado (su asignación anterior ya está cerrada).

        Si el empleado ya tiene una asignación vigente, SQLite rechaza la
        inserción vía ``idx_empleado_turnos_vigente_unico`` — en ese caso,
        el servicio debe usar ``cerrar_vigente_y_asignar`` en su lugar.

        Args:
            empleado_id: FK a ``empleados(id)``.
            turno_id: FK a ``turnos(id)``.
            fecha_inicio: ISO ``YYYY-MM-DD``.

        Returns:
            Nueva instancia con el ``id`` ya asignado.

        Raises:
            sqlite3.IntegrityError: Si ya existe una vigente (índice parcial),
                si las FKs no son válidas, o si la tupla
                ``(empleado_id, fecha_inicio)`` ya existe.
        """

    @abstractmethod
    def cerrar_asignacion_por_id(self, asignacion_id: int, fecha_fin: str) -> None:
        """Sub-3.3: cierra una asignación específica por su id.

        Pensado para el flujo "el empleado tiene varios turnos vigentes
        en paralelo y quiero cerrar UNO sin tocar los demás".

        Args:
            asignacion_id: PK de la fila en ``empleado_turnos``.
            fecha_fin: ISO ``YYYY-MM-DD``. Último día efectivo.

        Raises:
            ValueError: Si el id no existe o la asignación ya está cerrada.
            sqlite3.IntegrityError: Si el CHECK ``fecha_fin >= fecha_inicio`` falla.
        """

    @abstractmethod
    def cerrar_vigente(self, empleado_id: int, fecha_fin: str) -> None:
        """Cierra la asignación vigente del empleado sin abrir una nueva.

        Pensado para el flujo de baja: cuando un empleado se desactiva,
        su asignación vigente debe quedar cerrada con
        ``fecha_fin = fecha_baja`` — no se le asigna un turno nuevo.

        Args:
            empleado_id: PK del empleado.
            fecha_fin: ISO ``YYYY-MM-DD``. Último día efectivo del turno.

        Raises:
            ValueError: Si el empleado no tiene una asignación vigente
                (no hay nada que cerrar).
            sqlite3.IntegrityError: Si el CHECK de fechas falla
                (``fecha_fin >= fecha_inicio``).
        """

    @abstractmethod
    def cerrar_vigente_y_asignar(
        self,
        empleado_id: int,
        turno_id_nuevo: int,
        fecha_fin_vigente: str,
        fecha_inicio_nueva: str,
    ) -> EmpleadoTurno:
        """Cambio atómico de turno: cierra la vigente + crea una nueva.

        Ambas operaciones corren dentro de la misma transacción SQLite.
        Si la segunda falla (FK inválida, CHECK de fechas), la primera se
        revierte — el empleado queda con su vigente original intacta.

        Args:
            empleado_id: PK del empleado.
            turno_id_nuevo: FK a ``turnos(id)`` del turno que entra a regir.
            fecha_fin_vigente: ISO ``YYYY-MM-DD``. Último día efectivo del
                turno anterior (típicamente ``fecha_inicio_nueva`` − 1 día,
                pero el servicio decide).
            fecha_inicio_nueva: ISO ``YYYY-MM-DD``. Primer día efectivo del
                turno nuevo. Debe ser mayor que ``fecha_fin_vigente``.

        Returns:
            La nueva ``EmpleadoTurno`` vigente (con ``fecha_fin = None``).

        Raises:
            ValueError: Si el empleado no tiene una asignación vigente
                (no hay nada que cerrar — usar ``asignar`` directamente).
            sqlite3.IntegrityError: Si las FKs son inválidas o el CHECK de
                fechas (``fecha_fin >= fecha_inicio`` en la fila cerrada) falla.
        """
