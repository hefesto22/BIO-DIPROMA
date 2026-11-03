"""Interfaces del repositorio de Asistencia.

Se separan en ``IAsistenciaReadRepository`` e
``IAsistenciaWriteRepository`` siguiendo ISP.

Semántica clave — UPSERT idempotente:
    ``upsert`` usa ``ON CONFLICT(empleado_id, fecha) DO UPDATE`` para
    que el servicio de consolidación pueda re-procesar el mismo día sin
    duplicar filas. La re-consolidación sobrescribe los campos
    calculados (estado, horas, minutos, turno_id_aplicado,
    consolidada_en) pero PRESERVA ``observaciones`` — son anotaciones
    manuales del operador que no deben perderse cuando cambia una
    tolerancia o turno.

Uso esperado:
    - El servicio de consolidación llama ``upsert`` una vez por
      empleado por fecha.
    - La UI de Fase 4 usará ``update_observaciones`` para anotaciones
      manuales sin re-consolidar.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from core.models.asistencia import Asistencia


class IAsistenciaReadRepository(ABC):
    """Operaciones de solo lectura sobre la tabla ``asistencias``."""

    @abstractmethod
    def get_by_empleado_y_fecha(self, empleado_id: int, fecha: str) -> Optional[Asistencia]:
        """Devuelve la asistencia de un empleado para un día específico.

        Args:
            empleado_id: FK a ``empleados(id)``.
            fecha: ISO ``YYYY-MM-DD``.

        Returns:
            La fila consolidada, o ``None`` si aún no se ha consolidado
            ese día para ese empleado.
        """

    @abstractmethod
    def list_by_empleado_y_rango(
        self,
        empleado_id: int,
        desde: str,
        hasta: str,
        limit: Optional[int] = None,
    ) -> List[Asistencia]:
        """Devuelve las asistencias de un empleado en un rango inclusive.

        Ordenadas por ``fecha`` ascendente. Pensado para el reporte
        individual por empleado.

        Args:
            empleado_id: FK a ``empleados(id)``.
            desde: ISO ``YYYY-MM-DD``. Límite inferior inclusivo.
            hasta: ISO ``YYYY-MM-DD``. Límite superior inclusivo.
            limit: Si se pasa, corta el resultado a los primeros N
                registros (tras el ORDER BY). Pensado para UIs que
                quieran detectar truncamiento pidiendo ``N+1`` filas.
                ``None`` trae todas las filas.
        """

    @abstractmethod
    def list_by_fecha(self, fecha: str) -> List[Asistencia]:
        """Devuelve todas las asistencias de un día.

        Ordenadas por ``empleado_id`` ascendente. Pensado para el reporte
        "día X" que lista a todos los empleados.
        """

    @abstractmethod
    def list_by_rango(
        self,
        desde: str,
        hasta: str,
        limit: Optional[int] = None,
    ) -> List[Asistencia]:
        """Devuelve todas las asistencias en un rango inclusive.

        Ordenadas por ``fecha`` ascendente + ``empleado_id`` ascendente.
        Pensado para reportes mensuales de toda la municipalidad.

        Args:
            desde: ISO ``YYYY-MM-DD``. Límite inferior inclusivo.
            hasta: ISO ``YYYY-MM-DD``. Límite superior inclusivo.
            limit: Si se pasa, corta el resultado a los primeros N
                registros (tras el ORDER BY). Pensado para UIs que
                quieran detectar truncamiento pidiendo ``N+1`` filas.
                ``None`` trae todas las filas.
        """

    @abstractmethod
    def primera_fecha(self, empleado_id: Optional[int] = None) -> Optional[str]:
        """Fecha ISO de la asistencia más antigua (de un empleado o de todos).

        ``None`` si no hay ninguna. Sirve para el período "Todo" del reporte.
        """

    @abstractmethod
    def count_by_rango(
        self,
        desde: str,
        hasta: str,
        empleado_id: Optional[int] = None,
    ) -> int:
        """Cuenta asistencias en un rango (opcionalmente filtrado por empleado).

        Pensado para que la UI de reportes muestre una confirmación
        previa al export ("Esto generará ~7800 filas, ¿continuar?")
        sin tener que materializar el resultado completo en memoria.

        Args:
            desde: ISO ``YYYY-MM-DD``. Límite inferior inclusivo.
            hasta: ISO ``YYYY-MM-DD``. Límite superior inclusivo.
            empleado_id: Si se pasa, cuenta solo las filas de ese
                empleado. ``None`` cuenta todas las del rango.

        Returns:
            Conteo entero >= 0.
        """


class IAsistenciaWriteRepository(ABC):
    """Operaciones de escritura sobre la tabla ``asistencias``.

    No hay ``delete`` explícito — las filas se mantienen en histórico.
    La re-consolidación sobrescribe vía UPSERT sin crear filas huérfanas.
    """

    @abstractmethod
    def upsert(self, asistencia: Asistencia) -> Asistencia:
        """Inserta o actualiza la asistencia del (empleado, fecha).

        Si no existe fila previa con esa tupla (empleado_id, fecha), la
        inserta. Si existe, actualiza los campos calculados manteniendo
        ``observaciones`` intactas (regla de consolidación idempotente).

        Args:
            asistencia: Instancia completa. ``id`` se ignora — la BD
                asigna o reutiliza según el conflicto. ``observaciones``
                SOLO se guarda si la fila es nueva; en actualización se
                preserva lo que ya había.

        Returns:
            La fila resultante con el ``id`` efectivo (recién insertada
            o la que ya existía).

        Raises:
            sqlite3.IntegrityError: Si ``empleado_id`` no existe,
                ``turno_id_aplicado`` no existe, ``estado`` no está en
                el enum, o algún CHECK de minutos falla (negativos).
        """

    @abstractmethod
    def update_observaciones(self, asistencia_id: int, observaciones: Optional[str]) -> None:
        """Actualiza solo el campo ``observaciones``.

        Pensado para la UI de Fase 4: el operador puede anotar
        justificaciones manualmente sin disparar una re-consolidación.

        Args:
            asistencia_id: PK de la fila a actualizar.
            observaciones: Texto libre o ``None`` para limpiar.

        Raises:
            ValueError: Si el id no existe.
        """
