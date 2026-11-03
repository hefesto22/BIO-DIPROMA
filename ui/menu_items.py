"""Catálogo de entradas del sidebar del MainWindow.

Cada entrada liga un código interno, un label en español, el permiso
requerido y una descripción (usada por el PlaceholderView mientras el
módulo real aún no existe).

Phase 2+ agregará un campo ``view_factory`` opcional: cuando un módulo
tenga implementación real, apuntará a un callable que construya su
vista; hasta entonces, todas las entradas caen en el placeholder.

Diseño:
    - La lista es la fuente de verdad de la navegación. El
      ``permission_guard`` la filtra por sesión; el MainWindow pinta
      solo lo permitido.
    - El permiso asociado se valida DOS veces: al renderizar el sidebar
      (defensa primaria) y al invocar el método del controller
      (defensa secundaria, vía ``@require_permission``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Tuple

from core.models import permissions as perms


@dataclass(frozen=True)
class MenuItem:
    """Entrada del sidebar del MainWindow.

    Atributos:
        code: Identificador estable. Se usa internamente para enrutar
            y para cargar el PlaceholderView. Nunca se muestra al usuario.
        label: Texto visible en el botón (español).
        permission: Código del permiso requerido. Debe existir en
            ``permissions.ALL_PERMISSIONS``.
        description: Texto descriptivo mostrado en el placeholder.
    """

    code: str
    label: str
    permission: str
    description: str


MENU_ITEMS: Final[Tuple[MenuItem, ...]] = (
    MenuItem(
        code="users",
        label="Usuarios y roles",
        permission=perms.MANAGE_USERS,
        description="Gestión de usuarios internos y asignación de roles.",
    ),
    MenuItem(
        code="settings",
        label="Configuración",
        permission=perms.MANAGE_SETTINGS,
        description="Parámetros del sistema y conexión con el reloj ZKTeco.",
    ),
    MenuItem(
        code="employees",
        label="Empleados",
        permission=perms.MANAGE_EMPLOYEES,
        description="Alta, modificación y baja de empleados.",
    ),
    MenuItem(
        code="shifts",
        label="Turnos",
        permission=perms.MANAGE_EMPLOYEES,
        description="Definición de turnos y asignación a empleados.",
    ),
    MenuItem(
        code="zkteco_sync",
        label="Sincronización ZKTeco",
        permission=perms.RUN_ZKTECO_SYNC,
        description="Descarga de marcas desde el reloj biométrico.",
    ),
    MenuItem(
        code="attendance",
        label="Asistencia",
        permission=perms.VIEW_ATTENDANCE,
        description="Visualización de marcas y asistencias calculadas.",
    ),
    MenuItem(
        code="reports",
        label="Reportes",
        permission=perms.EXPORT_REPORTS,
        description="Generación y descarga de reportes Excel.",
    ),
)
