"""Tests del SetupController (UI).

Usa un mock del SetupWizardService — no tocamos SQLite ni Tk.
"""

from __future__ import annotations

from typing import Optional
from unittest.mock import Mock

import pytest

from core.models.usuario import Usuario
from core.services.errors import (
    DuplicateUsernameError,
    SetupAlreadyCompletedError,
    WeakPasswordError,
)
from ui.controllers.setup_controller import SetupController


def _usuario_fake() -> Usuario:
    return Usuario(
        id=1,
        username="admin",
        password_hash="hash",
        full_name="Administrador",
        role_id=1,
    )


@pytest.fixture
def service() -> Mock:
    return Mock()


@pytest.fixture
def controller(service: Mock) -> SetupController:
    return SetupController(service)


def test_is_first_run_delega_al_service(controller: SetupController, service: Mock) -> None:
    service.is_first_run.return_value = True
    assert controller.is_first_run() is True
    service.is_first_run.assert_called_once_with()


def test_try_create_superadmin_feliz(controller: SetupController, service: Mock) -> None:
    usuario = _usuario_fake()
    service.create_superadmin.return_value = usuario

    resultado = controller.try_create_superadmin("admin", "Secreto1!", "Administrador")

    assert resultado.ok is True
    assert resultado.usuario is usuario
    assert resultado.error_message is None
    service.create_superadmin.assert_called_once_with("admin", "Secreto1!", "Administrador")


@pytest.mark.parametrize(
    ("exception", "extra"),
    [
        (WeakPasswordError("Debe tener al menos 8 caracteres."), None),
        (DuplicateUsernameError("admin"), None),
        (SetupAlreadyCompletedError(), None),
        (ValueError("El nombre de usuario no puede estar vacío."), None),
    ],
)
def test_try_create_superadmin_captura_errores_conocidos(
    controller: SetupController,
    service: Mock,
    exception: Exception,
    extra: Optional[str],
) -> None:
    service.create_superadmin.side_effect = exception
    resultado = controller.try_create_superadmin("admin", "weak", "Administrador")
    assert resultado.ok is False
    assert resultado.usuario is None
    assert resultado.error_message  # no vacío


def test_try_create_superadmin_propaga_errores_inesperados(
    controller: SetupController, service: Mock
) -> None:
    """Excepciones no previstas NO se tragan — el composition root decide."""
    service.create_superadmin.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        controller.try_create_superadmin("admin", "Secreto1!", "Administrador")
