"""Tests del LoginController (UI).

Usa un runner síncrono para no lidiar con threads, y un mock de
AuthService. Validamos:
    - Campos vacíos → mensaje genérico sin llamar al service.
    - Login OK → on_success con la Session.
    - Login inválido → mensaje genérico (no revela user vs password).
    - Cuenta bloqueada e inactiva → mensaje específico de la excepción.
    - Excepción inesperada → mensaje genérico, no se propaga.
"""

from __future__ import annotations

from typing import Callable, List
from unittest.mock import Mock

import pytest

from core.services.errors import (
    AccountInactiveError,
    AccountLockedError,
    InvalidCredentialsError,
)
from core.services.session import Session
from ui.controllers.login_controller import LoginController


def _session_fake() -> Session:
    return Session(
        user_id=1,
        username="admin",
        role_id=1,
        role_code="SUPERADMIN",
        permissions=frozenset({"manage_users"}),
    )


def _sync_runner(
    work: Callable[[], Session],
    on_success: Callable[[Session], None],
    on_error: Callable[[BaseException], None],
) -> None:
    """Ejecuta work() en el mismo hilo — simplifica los tests."""
    try:
        resultado = work()
    except BaseException as exc:  # noqa: BLE001 — replica el comportamiento del runner real
        on_error(exc)
        return
    on_success(resultado)


@pytest.fixture
def auth_service() -> Mock:
    return Mock()


@pytest.fixture
def controller(auth_service: Mock) -> LoginController:
    return LoginController(auth_service, _sync_runner)


# ── Validación de campos ──────────────────────────────────────────────────────


def test_campos_vacios_no_llaman_al_service(
    controller: LoginController, auth_service: Mock
) -> None:
    errores: List[str] = []
    controller.try_login("", "", lambda s: None, errores.append)
    assert errores == ["Ingrese usuario y contraseña."]
    auth_service.login.assert_not_called()


def test_username_vacio_no_llama_al_service(
    controller: LoginController, auth_service: Mock
) -> None:
    errores: List[str] = []
    controller.try_login("", "password", lambda s: None, errores.append)
    assert errores
    auth_service.login.assert_not_called()


# ── Flujo feliz ───────────────────────────────────────────────────────────────


def test_login_ok_invoca_on_success_con_session(
    controller: LoginController, auth_service: Mock
) -> None:
    session = _session_fake()
    auth_service.login.return_value = session

    sessions: List[Session] = []
    errores: List[str] = []
    controller.try_login("admin", "correct", sessions.append, errores.append)

    assert sessions == [session]
    assert errores == []
    auth_service.login.assert_called_once_with("admin", "correct")


# ── Errores de auth ───────────────────────────────────────────────────────────


def test_credenciales_invalidas_muestran_mensaje_generico(
    controller: LoginController, auth_service: Mock
) -> None:
    auth_service.login.side_effect = InvalidCredentialsError()
    errores: List[str] = []
    controller.try_login("admin", "WRONG", lambda s: None, errores.append)
    assert len(errores) == 1
    # Mensaje genérico (no revela user vs password).
    assert "Usuario o contraseña" in errores[0]


def test_cuenta_bloqueada_muestra_locked_until(
    controller: LoginController, auth_service: Mock
) -> None:
    auth_service.login.side_effect = AccountLockedError("2026-04-22T12:34:56+00:00")
    errores: List[str] = []
    controller.try_login("admin", "correct", lambda s: None, errores.append)
    assert len(errores) == 1
    assert "bloqueada" in errores[0].lower()


def test_cuenta_inactiva_muestra_mensaje_especifico(
    controller: LoginController, auth_service: Mock
) -> None:
    auth_service.login.side_effect = AccountInactiveError()
    errores: List[str] = []
    controller.try_login("admin", "correct", lambda s: None, errores.append)
    assert len(errores) == 1
    assert "desactivada" in errores[0].lower()


def test_excepcion_inesperada_muestra_mensaje_generico(
    controller: LoginController, auth_service: Mock
) -> None:
    auth_service.login.side_effect = RuntimeError("db caída")
    errores: List[str] = []
    controller.try_login("admin", "correct", lambda s: None, errores.append)
    assert len(errores) == 1
    # No incluye el texto interno del error, ni el tipo de la excepción.
    assert "db caída" not in errores[0]
    assert "inesperado" in errores[0].lower()
