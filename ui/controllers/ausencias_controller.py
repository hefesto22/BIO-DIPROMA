"""Controller de "Vacaciones y permisos" (pestaña de Turnos y panel de Empleados).

Mismo permiso que turnos y empleados (``MANAGE_EMPLOYEES``): quien arma
los horarios es quien registra cuándo alguien no viene.
"""

from __future__ import annotations

import logging
from typing import Dict, Iterable, List, Optional

from core.models import permissions as perms
from core.models.ausencia import Ausencia
from core.models.empleado import Empleado
from core.services.ausencia_service import AusenciaService
from core.services.empleado_service import EmpleadoService
from core.services.permission_service import PermissionService, require_permission
from core.services.session import Session


class AusenciasController:
    """Fachada con guard de permisos sobre ``AusenciaService``."""

    def __init__(
        self,
        session: Session,
        permission_service: PermissionService,
        ausencia_service: AusenciaService,
        empleado_service: EmpleadoService,
    ) -> None:
        # Nombres obligatorios para que ``@require_permission`` funcione.
        self.session = session
        self.permission_service = permission_service
        self._ausencias = ausencia_service
        self._empleados = empleado_service
        self._log = logging.getLogger(self.__class__.__name__)

    # ── Lecturas ──────────────────────────────────────────────────────────

    @require_permission(perms.MANAGE_EMPLOYEES)
    def listar(self) -> List[Ausencia]:
        return self._ausencias.listar()

    @require_permission(perms.MANAGE_EMPLOYEES)
    def listar_por_empleado(self, empleado_id: int) -> List[Ausencia]:
        return self._ausencias.listar_por_empleado(empleado_id)

    @require_permission(perms.MANAGE_EMPLOYEES)
    def empleados(self, solo_activos: bool = False) -> List[Empleado]:
        """Empleados para el selector (activos) o para nombrar registros viejos (todos)."""
        return self._empleados.list_empleados(solo_activos=solo_activos)

    @require_permission(perms.MANAGE_EMPLOYEES)
    def dias_laborables(self, empleado_id: int, desde: str, hasta: str) -> int:
        return self._ausencias.dias_laborables(empleado_id, desde, hasta)

    @require_permission(perms.MANAGE_EMPLOYEES)
    def dias_laborables_de(self, ausencias: Iterable[Ausencia]) -> Dict[int, int]:
        return self._ausencias.dias_laborables_de(ausencias)

    @require_permission(perms.MANAGE_EMPLOYEES)
    def resumen_anual(self, empleado_id: int, anio: int) -> Dict[str, int]:
        return self._ausencias.resumen_anual(empleado_id, anio)

    # ── Escrituras ────────────────────────────────────────────────────────

    @require_permission(perms.MANAGE_EMPLOYEES)
    def registrar(
        self,
        empleado_id: int,
        tipo: str,
        fecha_desde: str,
        fecha_hasta: str,
        nota: Optional[str],
    ) -> Ausencia:
        return self._ausencias.registrar(
            empleado_id=empleado_id,
            tipo=tipo,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
            nota=nota,
            actor_user_id=self.session.user_id,
        )

    @require_permission(perms.MANAGE_EMPLOYEES)
    def actualizar(
        self,
        ausencia_id: int,
        tipo: str,
        fecha_desde: str,
        fecha_hasta: str,
        nota: Optional[str],
    ) -> Ausencia:
        return self._ausencias.actualizar(
            ausencia_id=ausencia_id,
            tipo=tipo,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
            nota=nota,
            actor_user_id=self.session.user_id,
        )

    @require_permission(perms.MANAGE_EMPLOYEES)
    def eliminar(self, ausencia_id: int) -> None:
        self._ausencias.eliminar(ausencia_id, self.session.user_id)
