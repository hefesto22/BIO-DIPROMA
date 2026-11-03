"""Tests del permission_guard.

Función pura — tests directos con Session fabricadas a mano.
"""

from __future__ import annotations

from core.models import permissions as perms
from core.services.session import Session
from ui.guards.permission_guard import filter_visible
from ui.menu_items import MENU_ITEMS


def _session_con(role_code: str, perms_set: set[str]) -> Session:
    return Session(
        user_id=1,
        username="u",
        role_id=1,
        role_code=role_code,
        permissions=frozenset(perms_set),
    )


def test_superadmin_ve_todas_las_entradas() -> None:
    """SUPERADMIN tiene los 7 permisos → ve las 7 entradas del catálogo.

    Tras Sub-3.5b el menú colapsó "Reportes" e "Historial de descargas"
    en una sola entrada con pestañas internas, así que el catálogo
    pasó de 8 a 7 items.
    """
    session = _session_con(perms.ROLE_SUPERADMIN, set(perms.ALL_PERMISSIONS))
    visibles = filter_visible(MENU_ITEMS, session)
    assert len(visibles) == len(MENU_ITEMS)


def test_operador_solo_ve_sync_y_asistencia() -> None:
    """OPERADOR: run_zkteco_sync + view_attendance → exactamente 2 entradas."""
    session = _session_con(
        perms.ROLE_OPERADOR,
        {perms.RUN_ZKTECO_SYNC, perms.VIEW_ATTENDANCE},
    )
    visibles = filter_visible(MENU_ITEMS, session)
    codes = {m.code for m in visibles}
    assert codes == {"zkteco_sync", "attendance"}


def test_reportes_solo_ve_reportes() -> None:
    """REPORTES solo ve la entrada 'Reportes' del menú.

    El historial vive como pestaña interna de la misma vista — la
    sesión necesita VIEW_EXPORT_HISTORY para que la pestaña aparezca,
    pero el menú lateral solo muestra una entrada.
    """
    session = _session_con(
        perms.ROLE_REPORTES,
        {perms.EXPORT_REPORTS, perms.VIEW_EXPORT_HISTORY},
    )
    visibles = filter_visible(MENU_ITEMS, session)
    codes = {m.code for m in visibles}
    assert codes == {"reports"}


def test_admin_no_ve_usuarios_y_roles() -> None:
    """Matriz del PRD: ADMIN NO tiene manage_users."""
    admin_perms = set(perms.ALL_PERMISSIONS) - {perms.MANAGE_USERS}
    session = _session_con(perms.ROLE_ADMIN, admin_perms)
    visibles = filter_visible(MENU_ITEMS, session)
    codes = {m.code for m in visibles}
    assert "users" not in codes
    # Debe ver los otros 6 códigos (7 items totales en el menú; "users"
    # queda fuera; "shifts" sigue visible por compartir manage_employees).
    assert len(visibles) == len(MENU_ITEMS) - 1


def test_sesion_sin_permisos_no_ve_nada() -> None:
    session = _session_con("NADIE", set())
    assert filter_visible(MENU_ITEMS, session) == []


def test_orden_de_la_lista_se_preserva() -> None:
    """La UI depende del orden — validamos que el filtro no lo altere."""
    session = _session_con(perms.ROLE_SUPERADMIN, set(perms.ALL_PERMISSIONS))
    visibles = filter_visible(MENU_ITEMS, session)
    assert [m.code for m in visibles] == [m.code for m in MENU_ITEMS]


def test_todos_los_permisos_usados_existen_en_catalogo() -> None:
    """Invariante: cada MenuItem.permission pertenece a ALL_PERMISSIONS."""
    for item in MENU_ITEMS:
        assert (
            item.permission in perms.ALL_PERMISSIONS
        ), f"Item {item.code} usa permiso inexistente: {item.permission}"
