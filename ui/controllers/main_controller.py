"""Controller del MainWindow.

Orquesta la navegación entre módulos. Cada método público decorado con
``@require_permission`` es un "punto de entrada" a un módulo; el
decorator valida el permiso de la sesión antes de permitir abrir la
vista.

Doble defensa de permisos:
    1. El sidebar se filtra con ``permission_guard.filter_visible``:
       los botones de módulos sin permiso NO se renderizan.
    2. Aunque un bug futuro dejara un botón visible, cada ``open_*``
       está decorado: el click lanzaría ``PermissionDeniedError`` y
       no abriría la vista.
"""

from __future__ import annotations

import logging
from typing import Callable

from core.models import permissions as perms
from core.services.permission_service import PermissionService, require_permission
from core.services.session import Session

# La view es responsable de pasar este callback; el controller NO sabe
# de Tk ni de widgets — solo recibe el code del módulo a abrir y delega.
ViewOpener = Callable[[str], None]


class MainController:
    """Controller de la ventana principal. Aplica @require_permission."""

    def __init__(
        self,
        session: Session,
        permission_service: PermissionService,
        open_view: ViewOpener,
    ) -> None:
        """Inicializa el controller.

        Args:
            session: Sesión activa. Requerida; el MainWindow no debe
                montarse sin sesión válida.
            permission_service: Verificador de permisos. Usado por
                ``@require_permission``.
            open_view: Callback que la vista provee para montar el
                ``PlaceholderView`` (o la vista real en Phase 2+) en el
                área central. Recibe el ``code`` del módulo.
        """
        # Los atributos ``session`` y ``permission_service`` son los que
        # el decorador ``@require_permission`` lee. Nombre obligatorio.
        self.session = session
        self.permission_service = permission_service
        self._open_view = open_view
        self._log = logging.getLogger(self.__class__.__name__)

    # ── Puntos de entrada por módulo (todos decorados) ────────────────────

    @require_permission(perms.MANAGE_USERS)
    def open_users(self) -> None:
        """Abre la vista de usuarios y roles (SUPERADMIN)."""
        self._open_view("users")

    @require_permission(perms.MANAGE_SETTINGS)
    def open_settings(self) -> None:
        """Abre la configuración del sistema (SUPERADMIN / ADMIN)."""
        self._open_view("settings")

    @require_permission(perms.MANAGE_EMPLOYEES)
    def open_employees(self) -> None:
        """Abre la gestión de empleados (SUPERADMIN / ADMIN)."""
        self._open_view("employees")

    @require_permission(perms.MANAGE_EMPLOYEES)
    def open_shifts(self) -> None:
        """Abre la gestión de turnos (SUPERADMIN / ADMIN)."""
        self._open_view("shifts")

    @require_permission(perms.RUN_ZKTECO_SYNC)
    def open_zkteco_sync(self) -> None:
        """Abre la sincronización ZKTeco (SUPERADMIN / ADMIN / OPERADOR)."""
        self._open_view("zkteco_sync")

    @require_permission(perms.VIEW_ATTENDANCE)
    def open_attendance(self) -> None:
        """Abre la vista de asistencia (SUPERADMIN / ADMIN / OPERADOR)."""
        self._open_view("attendance")

    @require_permission(perms.EXPORT_REPORTS)
    def open_reports(self) -> None:
        """Abre la vista de reportes (SUPERADMIN / ADMIN / REPORTES).

        La vista usa pestañas internas para "Exportar" e "Historial de
        descargas"; no hay un punto de entrada separado al historial
        — el tab se renderiza condicionalmente si la sesión tiene
        ``VIEW_EXPORT_HISTORY``.
        """
        self._open_view("reports")

    # ── Mapa code → método ────────────────────────────────────────────────

    def open_by_code(self, code: str) -> None:
        """Dispatcher: recibe un ``MenuItem.code`` e invoca el handler.

        Este método NO está decorado porque el handler al que despacha
        sí lo está — la verificación ocurre dentro de ``open_*()``.

        Raises:
            KeyError: Si ``code`` no corresponde a ningún módulo.
            PermissionDeniedError: Si el handler detecta falta de permiso.
        """
        handlers = {
            "users": self.open_users,
            "settings": self.open_settings,
            "employees": self.open_employees,
            "shifts": self.open_shifts,
            "zkteco_sync": self.open_zkteco_sync,
            "attendance": self.open_attendance,
            "reports": self.open_reports,
        }
        handler = handlers.get(code)
        if handler is None:
            raise KeyError(f"Módulo desconocido: {code}")
        handler()
