"""Interfaces del repositorio de Dispositivo.

Se separan en ``IDispositivoReadRepository`` e
``IDispositivoWriteRepository`` siguiendo ISP (regla SOLID-I del proyecto).
Ambas son ``abc.ABC`` (consistencia con Fase 1 y Fase 2).

Semántica:
    - Archivado lógico vía ``is_active`` — nunca se borran dispositivos
      porque ``sincronizaciones`` y ``registros_raw`` los referencian con
      ``ON DELETE RESTRICT`` para preservar el historial.
    - ``nombre`` es único; ``(ip, puerto)`` también es único. Así se evita
      que el operador registre dos veces el mismo reloj con etiqueta
      distinta o el mismo endpoint con nombres distintos.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from core.models.dispositivo import Dispositivo


class IDispositivoReadRepository(ABC):
    """Operaciones de solo lectura sobre la tabla ``dispositivos``."""

    @abstractmethod
    def get_by_id(self, dispositivo_id: int) -> Optional[Dispositivo]:
        """Devuelve el dispositivo con ese id, o ``None`` si no existe."""

    @abstractmethod
    def get_by_nombre(self, nombre: str) -> Optional[Dispositivo]:
        """Devuelve el dispositivo con ese nombre, o ``None`` si no existe.

        La búsqueda es case-sensitive (consistente con el UNIQUE de la tabla).
        """

    @abstractmethod
    def get_by_ip_puerto(self, ip: str, puerto: int) -> Optional[Dispositivo]:
        """Devuelve el dispositivo con ese endpoint, o ``None`` si no existe.

        Útil para validar duplicados antes de crear y para resolver un
        dispositivo desde la IP/puerto que reporta el reloj físico.
        """

    @abstractmethod
    def get_by_numero_serie(self, numero_serie: str) -> Optional[Dispositivo]:
        """Busca por número de serie (así se reconoce a un reloj PUSH)."""

    @abstractmethod
    def list_all(self) -> List[Dispositivo]:
        """Devuelve todos los dispositivos (activos y archivados).

        Ordenados por ``nombre`` ascendente. Útil para la vista
        "mostrar archivados" de la UI de Configuración.
        """

    @abstractmethod
    def list_active(self) -> List[Dispositivo]:
        """Devuelve solo los dispositivos con ``is_active = True``.

        Ordenados por ``nombre`` ascendente. Es el método default para
        poblar el combo de "¿qué reloj sincronizar?" en la UI.
        """


class IDispositivoWriteRepository(ABC):
    """Operaciones de escritura sobre la tabla ``dispositivos``."""

    @abstractmethod
    def create(self, dispositivo: Dispositivo) -> Dispositivo:
        """Inserta un dispositivo nuevo.

        Args:
            dispositivo: Instancia con ``id=None``. ``is_active`` se respeta
                (típicamente ``True`` al crear).

        Returns:
            Nueva instancia con el ``id`` ya asignado.

        Raises:
            sqlite3.IntegrityError: Si ``nombre`` o ``(ip, puerto)`` ya
                existen, o si ``puerto`` viola el CHECK (1..65535).
        """

    @abstractmethod
    def update(self, dispositivo: Dispositivo) -> None:
        """Actualiza nombre, ip y puerto del dispositivo.

        NO toca ``is_active`` — para eso existen ``archive`` y ``unarchive``
        con semántica explícita.

        Args:
            dispositivo: Instancia con ``id`` ya asignado.

        Raises:
            ValueError: Si ``dispositivo.id is None``.
            sqlite3.IntegrityError: Si ``nombre`` o ``(ip, puerto)`` nuevos
                colisionan con otra fila.
        """

    @abstractmethod
    def archive(self, dispositivo_id: int) -> None:
        """Archiva el dispositivo (``is_active = False``).

        No afecta las FKs de sincronizaciones ni registros_raw existentes.
        Si el id no existe, no es error (0 filas afectadas — consistente
        con SQL).
        """

    @abstractmethod
    def unarchive(self, dispositivo_id: int) -> None:
        """Reactiva el dispositivo (``is_active = True``)."""

    @abstractmethod
    def registrar_contacto(
        self, dispositivo_id: int, cuando: str, push_stamp: Optional[str] = None
    ) -> None:
        """Guarda la hora del último contacto push (y la marca de agua, si viene)."""
