"""Interfaces del repositorio de RegistroRaw.

Se separan en ``IRegistroRawReadRepository`` e
``IRegistroRawWriteRepository`` siguiendo ISP.

Semántica:
    - Tabla inmutable una vez insertada: no hay ``update`` ni ``delete``
      directos. Los borrados ocurren por CASCADE cuando se borra la
      sincronización padre.
    - ``create_bulk`` usa ``INSERT OR IGNORE`` para dedupe defensivo: si
      el mismo registro (dispositivo_id, zkteco_user_id, timestamp) ya
      existe en BD, la nueva inserción se descarta silenciosamente. Esto
      permite re-sincronizar un rango sin duplicar datos.
    - Las queries de lectura están diseñadas para el servicio de
      consolidación (Fase 3.3): recuperar las marcadas de un empleado
      (por zkteco_user_id) en un rango temporal, o listar todas las de
      una sync específica.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional, Sequence

from core.models.registro_raw import RegistroRaw


class IRegistroRawReadRepository(ABC):
    """Operaciones de solo lectura sobre la tabla ``registros_raw``."""

    @abstractmethod
    def get_by_id(self, registro_id: int) -> Optional[RegistroRaw]:
        """Devuelve el registro con ese id, o ``None`` si no existe."""

    @abstractmethod
    def list_by_sincronizacion(self, sincronizacion_id: int) -> List[RegistroRaw]:
        """Devuelve todos los registros de una sync.

        Ordenados por ``timestamp`` ascendente + ``id`` ascendente para
        estabilidad ante marcadas con el mismo timestamp (raro pero
        posible con dos marcadas separadas por menos de 1 segundo, que
        el reloj reporta con el mismo valor).
        """

    @abstractmethod
    def list_by_zkteco_user_y_rango(
        self,
        zkteco_user_id: int,
        dispositivo_id: int,
        desde: str,
        hasta: str,
    ) -> List[RegistroRaw]:
        """Devuelve los registros de un empleado (por ID de reloj) en rango.

        Query crítica del servicio de consolidación: dado un empleado y
        un día, recupera todas sus marcadas para armar la fila de
        asistencia. El rango es inclusive en ambos extremos.

        Args:
            zkteco_user_id: ID del empleado en el reloj (no ``empleado_id``
                de nuestra BD).
            dispositivo_id: ID del reloj. Se filtra explícitamente para
                que marcadas accidentales en otro reloj con el mismo
                ``zkteco_user_id`` no contaminen el resultado.
            desde: ISO-8601 ``YYYY-MM-DDTHH:MM:SS``. Límite inferior inclusivo.
            hasta: ISO-8601 ``YYYY-MM-DDTHH:MM:SS``. Límite superior inclusivo.

        Ordenados por ``timestamp`` ascendente + ``id`` ascendente.
        """

    @abstractmethod
    def list_by_rango(self, desde: str, hasta: str) -> List[RegistroRaw]:
        """Devuelve todas las marcadas del rango, a través de TODOS los dispositivos.

        Pensado para el servicio de consolidación: necesita una pasada
        única sobre todas las marcadas del rango para agruparlas en
        memoria por ``zkteco_user_id`` sin N+1 queries. Un empleado puede
        marcar entrada en un reloj y salida en otro, así que el filtro
        por dispositivo no aplica — se quiere TODO.

        Args:
            desde: ISO-8601 ``"YYYY-MM-DDTHH:MM:SS"``. Inclusivo.
            hasta: ISO-8601 ``"YYYY-MM-DDTHH:MM:SS"``. Inclusivo.

        Returns:
            Lista ordenada por ``zkteco_user_id`` asc, ``timestamp`` asc,
            ``id`` asc — así el servicio puede hacer groupby lineal.
        """

    @abstractmethod
    def count_by_sincronizacion(self, sincronizacion_id: int) -> int:
        """Cuenta cuántos registros pertenecen a una sync.

        Útil para cuadrar el ``registros_recibidos`` de la cabecera de
        sincronización cuando la BD es la fuente de verdad (p.ej. después
        de aplicar dedupe con INSERT OR IGNORE, el conteo real puede ser
        menor al solicitado por el adapter).
        """


class IRegistroRawWriteRepository(ABC):
    """Operaciones de escritura sobre la tabla ``registros_raw``.

    No hay ``update`` ni ``delete`` directos — la tabla es append-only.
    El borrado masivo ocurre vía CASCADE al borrar la sync padre.
    """

    @abstractmethod
    def create_bulk(self, registros: Sequence[RegistroRaw]) -> int:
        """Inserta múltiples registros en un solo batch.

        Usa ``INSERT OR IGNORE`` para aplicar el UNIQUE
        ``(dispositivo_id, zkteco_user_id, timestamp)`` como dedupe
        silencioso: los registros ya existentes se descartan y NO son
        contados en el retorno.

        Args:
            registros: Secuencia de ``RegistroRaw`` con ``id=None``.
                Vacía es aceptable (no-op, retorna 0).

        Returns:
            Cantidad de filas efectivamente insertadas (después de dedupe).
            Puede ser menor que ``len(registros)``.

        Raises:
            sqlite3.IntegrityError: Si algún ``dispositivo_id`` o
                ``sincronizacion_id`` no existe, o si el CHECK de
                ``tipo_marcada`` falla.
        """
