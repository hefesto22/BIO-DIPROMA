"""Servicio de verificación de permisos + decorador ``@require_permission``.

Diseño:
    - La fuente de verdad de los permisos es el snapshot en ``Session``
      (capturado al login). Verificar un permiso NO golpea la BD — es
      una operación O(1) contra un ``frozenset`` en memoria.
    - El decorador ``@require_permission('code')`` se aplica a métodos
      de controllers. Espera que el objeto ``self`` tenga un atributo
      ``session`` (``Optional[Session]``) — los controllers que se
      decoren deben cumplir este contrato.
    - Si no hay sesión → ``NotAuthenticatedError``.
    - Si hay sesión pero le falta el permiso → ``PermissionDeniedError``.

El diseño está alineado con la REGLA: "Cada vista usa decorator
``@require_permission('permiso')`` antes de renderizarse".
"""

from __future__ import annotations

import logging
from functools import wraps
from typing import Any, Callable, Optional, TypeVar

from core.services.errors import NotAuthenticatedError, PermissionDeniedError
from core.services.session import Session

F = TypeVar("F", bound=Callable[..., Any])


class PermissionService:
    """Verifica si un permiso concreto está presente en una ``Session``."""

    def __init__(self) -> None:
        self._log = logging.getLogger(self.__class__.__name__)

    def check(self, session: Optional[Session], permission: str) -> None:
        """Verifica que ``session`` tenga ``permission``.

        Args:
            session: Sesión activa, o ``None`` si no hay nadie logueado.
            permission: Código del permiso requerido.

        Raises:
            NotAuthenticatedError: Si ``session`` es ``None``.
            PermissionDeniedError: Si la sesión existe pero no incluye
                el permiso requerido.
        """
        if session is None:
            raise NotAuthenticatedError()
        if not session.has_permission(permission):
            self._log.warning(
                "Permiso denegado: usuario=%s rol=%s intento permiso=%s",
                session.username,
                session.role_code,
                permission,
            )
            raise PermissionDeniedError(permission)


def require_permission(permission: str) -> Callable[[F], F]:
    """Decorador para métodos de controller que requieren un permiso.

    El decorador asume que el objeto ``self`` del método decorado
    expone:
        - ``self.session`` : ``Optional[Session]``
        - ``self.permission_service`` : ``PermissionService``

    Uso:
        class EmpleadosController:
            def __init__(self, session, permission_service, ...):
                self.session = session
                self.permission_service = permission_service

            @require_permission('manage_employees')
            def crear_empleado(self, datos):
                ...

    Raises:
        NotAuthenticatedError: Si no hay sesión.
        PermissionDeniedError: Si la sesión no tiene el permiso.
    """

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            service: PermissionService = self.permission_service
            session: Optional[Session] = self.session
            service.check(session, permission)
            return func(self, *args, **kwargs)

        # El cast a F es seguro: wrapper preserva la firma vía @wraps.
        return wrapper  # type: ignore[return-value]

    return decorator
