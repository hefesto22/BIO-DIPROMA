"""Interfaces del repositorio de AuditLog.

La tabla ``audit_log`` es append-only por política del proyecto: se
expone solo ``insert`` (write) y ``list_*`` (read). Nunca update ni
delete, aunque SQLite técnicamente los permita.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List

from core.models.audit_entry import AuditEntry


class IAuditLogReadRepository(ABC):
    """Operaciones de solo lectura sobre la tabla ``audit_log``."""

    @abstractmethod
    def list_recent(self, limit: int) -> List[AuditEntry]:
        """Devuelve las últimas ``limit`` entradas ordenadas por timestamp desc.

        Args:
            limit: Número máximo de entradas a devolver. Debe ser > 0.

        Raises:
            ValueError: Si ``limit`` es <= 0.
        """

    @abstractmethod
    def list_by_user(self, user_id: int) -> List[AuditEntry]:
        """Devuelve todas las entradas de un usuario ordenadas por timestamp desc.

        Si el usuario no existe o no tiene entradas, devuelve lista vacía.
        """

    @abstractmethod
    def last_timestamp_by_user(self, action: str) -> Dict[int, str]:
        """Último ``timestamp`` de ``action`` por usuario: ``{user_id: iso}``.

        Usado para mostrar el "último acceso" (``action="login_ok"``) de
        todos los usuarios con una sola consulta. Los usuarios sin
        entradas de esa acción no aparecen en el dict.
        """


class IAuditLogWriteRepository(ABC):
    """Operaciones de escritura sobre la tabla ``audit_log`` (append-only)."""

    @abstractmethod
    def insert(self, entry: AuditEntry) -> AuditEntry:
        """Inserta una entrada nueva en el log.

        Args:
            entry: ``AuditEntry`` con ``id=None``. Los demás campos deben
                venir completos — el repo no rellena timestamps ni hostnames
                porque estos pueden diferir del momento exacto del evento.

        Returns:
            Nueva ``AuditEntry`` con ``id`` asignado.
        """
