"""Servicio de solo lectura: actividad de los usuarios del sistema.

Alimenta el módulo "Usuarios y roles" con:

    - el último inicio de sesión de cada usuario (columna "Último acceso"),
    - la actividad reciente de un usuario (panel de detalle).

Todo sale del ``audit_log`` (append-only), así que no hace falta ninguna
columna nueva en ``usuarios`` ni migración.

Diseño (SOLID):
    - **S**: solo consulta; las mutaciones viven en ``UsuarioAdminService``.
    - **D**: recibe el repositorio de lectura del audit log por inyección.
"""

from __future__ import annotations

from typing import Dict, List

from core.models.audit_entry import AuditEntry
from core.repositories.audit_log_repository import IAuditLogReadRepository

# Acción que el ``AuthService`` registra en cada login correcto.
ACCION_LOGIN_OK = "login_ok"


class UsuarioActividadService:
    """Consultas de actividad de usuarios sobre el audit log."""

    def __init__(self, audit_read: IAuditLogReadRepository) -> None:
        self._audit_read = audit_read

    def ultimos_accesos(self) -> Dict[int, str]:
        """``{user_id: timestamp ISO UTC}`` del último login correcto de cada usuario."""
        return self._audit_read.last_timestamp_by_user(ACCION_LOGIN_OK)

    def actividad_reciente(self, user_id: int, limit: int = 10) -> List[AuditEntry]:
        """Últimas ``limit`` acciones hechas por el usuario, de la más reciente a la más vieja.

        Raises:
            ValueError: Si ``limit`` es <= 0.
        """
        if limit <= 0:
            raise ValueError(f"limit debe ser > 0, recibido: {limit}")
        return self._audit_read.list_by_user(user_id)[:limit]
