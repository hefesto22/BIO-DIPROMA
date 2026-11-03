"""Controller del Setup Wizard (UI).

Orquesta ``SetupWizardService`` desde la vista customtkinter. No
importa Tk: los tests lo cubren con mocks del service.

Política de mensajes:
    - Si el service lanza una excepción conocida, devolvemos el texto
      del error (apto para UI, en español — ya viene así del service).
    - Excepciones no esperadas se propagan — el composition root las
      captura, las loggea y muestra un diálogo genérico.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from core.models.usuario import Usuario
from core.services.errors import (
    DuplicateUsernameError,
    SetupAlreadyCompletedError,
    WeakPasswordError,
)
from core.services.setup_wizard_service import SetupWizardService


@dataclass(frozen=True)
class SetupResult:
    """Resultado del intento de crear el SUPERADMIN.

    Atributos:
        ok: ``True`` si se creó. ``False`` si alguna validación falló.
        usuario: El usuario creado (solo presente si ``ok``).
        error_message: Mensaje en español para mostrar al usuario si
            ``ok`` es ``False``.
    """

    ok: bool
    usuario: Optional[Usuario]
    error_message: Optional[str]


class SetupController:
    """Pegamento entre ``SetupWizardWindow`` y ``SetupWizardService``."""

    def __init__(self, service: SetupWizardService) -> None:
        """Inicializa el controller con el service inyectado."""
        self._service = service
        self._log = logging.getLogger(self.__class__.__name__)

    def is_first_run(self) -> bool:
        """Proxy simple para que la vista no importe el service."""
        return self._service.is_first_run()

    def try_create_superadmin(
        self,
        username: str,
        password: str,
        full_name: str,
    ) -> SetupResult:
        """Intenta crear el SUPERADMIN; devuelve un resultado estructurado.

        La vista recibe el ``SetupResult`` y decide qué widget actualizar
        (label de error, cerrar ventana, etc.).
        """
        try:
            usuario = self._service.create_superadmin(username, password, full_name)
        except WeakPasswordError as err:
            return SetupResult(ok=False, usuario=None, error_message=err.reason)
        except DuplicateUsernameError as err:
            return SetupResult(ok=False, usuario=None, error_message=str(err))
        except SetupAlreadyCompletedError as err:
            return SetupResult(ok=False, usuario=None, error_message=str(err))
        except ValueError as err:
            # Campos vacíos (username, full_name).
            return SetupResult(ok=False, usuario=None, error_message=str(err))

        self._log.info("Superadmin creado desde wizard: %s", usuario.username)
        return SetupResult(ok=True, usuario=usuario, error_message=None)
