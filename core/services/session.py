"""Modelo de sesión activa en memoria.

Una ``Session`` representa al usuario logueado en este momento. Por
política del proyecto:

    - Solo existe UNA sesión activa a la vez (la app es monousuario
      local, no servidor multi-cliente).
    - La sesión vive EN MEMORIA. Cerrar la app = perder la sesión.
      No se persiste en archivo ni en BD.
    - Al hacer login, se captura un "snapshot" de los permisos del
      rol en ese momento. Si un ADMIN cambia permisos del rol, el
      usuario debe re-loguearse para verlos reflejados. Esto es una
      decisión consciente: evita consultas extra en cada verificación
      y la app no tiene un flujo de "permisos en tiempo real".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet


@dataclass(frozen=True)
class Session:
    """Snapshot inmutable del usuario activo.

    Es ``frozen`` a propósito: si la sesión cambia (logout, re-login),
    se reemplaza por una instancia nueva — nunca se muta.

    Atributos:
        user_id: PK del usuario en la tabla ``usuarios``.
        username: Nombre para mostrar y para auditoría.
        role_id: PK del rol, útil para queries sin resolver el code.
        role_code: Código canónico (SUPERADMIN, ADMIN, REPORTES, OPERADOR).
        permissions: Set inmutable de códigos de permiso del rol al momento
                     del login.
    """

    user_id: int
    username: str
    role_id: int
    role_code: str
    permissions: FrozenSet[str]

    def has_permission(self, permission: str) -> bool:
        """Devuelve ``True`` si el rol de la sesión incluye ese permiso.

        No lanza excepción — solo contesta sí/no. El manejo de
        ``PermissionDeniedError`` es responsabilidad de ``PermissionService``.
        """
        return permission in self.permissions
