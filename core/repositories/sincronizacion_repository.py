"""Interfaces del repositorio de Sincronizacion.

Se separan en ``ISincronizacionReadRepository`` e
``ISincronizacionWriteRepository`` siguiendo ISP. Ambas son ``abc.ABC``.

Semántica de ciclo de vida:
    Una sync empieza en ``EN_CURSO`` al invocarse ``create``. El servicio
    de Fase 3.3 la cierra con ``marcar_ok`` (éxito, guarda fin +
    registros_recibidos) o ``marcar_fallida`` (error, guarda fin +
    error_mensaje). No hay update genérico — los dos verbos explícitos
    hacen el ciclo legible y obligan a que el estado final sea coherente.

Idempotencia/recovery:
    Si el proceso se mata a mitad de una sync, queda una fila ``EN_CURSO``
    sin ``fin``. El servicio puede listar huérfanas (``list_en_curso``) y
    marcarlas como FALLIDA en el siguiente arranque.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from core.models.sincronizacion import Sincronizacion


class ISincronizacionReadRepository(ABC):
    """Operaciones de solo lectura sobre la tabla ``sincronizaciones``."""

    @abstractmethod
    def get_by_id(self, sincronizacion_id: int) -> Optional[Sincronizacion]:
        """Devuelve la sync con ese id, o ``None`` si no existe."""

    @abstractmethod
    def list_recientes(self, limit: int = 50) -> List[Sincronizacion]:
        """Devuelve las syncs más recientes, ordenadas por ``inicio`` desc.

        Args:
            limit: Máximo de filas a devolver. Default 50 alcanza para la
                vista de historial típica.
        """

    @abstractmethod
    def list_by_dispositivo(self, dispositivo_id: int, limit: int = 50) -> List[Sincronizacion]:
        """Devuelve las syncs de un dispositivo específico.

        Ordenadas por ``inicio`` descendente.
        """

    @abstractmethod
    def list_en_curso(self) -> List[Sincronizacion]:
        """Devuelve todas las syncs con ``estado = 'EN_CURSO'``.

        Usado por el arranque del sistema para detectar syncs huérfanas
        (el proceso murió sin cerrar) y marcarlas como FALLIDA.
        """


class ISincronizacionWriteRepository(ABC):
    """Operaciones de escritura sobre la tabla ``sincronizaciones``.

    El write no expone ``update`` genérico — el ciclo de vida se modela
    con verbos explícitos ``create`` → ``marcar_ok`` / ``marcar_fallida``.
    """

    @abstractmethod
    def create(self, sincronizacion: Sincronizacion) -> Sincronizacion:
        """Inserta la cabecera de una sync recién iniciada.

        Args:
            sincronizacion: Instancia con ``id=None``. ``inicio``,
                ``rango_desde`` y ``rango_hasta`` requeridos.
                ``estado`` por default EN_CURSO (respeta lo que venga).
                ``fin``, ``error_mensaje`` deben ser ``None`` al crear.

        Returns:
            Nueva instancia con el ``id`` ya asignado.

        Raises:
            sqlite3.IntegrityError: Si ``dispositivo_id`` no existe,
                ``iniciada_por_user_id`` referencia un usuario borrado
                (permitido por SET NULL, pero acá aún estamos en INSERT),
                o el CHECK ``rango_hasta >= rango_desde`` falla, o el
                ``estado`` no está en ALL_ESTADOS_SINCRONIZACION.
        """

    @abstractmethod
    def marcar_ok(self, sincronizacion_id: int, fin: str, registros_recibidos: int) -> None:
        """Cierra la sync con éxito.

        Actualiza ``fin``, ``registros_recibidos`` y setea
        ``estado = 'OK'``. ``error_mensaje`` queda en ``NULL``.

        Args:
            sincronizacion_id: PK de la sync a cerrar.
            fin: Timestamp ISO-8601 UTC del cierre.
            registros_recibidos: Conteo final de registros descargados.

        Raises:
            ValueError: Si la sync no existe o ``registros_recibidos < 0``.
        """

    @abstractmethod
    def marcar_fallida(self, sincronizacion_id: int, fin: str, error_mensaje: str) -> None:
        """Cierra la sync con fallo.

        Actualiza ``fin``, ``error_mensaje`` y setea ``estado = 'FALLIDA'``.
        ``registros_recibidos`` queda tal como estaba (puede ser > 0 si el
        fallo ocurrió a mitad del pull).

        Args:
            sincronizacion_id: PK de la sync a cerrar.
            fin: Timestamp ISO-8601 UTC del cierre.
            error_mensaje: Descripción libre del fallo (traducido, legible).

        Raises:
            ValueError: Si la sync no existe.
        """
