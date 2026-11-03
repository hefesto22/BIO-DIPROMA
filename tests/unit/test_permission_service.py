"""Tests de PermissionService y del decorador @require_permission."""

from __future__ import annotations

from typing import Optional

import pytest

from core.services.errors import NotAuthenticatedError, PermissionDeniedError
from core.services.permission_service import PermissionService, require_permission
from core.services.session import Session


def _session_con(permisos: set[str]) -> Session:
    return Session(
        user_id=1,
        username="user",
        role_id=2,
        role_code="ADMIN",
        permissions=frozenset(permisos),
    )


# ── Tests: check directo ──────────────────────────────────────────────────────


def test_check_con_permiso_presente_no_lanza() -> None:
    svc = PermissionService()
    svc.check(_session_con({"manage_employees"}), "manage_employees")


def test_check_sin_permiso_lanza_permission_denied() -> None:
    svc = PermissionService()
    with pytest.raises(PermissionDeniedError) as exc:
        svc.check(_session_con({"view_attendance"}), "manage_users")
    assert exc.value.permission == "manage_users"


def test_check_sin_session_lanza_not_authenticated() -> None:
    svc = PermissionService()
    with pytest.raises(NotAuthenticatedError):
        svc.check(None, "manage_users")


# ── Tests: decorador @require_permission ──────────────────────────────────────


class _ControllerFake:
    """Controller-like usado para probar el decorador."""

    def __init__(self, session: Optional[Session]) -> None:
        self.session = session
        self.permission_service = PermissionService()

    @require_permission("manage_employees")
    def accion_restringida(self) -> str:
        return "ejecutada"


def test_decorador_permite_si_tiene_permiso() -> None:
    ctrl = _ControllerFake(_session_con({"manage_employees"}))
    assert ctrl.accion_restringida() == "ejecutada"


def test_decorador_niega_si_no_tiene_permiso() -> None:
    ctrl = _ControllerFake(_session_con({"view_attendance"}))
    with pytest.raises(PermissionDeniedError):
        ctrl.accion_restringida()


def test_decorador_niega_si_no_hay_session() -> None:
    ctrl = _ControllerFake(None)
    with pytest.raises(NotAuthenticatedError):
        ctrl.accion_restringida()


def test_decorador_preserva_nombre_y_docstring() -> None:
    """``@wraps`` debe mantener metadata de la función original."""
    assert _ControllerFake.accion_restringida.__name__ == "accion_restringida"
