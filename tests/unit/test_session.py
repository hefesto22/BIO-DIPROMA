"""Tests del dataclass Session."""

from __future__ import annotations

import dataclasses

import pytest

from core.services.session import Session


def _nueva_session(permisos: set[str] | None = None) -> Session:
    return Session(
        user_id=1,
        username="pepe",
        role_id=2,
        role_code="ADMIN",
        permissions=frozenset(permisos or {"manage_employees"}),
    )


def test_has_permission_true() -> None:
    s = _nueva_session({"manage_employees", "view_attendance"})
    assert s.has_permission("manage_employees") is True


def test_has_permission_false() -> None:
    s = _nueva_session({"view_attendance"})
    assert s.has_permission("manage_users") is False


def test_session_es_frozen() -> None:
    """La sesión no se muta: reemplazarla crea una nueva instancia."""
    s = _nueva_session()
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.username = "otro"  # type: ignore[misc]


def test_permissions_es_frozenset() -> None:
    s = _nueva_session()
    assert isinstance(s.permissions, frozenset)
