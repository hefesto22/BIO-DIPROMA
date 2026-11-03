"""Controller de la vista de Asistencia (Sub-3.4b / Sub-3.4c).

Expone los métodos de uso directo desde la vista:

    - list_asistencias / list_empleados_para_filtro / update_observaciones
      (VIEW_ATTENDANCE) — consulta y edición de anotaciones.
    - re_consolidar(desde, hasta, empleado_id)
      (RUN_ZKTECO_SYNC) — re-consolidación manual del rango, usada
      cuando el operador editó turnos/feriados y quiere regenerar las
      asistencias del período afectado.
    - puede_re_consolidar()
      Ayuda para la vista: decide si mostrar el botón de re-consolidar
      según el rol del usuario logueado.

Diseño:
    - El controller NO conoce Tk/customtkinter — devuelve modelos /
      DTOs y propaga excepciones de dominio para que la vista decida
      cómo presentarlas.
    - Para que ``@require_permission`` funcione, se exponen ``self.session``
      y ``self.permission_service`` como atributos públicos (mismo
      contrato que ``EmpleadosController``, ``SincronizacionController``,
      etc.).
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from core.models import permissions as perms
from core.models.turno import Turno
from core.services.asistencia_service import (
    AsistenciaService,
    ResultadoBusquedaAsistencia,
)
from core.services.permission_service import PermissionService, require_permission
from core.services.session import Session
from core.services.sincronizacion_result import ResultadoConsolidacion
from core.services.turno_service import TurnoService


class AsistenciaController:
    """Controller de la vista de Asistencia (consulta + edición de observaciones)."""

    def __init__(
        self,
        session: Session,
        permission_service: PermissionService,
        asistencia_service: AsistenciaService,
        turno_service: Optional[TurnoService] = None,
    ) -> None:
        """Inicializa el controller.

        Args:
            session: Sesión activa. Leída por el decorador vía
                ``self.session``. También se usa para resolver
                ``actor_user_id`` al actualizar observaciones.
            permission_service: Verificador de permisos (leído por el
                decorador vía ``self.permission_service``).
            asistencia_service: Servicio con la lógica de listado y
                edición.
            turno_service: Para mostrar el horario esperado de cada día
                en el detalle (opcional).
        """
        # Nombres obligatorios para que ``@require_permission`` funcione.
        self.session = session
        self.permission_service = permission_service
        self._asistencia = asistencia_service
        self._turno = turno_service
        self._log = logging.getLogger(self.__class__.__name__)

    # ── Reads ─────────────────────────────────────────────────────────────

    @require_permission(perms.VIEW_ATTENDANCE)
    def list_asistencias(
        self,
        desde: str,
        hasta: str,
        empleado_id: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> ResultadoBusquedaAsistencia:
        """Devuelve las asistencias del rango, enriquecidas y con bandera de trunc.

        ``limit`` sube el tope de filas (la tabla nueva aguanta meses
        enteros); ``None`` usa el tope del servicio.
        """
        if limit is None:
            return self._asistencia.list_asistencias(
                desde=desde, hasta=hasta, empleado_id=empleado_id
            )
        return self._asistencia.list_asistencias(
            desde=desde, hasta=hasta, empleado_id=empleado_id, limit=limit
        )

    @require_permission(perms.VIEW_ATTENDANCE)
    def turnos(self) -> Dict[int, Turno]:
        """Catálogo de turnos (con sus horarios por día) para el detalle."""
        if self._turno is None:
            return {}
        return {t.id: t for t in self._turno.list_turnos(solo_activos=False) if t.id is not None}

    @require_permission(perms.VIEW_ATTENDANCE)
    def list_empleados_para_filtro(self) -> List[Tuple[int, str]]:
        """Devuelve ``[(empleado_id, "apellidos nombres")]`` para el combo."""
        return self._asistencia.list_empleados_para_filtro()

    # ── Writes ────────────────────────────────────────────────────────────

    @require_permission(perms.VIEW_ATTENDANCE)
    def update_observaciones(
        self,
        asistencia_id: int,
        observaciones: Optional[str],
    ) -> None:
        """Actualiza la observación manual de una fila de asistencia.

        El ``actor_user_id`` lo provee el controller desde la sesión
        activa — la vista no necesita conocerlo.

        Raises:
            AsistenciaNotFoundError: Si el id no existe.
        """
        self._asistencia.update_observaciones(
            asistencia_id=asistencia_id,
            observaciones=observaciones,
            actor_user_id=self.session.user_id,
        )

    @require_permission(perms.RUN_ZKTECO_SYNC)
    def re_consolidar(
        self,
        desde: str,
        hasta: str,
        empleado_id: Optional[int] = None,
    ) -> ResultadoConsolidacion:
        """Re-consolida el rango (opcionalmente filtrado por empleado).

        Se protege con ``RUN_ZKTECO_SYNC`` — mismo permiso que la
        sincronización, porque el efecto (regenerar asistencias) es de
        la misma magnitud operativa. El ``actor_user_id`` lo resuelve
        el controller desde la sesión activa.

        Raises:
            PermissionDeniedError: si el rol no tiene RUN_ZKTECO_SYNC.
            EmpleadoNotFoundError: empleado filtrado no activo.
            InvalidRangoError / InvalidDateError: rango mal formado.
        """
        return self._asistencia.re_consolidar(
            desde=desde,
            hasta=hasta,
            empleado_id=empleado_id,
            actor_user_id=self.session.user_id,
        )

    def puede_re_consolidar(self) -> bool:
        """¿El usuario logueado puede re-consolidar? (para mostrar/ocultar botón).

        No usa ``@require_permission`` — es una consulta booleana que
        la vista usa para decidir su layout, no una acción privilegiada.
        Delega en ``Session.has_permission``, que devuelve ``bool`` sin
        levantar excepciones.
        """
        return self.session.has_permission(perms.RUN_ZKTECO_SYNC)
