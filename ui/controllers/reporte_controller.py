"""Controller de la vista de Reportes (Sub-3.5).

Expone los métodos de uso directo desde la vista:

    - contar_filas_asistencia (EXPORT_REPORTS) — confirmación previa al
      export, para que la UI muestre un diálogo si la cantidad supera
      un umbral.
    - exportar_asistencia (EXPORT_REPORTS) — genera el .xlsx, registra
      historial y deja audit log. Inyecta ``actor_user_id`` y
      ``actor_username`` desde la sesión activa para que la vista no
      tenga que conocerlos.
    - list_historial_descargas (VIEW_EXPORT_HISTORY) — listado de
      descargas pasadas para la vista de Historial.

Diseño:
    - El controller NO conoce Tk/customtkinter — recibe parámetros
      planos y devuelve modelos de dominio. La traducción a widgets
      vive en la vista.
    - ``self.session`` y ``self.permission_service`` se exponen como
      atributos públicos para que ``@require_permission`` pueda leerlos.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from core.models import permissions as perms
from core.models.descarga_reporte import DescargaReporte
from core.services.asistencia_exporter import ResumenEmpleado
from core.services.permission_service import PermissionService, require_permission
from core.services.reporte_service import ReporteService
from core.services.session import Session


class ReporteController:
    """Controller de la vista de Reportes (export Excel + historial)."""

    def __init__(
        self,
        session: Session,
        permission_service: PermissionService,
        reporte_service: ReporteService,
    ) -> None:
        """Inicializa el controller.

        Args:
            session: Sesión activa (leída por el decorador y para
                resolver actor_user_id/username al exportar).
            permission_service: Verificador de permisos (leído por el
                decorador).
            reporte_service: Servicio con la lógica de export e historial.
        """
        self.session = session
        self.permission_service = permission_service
        self._reportes = reporte_service
        self._log = logging.getLogger(self.__class__.__name__)

    # ── Reads ─────────────────────────────────────────────────────────────

    @require_permission(perms.EXPORT_REPORTS)
    def contar_filas_asistencia(
        self,
        desde: str,
        hasta: str,
        empleado_id: Optional[int] = None,
    ) -> int:
        """Cuenta filas que se exportarían — para confirmación previa.

        Raises:
            PermissionDeniedError: si el rol no tiene EXPORT_REPORTS.
            InvalidDateError, InvalidRangoError: rango mal formado.
        """
        return self._reportes.contar_asistencias(desde=desde, hasta=hasta, empleado_id=empleado_id)

    @require_permission(perms.EXPORT_REPORTS)
    def vista_previa(
        self,
        desde: str,
        hasta: str,
        empleado_id: Optional[int] = None,
    ) -> List[ResumenEmpleado]:
        """Resumen por empleado del rango, tal como saldrá en el Excel."""
        return self._reportes.vista_previa(desde=desde, hasta=hasta, empleado_id=empleado_id)

    @require_permission(perms.EXPORT_REPORTS)
    def primera_fecha(self, empleado_id: Optional[int] = None) -> Optional[str]:
        """Primer día con asistencia calculada (período "Todo"); ``None`` si no hay."""
        return self._reportes.primera_fecha(empleado_id)

    @require_permission(perms.VIEW_EXPORT_HISTORY)
    def list_historial_descargas(self, limit: int = 50) -> List[DescargaReporte]:
        """Devuelve las descargas más recientes para la vista de Historial.

        Raises:
            PermissionDeniedError: si el rol no tiene VIEW_EXPORT_HISTORY.
        """
        return self._reportes.list_historial_descargas(limit=limit)

    @require_permission(perms.EXPORT_REPORTS)
    def list_empleados_para_filtro(self) -> List[Tuple[int, str]]:
        """Devuelve los empleados activos para poblar el filtro del export.

        Protegido por ``EXPORT_REPORTS`` (no por ``VIEW_ATTENDANCE``)
        porque este controller es la "puerta" del módulo de reportes
        — un usuario REPORTES debe poder filtrar por empleado para
        exportar sin tener acceso al módulo de Asistencia.

        Raises:
            PermissionDeniedError: si el rol no tiene EXPORT_REPORTS.
        """
        return self._reportes.list_empleados_para_filtro()

    # ── Writes ────────────────────────────────────────────────────────────

    @require_permission(perms.EXPORT_REPORTS)
    def exportar_asistencia(
        self,
        desde: str,
        hasta: str,
        output_path: str,
        empleado_id: Optional[int] = None,
    ) -> DescargaReporte:
        """Exporta el reporte de asistencia y devuelve la fila de historial.

        El ``actor_user_id`` y ``actor_username`` los provee el controller
        desde la sesión activa — la vista no necesita conocerlos.

        Raises:
            PermissionDeniedError: si el rol no tiene EXPORT_REPORTS.
            InvalidDateError, InvalidRangoError: rango mal formado.
            ReporteSinDatosError: el rango no tiene asistencias.
            ReporteIOError: el archivo no se pudo escribir.
        """
        return self._reportes.exportar_asistencia(
            desde=desde,
            hasta=hasta,
            output_path=output_path,
            actor_user_id=self.session.user_id,
            actor_username=self.session.username,
            empleado_id=empleado_id,
        )

    @require_permission(perms.EXPORT_REPORTS)
    def exportar_asistencia_en_carpeta(
        self,
        desde: str,
        hasta: str,
        carpeta: str,
        empleado_id: Optional[int] = None,
    ) -> DescargaReporte:
        """Exporta con nombre único ``Reporte_fecha_hora[_empleado].xlsx`` en ``carpeta``.

        Raises:
            PermissionDeniedError: si el rol no tiene EXPORT_REPORTS.
            InvalidDateError, InvalidRangoError: rango mal formado.
            ReporteSinDatosError: el rango no tiene asistencias.
            ReporteIOError: el archivo no se pudo escribir.
        """
        return self._reportes.exportar_asistencia_en_carpeta(
            desde=desde,
            hasta=hasta,
            carpeta=carpeta,
            actor_user_id=self.session.user_id,
            actor_username=self.session.username,
            empleado_id=empleado_id,
        )
