"""Modelo de dominio: Usuario."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Usuario:
    """Usuario del sistema.

    Nota de diseño: ``Usuario`` NO es ``frozen`` a propósito. Varios flujos
    (registrar intento fallido, resetear contador tras login ok, actualizar
    hash al cambiar contraseña) lo mutan antes de persistir. El hash nunca
    se expone en UI — el controlador debe filtrarlo al construir DTOs.

    Atributos:
        id: PK en la tabla ``usuarios``. ``None`` si aún no fue persistido.
        username: Nombre único, case-sensitive.
        password_hash: Hash bcrypt (formato ``$2b$...``). Nunca texto plano.
        full_name: Nombre completo para mostrar en UI.
        role_id: FK a ``roles.id``.
        is_active: ``False`` desactiva el login sin borrar el registro.
        failed_attempts: Intentos fallidos consecutivos. Se resetea en login ok.
        locked_until: ISO-8601 UTC hasta cuando la cuenta está bloqueada.
                      ``None`` significa cuenta no bloqueada.
        created_at: ISO-8601 UTC.
        updated_at: ISO-8601 UTC.
    """

    id: Optional[int]
    username: str
    password_hash: str
    full_name: str
    role_id: int
    is_active: bool = True
    failed_attempts: int = 0
    locked_until: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
