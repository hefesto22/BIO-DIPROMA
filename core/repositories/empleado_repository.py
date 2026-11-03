"""Interfaces del repositorio de Empleado.

Se separan en ``IEmpleadoReadRepository`` e ``IEmpleadoWriteRepository``
siguiendo ISP (regla SOLID-I del proyecto). Ambas son ``abc.ABC``
(consistencia con Fase 1).

Convenciones de escritura:
    - ``create`` y ``update`` solo tocan campos de identidad, contacto,
      FKs a catálogos, ``fecha_ingreso`` y ``zkteco_id``.
    - Los campos de baja (``is_active`` + trío ``fecha_baja`` / ``motivo_baja``
      / ``nota_baja``) se manipulan vía ``deactivate`` / ``reactivate``, que
      garantizan la coherencia exigida por el CHECK constraint del schema.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from core.models.empleado import Empleado


class IEmpleadoReadRepository(ABC):
    """Operaciones de solo lectura sobre la tabla ``empleados``."""

    @abstractmethod
    def get_by_id(self, empleado_id: int) -> Optional[Empleado]:
        """Devuelve el empleado con ese id, o ``None`` si no existe."""

    @abstractmethod
    def get_by_dni(self, dni: str) -> Optional[Empleado]:
        """Devuelve el empleado con ese DNI, o ``None`` si no existe.

        La búsqueda es case-sensitive y exacta — el servicio es responsable
        de normalizar el DNI antes (quitar espacios, validar la máscara).
        """

    @abstractmethod
    def get_by_zkteco_id(self, zkteco_id: int) -> Optional[Empleado]:
        """Devuelve el empleado con ese ``zkteco_id``, o ``None`` si no existe.

        Útil para el proceso de sincronización del reloj (Fase 3): el
        reloj reporta ids de su propia numeración; hay que mapearlos al
        empleado de la BD.
        """

    @abstractmethod
    def list_all(self) -> List[Empleado]:
        """Devuelve todos los empleados (activos y archivados).

        Ordenados por ``apellidos, nombres`` ascendente.
        """

    @abstractmethod
    def list_active(self) -> List[Empleado]:
        """Devuelve solo los empleados con ``is_active = True``.

        Ordenados por ``apellidos, nombres`` ascendente. Es el método
        default para la tabla principal de la UI.
        """

    @abstractmethod
    def list_by_departamento(
        self, departamento_id: int, solo_activos: bool = True
    ) -> List[Empleado]:
        """Devuelve los empleados de un departamento.

        Args:
            departamento_id: FK a ``departamentos(id)``.
            solo_activos: Si ``True`` (default), filtra por ``is_active = 1``.
        """

    @abstractmethod
    def list_by_cargo(self, cargo_id: int, solo_activos: bool = True) -> List[Empleado]:
        """Devuelve los empleados con un cargo dado.

        Args:
            cargo_id: FK a ``cargos(id)``.
            solo_activos: Si ``True`` (default), filtra por ``is_active = 1``.
        """


class IEmpleadoWriteRepository(ABC):
    """Operaciones de escritura sobre la tabla ``empleados``."""

    @abstractmethod
    def create(self, empleado: Empleado) -> Empleado:
        """Inserta un empleado nuevo.

        Args:
            empleado: Instancia con ``id=None``. ``created_at`` y
                ``updated_at`` pueden venir en ``None``; el repo los
                rellena con el timestamp UTC actual. El empleado se inserta
                como activo: si llega con ``is_active = False`` o con
                algún campo de baja seteado, el CHECK del schema aborta.

        Returns:
            Nueva instancia con ``id``, ``created_at`` y ``updated_at``
            asignados.

        Raises:
            sqlite3.IntegrityError: Si ``dni`` o ``zkteco_id`` ya existen,
                si las FKs no apuntan a filas válidas, o si el CHECK de
                coherencia falla.
        """

    @abstractmethod
    def update(self, empleado: Empleado) -> None:
        """Actualiza los campos editables del empleado.

        Sobrescribe: ``dni``, ``nombres``, ``apellidos``, ``departamento_id``,
        ``cargo_id``, ``fecha_ingreso``, ``telefono``, ``email``,
        ``zkteco_id``. NO toca ``is_active`` ni los campos de baja — para
        eso existen ``deactivate`` y ``reactivate``.

        Args:
            empleado: Instancia con ``id`` ya asignado.

        Raises:
            ValueError: Si ``empleado.id is None``.
            sqlite3.IntegrityError: Si el ``dni`` o ``zkteco_id`` nuevos
                colisionan con otro empleado, o las FKs son inválidas.
        """

    @abstractmethod
    def deactivate(
        self,
        empleado_id: int,
        fecha_baja: str,
        motivo_baja: str,
        nota_baja: Optional[str],
    ) -> None:
        """Archiva el empleado con trazabilidad de la baja.

        Ejecuta como una sola UPDATE para que el CHECK de coherencia
        transicione atómicamente (no puede haber un paso intermedio
        ``is_active = 0`` con campos de baja en NULL).

        Args:
            empleado_id: PK del empleado a archivar.
            fecha_baja: ISO ``YYYY-MM-DD``.
            motivo_baja: Debe ser uno de ``MotivoBaja.*.value``. El servicio
                es responsable de haberlo validado — el CHECK del schema
                sirve de red de seguridad y rechaza valores desconocidos.
            nota_baja: Obligatoria si ``motivo_baja == 'OTRO'`` (validado
                en servicio). ``None`` aceptable para los otros motivos.

        Raises:
            sqlite3.IntegrityError: Si el CHECK falla (p. ej. motivo inválido).
        """

    @abstractmethod
    def reactivate(self, empleado_id: int) -> None:
        """Reactiva un empleado archivado.

        Limpia ``fecha_baja``, ``motivo_baja`` y ``nota_baja`` y setea
        ``is_active = True`` en una sola UPDATE (el CHECK exige coherencia
        atómica entre los cuatro campos).
        """
