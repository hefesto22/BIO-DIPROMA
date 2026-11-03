"""Tests del catálogo de constantes de permisos y roles."""

from __future__ import annotations

from core.models import permissions


def test_all_permissions_contiene_los_8() -> None:
    """Debe haber exactamente 8 permisos: los 7 del PRD original + manage_roles
    (Sub-2.7a separó MANAGE_USERS y MANAGE_ROLES para que ADMIN gestione
    usuarios sin tocar la matriz de roles)."""
    assert len(permissions.ALL_PERMISSIONS) == 8


def test_cada_constante_esta_en_all_permissions() -> None:
    """Toda constante individual debe aparecer en el set agregador."""
    esperados = {
        permissions.MANAGE_USERS,
        permissions.MANAGE_ROLES,
        permissions.MANAGE_SETTINGS,
        permissions.MANAGE_EMPLOYEES,
        permissions.RUN_ZKTECO_SYNC,
        permissions.VIEW_ATTENDANCE,
        permissions.EXPORT_REPORTS,
        permissions.VIEW_EXPORT_HISTORY,
    }
    assert esperados == set(permissions.ALL_PERMISSIONS)


def test_codigos_de_rol_canonicos() -> None:
    """Los códigos de rol deben ser los exactos del PRD (case-sensitive)."""
    assert permissions.ROLE_SUPERADMIN == "SUPERADMIN"
    assert permissions.ROLE_ADMIN == "ADMIN"
    assert permissions.ROLE_REPORTES == "REPORTES"
    assert permissions.ROLE_OPERADOR == "OPERADOR"
    assert permissions.ALL_ROLES == {
        "SUPERADMIN",
        "ADMIN",
        "REPORTES",
        "OPERADOR",
    }


def test_all_permissions_es_inmutable() -> None:
    """Debe ser un frozenset — ningún caller puede agregarle permisos."""
    assert isinstance(permissions.ALL_PERMISSIONS, frozenset)
    assert isinstance(permissions.ALL_ROLES, frozenset)
