"""Servicio de auditoría.

Envuelve ``IAuditLogWriteRepository`` agregando automáticamente:
    - timestamp UTC (ISO-8601)
    - hostname de la máquina (``socket.gethostname()``)

El caller solo pasa ``user_id``, ``action`` y opcionalmente
``details`` (JSON serializado). Esto asegura que todas las entradas
tengan timestamp consistente y no dependan del reloj del caller.

Diseño (SRP):
    - AuditLogger NO decide qué es auditable — el servicio que invoca
      (AuthService, etc.) decide cuándo llamar a ``log()``.
    - AuditLogger NO lee ni filtra entradas — eso vive en
      ``IAuditLogReadRepository``.
"""

from __future__ import annotations

import logging
import socket
from datetime import datetime, timezone
from typing import Optional

from core.models.audit_entry import AuditEntry
from core.repositories.audit_log_repository import IAuditLogWriteRepository


class AuditLogger:
    """Servicio para registrar acciones auditables (append-only)."""

    def __init__(
        self,
        audit_repo: IAuditLogWriteRepository,
        machine_name: Optional[str] = None,
    ) -> None:
        """Inicializa el logger.

        Args:
            audit_repo: Repositorio de escritura inyectado.
            machine_name: Hostname a registrar en cada entrada. Si es
                ``None`` se captura una vez vía ``socket.gethostname()``.
                Se permite override para tests y para entornos donde el
                hostname del SO no sea informativo.
        """
        self._repo = audit_repo
        self._machine_name = machine_name or socket.gethostname()
        self._log = logging.getLogger(self.__class__.__name__)

    @property
    def machine_name(self) -> str:
        """Hostname usado en los registros (expuesto para tests)."""
        return self._machine_name

    def log(
        self,
        action: str,
        user_id: Optional[int],
        details: Optional[str] = None,
    ) -> AuditEntry:
        """Registra una entrada en el log de auditoría.

        Args:
            action: Código corto de la acción (``login_ok``, ``login_fail``,
                ``password_changed``, etc.).
            user_id: ID del usuario. ``None`` para eventos sin usuario
                identificado (p. ej. login_fail con username desconocido).
            details: JSON serializado opcional con contexto extra.

        Returns:
            La entrada persistida con ``id`` asignado.
        """
        entry = AuditEntry(
            id=None,
            user_id=user_id,
            action=action,
            machine_name=self._machine_name,
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            details=details,
        )
        return self._repo.insert(entry)
