"""Controller del login (UI).

Orquesta ``AuthService.login`` con despacho asíncrono para no congelar
la UI durante la verificación bcrypt (~250ms por intento con cost=12).

Diseño:
    - El controller NO sabe nada de widgets. Recibe callbacks
      (``on_success``, ``on_error``) y una función ``run_async`` para
      correr el trabajo fuera del UI thread. En tests se inyecta un
      ``run_async`` síncrono para no lidiar con threads.
    - Los errores de login se traducen a mensajes en español listos
      para mostrar. Los mensajes son intencionalmente genéricos para
      ``InvalidCredentialsError`` (REGLA: no revelar user vs password).
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from core.services.auth_service import AuthService
from core.services.errors import (
    AccountInactiveError,
    AccountLockedError,
    InvalidCredentialsError,
)
from core.services.session import Session

# Tipo del runner asíncrono: toma una work fn, un on_success y un on_error.
AsyncRunner = Callable[
    [
        Callable[[], Session],
        Callable[[Session], None],
        Callable[[BaseException], None],
    ],
    Any,
]


class LoginController:
    """Pegamento entre ``LoginWindow`` y ``AuthService``."""

    def __init__(self, auth_service: AuthService, async_runner: AsyncRunner) -> None:
        """Inicializa el controller.

        Args:
            auth_service: Servicio de autenticación inyectado.
            async_runner: Función que despacha ``work`` fuera del UI
                thread y llama los callbacks de vuelta en el UI thread.
                En prod se envuelve ``ui.async_util.run_async_ui``
                pre-curryficado con el widget; en tests se pasa un
                runner síncrono.
        """
        self._auth = auth_service
        self._run_async = async_runner
        self._log = logging.getLogger(self.__class__.__name__)

    def try_login(
        self,
        username: str,
        password: str,
        on_success: Callable[[Session], None],
        on_error: Callable[[str], None],
    ) -> None:
        """Intenta login y despacha el resultado.

        La vista debe deshabilitar el botón de login antes de llamar
        y re-habilitarlo en cualquiera de los dos callbacks.

        Args:
            username: Valor del campo.
            password: Valor del campo.
            on_success: Recibe la ``Session`` en el UI thread.
            on_error: Recibe un mensaje en español en el UI thread.
        """
        if not username or not password:
            on_error("Ingrese usuario y contraseña.")
            return

        def _work() -> Session:
            return self._auth.login(username, password)

        def _on_ok(session: Session) -> None:
            self._log.info("Login OK: usuario=%s rol=%s", session.username, session.role_code)
            on_success(session)

        def _on_err(exc: BaseException) -> None:
            on_error(_mensaje_para_usuario(exc))

        self._run_async(_work, _on_ok, _on_err)


def _mensaje_para_usuario(exc: BaseException) -> str:
    """Traduce excepciones de auth a mensajes en español aptos para UI.

    Los errores conocidos ya traen texto apto. Para desconocidos
    devolvemos un mensaje genérico; el stack va al logger técnico.
    """
    if isinstance(exc, (InvalidCredentialsError, AccountLockedError, AccountInactiveError)):
        return str(exc)
    logging.getLogger(__name__).exception("Error inesperado en login", exc_info=exc)
    return "Ocurrió un error inesperado. Intente de nuevo o revise los logs."
