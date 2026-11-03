"""Tests del MainController.

Valida:
    - Cada open_* con permiso llama al view opener con el code correcto.
    - Cada open_* sin permiso lanza PermissionDeniedError.
    - open_by_code despacha al handler correcto.
    - open_by_code con code desconocido lanza KeyError.
"""

from __future__ import annotations

from typing import List

import pytest

from core.models import permissions as perms
from core.services.errors import PermissionDeniedError
from core.services.permission_service import PermissionService
from core.services.session import Session
from ui.controllers.main_controller import MainController


def _session_con(perms_set: set[str]) -> Session:
    return Session(
        user_id=1,
        username="u",
        role_id=1,
        role_code="TEST",
        permissions=frozenset(perms_set),
    )


def _controller(perms_set: set[str]) -> tuple[MainController, List[str]]:
    aperturas: List[str] = []
    ctrl = MainController(
        session=_session_con(perms_set),
        permission_service=PermissionService(),
        open_view=aperturas.append,
    )
    return ctrl, aperturas


# ── Handlers individuales: con permiso ────────────────────────────────────────


def test_open_users_con_permiso_llama_view_opener() -> None:
    ctrl, aperturas = _controller({perms.MANAGE_USERS})
    ctrl.open_users()
    assert aperturas == ["users"]


def test_open_employees_con_permiso_llama_view_opener() -> None:
    ctrl, aperturas = _controller({perms.MANAGE_EMPLOYEES})
    ctrl.open_employees()
    assert aperturas == ["employees"]


def test_open_shifts_comparte_permiso_con_employees() -> None:
    """Ambos usan manage_employees — dato del PRD."""
    ctrl, aperturas = _controller({perms.MANAGE_EMPLOYEES})
    ctrl.open_shifts()
    assert aperturas == ["shifts"]


# ── Handlers individuales: sin permiso ────────────────────────────────────────


def test_open_users_sin_permiso_lanza_permission_denied() -> None:
    ctrl, aperturas = _controller({perms.VIEW_ATTENDANCE})
    with pytest.raises(PermissionDeniedError) as exc:
        ctrl.open_users()
    assert exc.value.permission == perms.MANAGE_USERS
    assert aperturas == []


def test_open_reports_sin_permiso_lanza_permission_denied() -> None:
    ctrl, aperturas = _controller({perms.MANAGE_USERS})
    with pytest.raises(PermissionDeniedError):
        ctrl.open_reports()
    assert aperturas == []


# ── Dispatcher open_by_code ───────────────────────────────────────────────────


def test_open_by_code_con_permiso_despacha() -> None:
    ctrl, aperturas = _controller({perms.VIEW_ATTENDANCE})
    ctrl.open_by_code("attendance")
    assert aperturas == ["attendance"]


def test_open_by_code_sin_permiso_lanza_permission_denied() -> None:
    ctrl, aperturas = _controller(set())
    with pytest.raises(PermissionDeniedError):
        ctrl.open_by_code("users")
    assert aperturas == []


def test_open_by_code_desconocido_lanza_key_error() -> None:
    ctrl, _aperturas = _controller(set(perms.ALL_PERMISSIONS))
    with pytest.raises(KeyError):
        ctrl.open_by_code("modulo_inexistente")


def test_operador_puede_abrir_sync_y_asistencia_y_nada_mas() -> None:
    ctrl, aperturas = _controller({perms.RUN_ZKTECO_SYNC, perms.VIEW_ATTENDANCE})
    ctrl.open_zkteco_sync()
    ctrl.open_attendance()
    assert aperturas == ["zkteco_sync", "attendance"]

    with pytest.raises(PermissionDeniedError):
        ctrl.open_employees()
    with pytest.raises(PermissionDeniedError):
        ctrl.open_reports()
