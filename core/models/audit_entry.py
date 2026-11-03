"""Modelo de dominio: AuditEntry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AuditEntry:
    """Entrada del log de auditoría (append-only).

    La tabla ``audit_log`` es append-only por política: el repositorio
    expone solo ``insert`` y ``select``. Por eso esta dataclass es
    ``frozen`` — una entrada nunca se modifica después de crearse.

    Atributos:
        id: PK. ``None`` si aún no fue persistido.
        user_id: ID del usuario que ejecutó la acción.
                 ``None`` si no se pudo identificar (p. ej. intento de login
                 con un username inexistente).
        action: Código de acción. Ejemplos: ``login_ok``, ``login_fail``,
                ``user_created``, ``password_changed``, ``export_reports``.
        machine_name: Hostname de la máquina donde ocurrió la acción
                      (``socket.gethostname()``).
        timestamp: ISO-8601 UTC, con precisión de segundos.
        details: JSON serializado opcional con info extra (p. ej.
                 ``{"username_intentado": "pepe"}``). ``None`` si no aplica.
    """

    id: Optional[int]
    user_id: Optional[int]
    action: str
    machine_name: str
    timestamp: str
    details: Optional[str] = None
