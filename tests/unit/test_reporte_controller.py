"""Tests del ReporteController (Sub-3.5a).

Como los demás controllers del proyecto, este es un wrapper delgado
sobre ``ReporteService`` — aquí validamos que cada método:

    1. Delega con los argumentos correctos.
    2. Inyecta ``actor_user_id`` y ``actor_username`` desde la sesión
       activa al exportar (la vista no debe conocer esos datos).
    3. Está protegido por ``@require_permission`` correcto:
        - EXPORT_REPORTS para ``contar_filas_asistencia`` y
          ``exportar_asistencia``.
        - VIEW_EXPORT_HISTORY para ``list_historial_descargas``.
    4. Propaga sin tocar las excepciones de dominio (sin datos, I/O,
       rango inválido).

Usamos un stub manual (sin MagicMock) para mantener el test alineado
con ``test_asistencia_controller`` / ``test_empleados_controller``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pytest

from core.models import permissions as perms
from core.models.descarga_reporte import DescargaReporte, TipoReporte
from core.services.errors import (
    InvalidRangoError,
    PermissionDeniedError,
    ReporteIOError,
    ReporteSinDatosError,
)
from core.services.permission_service import PermissionService
from core.services.session import Session
from ui.controllers.reporte_controller import ReporteController


# ── Fábricas ────────────────────────────────────────────────────────────────


def _descarga(descarga_id: int = 1, user_id: Optional[int] = 42) -> DescargaReporte:
    return DescargaReporte(
        id=descarga_id,
        user_id=user_id,
        fecha_hora_utc="2026-04-24T10:00:00+00:00",
        tipo_reporte=TipoReporte.ASISTENCIA.value,
        rango_desde="2026-04-01",
        rango_hasta="2026-04-30",
        ruta_archivo="/tmp/rep.xlsx",
        filas_exportadas=10,
    )


# ── Stub del service ────────────────────────────────────────────────────────


class _StubReporteService:
    """Stub de ReporteService: duck-typed, registra llamadas."""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, Tuple[Any, ...], Dict[str, Any]]] = []
        self.contar_result: int = 0
        self.exportar_result: DescargaReporte = _descarga()
        self.historial_result: List[DescargaReporte] = []
        self.empleados_result: List[Tuple[int, str]] = []
        self.contar_raises: Optional[Exception] = None
        self.exportar_raises: Optional[Exception] = None
        self.historial_raises: Optional[Exception] = None
        self.empleados_raises: Optional[Exception] = None

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))

    def contar_asistencias(
        self,
        desde: str,
        hasta: str,
        empleado_id: Optional[int] = None,
    ) -> int:
        self._record(
            "contar_asistencias",
            desde=desde,
            hasta=hasta,
            empleado_id=empleado_id,
        )
        if self.contar_raises is not None:
            raise self.contar_raises
        return self.contar_result

    def exportar_asistencia(
        self,
        desde: str,
        hasta: str,
        output_path: str,
        actor_user_id: int,
        actor_username: str,
        empleado_id: Optional[int] = None,
    ) -> DescargaReporte:
        self._record(
            "exportar_asistencia",
            desde=desde,
            hasta=hasta,
            output_path=output_path,
            actor_user_id=actor_user_id,
            actor_username=actor_username,
            empleado_id=empleado_id,
        )
        if self.exportar_raises is not None:
            raise self.exportar_raises
        return self.exportar_result

    def exportar_asistencia_en_carpeta(
        self,
        desde: str,
        hasta: str,
        carpeta: str,
        actor_user_id: int,
        actor_username: str,
        empleado_id: Optional[int] = None,
    ) -> DescargaReporte:
        self._record(
            "exportar_asistencia_en_carpeta",
            desde=desde,
            hasta=hasta,
            carpeta=carpeta,
            actor_user_id=actor_user_id,
            actor_username=actor_username,
            empleado_id=empleado_id,
        )
        return self.exportar_result

    def primera_fecha(self, empleado_id: Optional[int] = None) -> Optional[str]:
        self._record("primera_fecha", empleado_id)
        return "2026-09-03"

    def list_historial_descargas(self, limit: int = 50) -> List[DescargaReporte]:
        self._record("list_historial_descargas", limit=limit)
        if self.historial_raises is not None:
            raise self.historial_raises
        return self.historial_result

    def list_empleados_para_filtro(self) -> List[Tuple[int, str]]:
        self._record("list_empleados_para_filtro")
        if self.empleados_raises is not None:
            raise self.empleados_raises
        return self.empleados_result


# ── Fábricas de controller + session ────────────────────────────────────────


def _session(permissions: set[str], user_id: int = 42, username: str = "alice") -> Session:
    return Session(
        user_id=user_id,
        username=username,
        role_id=1,
        role_code="TEST",
        permissions=frozenset(permissions),
    )


def _controller(
    permissions: set[str], user_id: int = 42, username: str = "alice"
) -> Tuple[ReporteController, _StubReporteService]:
    stub = _StubReporteService()
    ctrl = ReporteController(
        session=_session(permissions, user_id=user_id, username=username),
        permission_service=PermissionService(),
        reporte_service=stub,  # type: ignore[arg-type]  # duck-typed
    )
    return ctrl, stub


# ── contar_filas_asistencia ─────────────────────────────────────────────────


def test_contar_filas_delega_sin_filtro() -> None:
    ctrl, stub = _controller({perms.EXPORT_REPORTS})
    stub.contar_result = 17
    n = ctrl.contar_filas_asistencia(desde="2026-04-01", hasta="2026-04-30")
    assert n == 17
    assert stub.calls == [
        (
            "contar_asistencias",
            (),
            {
                "desde": "2026-04-01",
                "hasta": "2026-04-30",
                "empleado_id": None,
            },
        )
    ]


def test_contar_filas_delega_con_filtro_empleado() -> None:
    ctrl, stub = _controller({perms.EXPORT_REPORTS})
    ctrl.contar_filas_asistencia(desde="2026-04-01", hasta="2026-04-30", empleado_id=7)
    assert stub.calls[0][2]["empleado_id"] == 7


def test_contar_filas_propaga_invalid_rango() -> None:
    ctrl, stub = _controller({perms.EXPORT_REPORTS})
    stub.contar_raises = InvalidRangoError(desde="2026-04-30", hasta="2026-04-01")
    with pytest.raises(InvalidRangoError):
        ctrl.contar_filas_asistencia(desde="2026-04-30", hasta="2026-04-01")


# ── exportar_asistencia ─────────────────────────────────────────────────────


def test_exportar_inyecta_actor_user_id_y_username() -> None:
    """La vista no pasa user_id/username — el controller los toma de la session."""
    ctrl, stub = _controller({perms.EXPORT_REPORTS}, user_id=77, username="mauricio")
    descarga = ctrl.exportar_asistencia(
        desde="2026-04-01",
        hasta="2026-04-30",
        output_path="/tmp/x.xlsx",
    )
    assert descarga.id == 1
    assert stub.calls == [
        (
            "exportar_asistencia",
            (),
            {
                "desde": "2026-04-01",
                "hasta": "2026-04-30",
                "output_path": "/tmp/x.xlsx",
                "actor_user_id": 77,
                "actor_username": "mauricio",
                "empleado_id": None,
            },
        )
    ]


def test_exportar_propaga_filtro_empleado() -> None:
    ctrl, stub = _controller({perms.EXPORT_REPORTS})
    ctrl.exportar_asistencia(
        desde="2026-04-01",
        hasta="2026-04-30",
        output_path="/tmp/x.xlsx",
        empleado_id=7,
    )
    assert stub.calls[0][2]["empleado_id"] == 7


def test_exportar_propaga_reporte_sin_datos_error() -> None:
    ctrl, stub = _controller({perms.EXPORT_REPORTS})
    stub.exportar_raises = ReporteSinDatosError(
        desde="2026-04-01", hasta="2026-04-30", empleado_id=None
    )
    with pytest.raises(ReporteSinDatosError):
        ctrl.exportar_asistencia(
            desde="2026-04-01",
            hasta="2026-04-30",
            output_path="/tmp/x.xlsx",
        )


def test_exportar_propaga_reporte_io_error() -> None:
    ctrl, stub = _controller({perms.EXPORT_REPORTS})
    stub.exportar_raises = ReporteIOError(ruta="/tmp/x.xlsx", causa="Permission denied")
    with pytest.raises(ReporteIOError):
        ctrl.exportar_asistencia(
            desde="2026-04-01",
            hasta="2026-04-30",
            output_path="/tmp/x.xlsx",
        )


# ── list_historial_descargas ────────────────────────────────────────────────


def test_list_historial_delega_con_limit_default() -> None:
    ctrl, stub = _controller({perms.VIEW_EXPORT_HISTORY})
    stub.historial_result = [_descarga(descarga_id=1), _descarga(descarga_id=2)]
    result = ctrl.list_historial_descargas()
    assert len(result) == 2
    assert stub.calls == [("list_historial_descargas", (), {"limit": 50})]


def test_list_historial_delega_con_limit_custom() -> None:
    ctrl, stub = _controller({perms.VIEW_EXPORT_HISTORY})
    ctrl.list_historial_descargas(limit=10)
    assert stub.calls[0][2]["limit"] == 10


# ── list_empleados_para_filtro ──────────────────────────────────────────────


def test_list_empleados_para_filtro_delega() -> None:
    ctrl, stub = _controller({perms.EXPORT_REPORTS})
    stub.empleados_result = [(1, "Pérez Juan"), (2, "Gómez María")]
    result = ctrl.list_empleados_para_filtro()
    assert result == [(1, "Pérez Juan"), (2, "Gómez María")]
    assert stub.calls == [("list_empleados_para_filtro", (), {})]


def test_list_empleados_para_filtro_sin_permiso_lanza_denied() -> None:
    """Sin EXPORT_REPORTS no se puede poblar el filtro de empleados."""
    ctrl, stub = _controller({perms.VIEW_EXPORT_HISTORY})
    with pytest.raises(PermissionDeniedError):
        ctrl.list_empleados_para_filtro()
    assert stub.calls == []


# ── Guardas de permisos ─────────────────────────────────────────────────────


def test_sin_permiso_contar_filas_lanza_denied() -> None:
    """Sin EXPORT_REPORTS (incluso teniendo VIEW_EXPORT_HISTORY) → Denied."""
    ctrl, stub = _controller({perms.VIEW_EXPORT_HISTORY})
    with pytest.raises(PermissionDeniedError):
        ctrl.contar_filas_asistencia(desde="2026-04-01", hasta="2026-04-30")
    assert stub.calls == []  # no debe llegar al service


def test_sin_permiso_exportar_lanza_denied() -> None:
    """Sin EXPORT_REPORTS, ``exportar_asistencia`` no llega al service."""
    ctrl, stub = _controller({perms.VIEW_EXPORT_HISTORY})
    with pytest.raises(PermissionDeniedError):
        ctrl.exportar_asistencia(
            desde="2026-04-01",
            hasta="2026-04-30",
            output_path="/tmp/x.xlsx",
        )
    assert stub.calls == []


def test_sin_permiso_list_historial_lanza_denied() -> None:
    """Sin VIEW_EXPORT_HISTORY (incluso teniendo EXPORT_REPORTS) → Denied."""
    ctrl, stub = _controller({perms.EXPORT_REPORTS})
    with pytest.raises(PermissionDeniedError):
        ctrl.list_historial_descargas()
    assert stub.calls == []


def test_operador_no_puede_exportar() -> None:
    """OPERADOR (RUN_ZKTECO_SYNC + VIEW_ATTENDANCE) no exporta reportes."""
    ctrl, stub = _controller({perms.RUN_ZKTECO_SYNC, perms.VIEW_ATTENDANCE})
    with pytest.raises(PermissionDeniedError):
        ctrl.exportar_asistencia(
            desde="2026-04-01",
            hasta="2026-04-30",
            output_path="/tmp/x.xlsx",
        )
    assert stub.calls == []


def test_operador_no_puede_ver_historial() -> None:
    """OPERADOR tampoco ve el historial de descargas."""
    ctrl, stub = _controller({perms.RUN_ZKTECO_SYNC, perms.VIEW_ATTENDANCE})
    with pytest.raises(PermissionDeniedError):
        ctrl.list_historial_descargas()
    assert stub.calls == []


def test_reportes_role_puede_exportar_y_ver_historial() -> None:
    """REPORTES tiene EXPORT_REPORTS + VIEW_EXPORT_HISTORY (matriz del README)."""
    ctrl, stub = _controller({perms.EXPORT_REPORTS, perms.VIEW_EXPORT_HISTORY})
    # Ambos métodos llegan al service sin Denied.
    ctrl.contar_filas_asistencia(desde="2026-04-01", hasta="2026-04-30")
    ctrl.exportar_asistencia(desde="2026-04-01", hasta="2026-04-30", output_path="/tmp/x.xlsx")
    ctrl.list_historial_descargas()
    nombres = [c[0] for c in stub.calls]
    assert nombres == [
        "contar_asistencias",
        "exportar_asistencia",
        "list_historial_descargas",
    ]


def test_admin_role_puede_exportar_y_ver_historial() -> None:
    """ADMIN/SUPERADMIN tienen ambos permisos también — sanity check."""
    ctrl, stub = _controller(
        {
            perms.EXPORT_REPORTS,
            perms.VIEW_EXPORT_HISTORY,
            perms.MANAGE_EMPLOYEES,
        }
    )
    ctrl.contar_filas_asistencia(desde="2026-04-01", hasta="2026-04-30")
    ctrl.list_historial_descargas()
    assert len(stub.calls) == 2


# ── Guardado con nombre único y período "Todo" ──────────────────────────────


def test_exportar_en_carpeta_inyecta_actor_y_pasa_la_carpeta() -> None:
    ctrl, stub = _controller({perms.EXPORT_REPORTS}, user_id=77, username="mauricio")
    ctrl.exportar_asistencia_en_carpeta("2026-11-01", "2026-11-02", "/datos/reportes", 5)
    assert stub.calls == [
        (
            "exportar_asistencia_en_carpeta",
            (),
            {
                "desde": "2026-11-01",
                "hasta": "2026-11-02",
                "carpeta": "/datos/reportes",
                "actor_user_id": 77,
                "actor_username": "mauricio",
                "empleado_id": 5,
            },
        )
    ]


def test_sin_permiso_exportar_en_carpeta_ni_primera_fecha() -> None:
    ctrl, stub = _controller({perms.VIEW_EXPORT_HISTORY})
    with pytest.raises(PermissionDeniedError):
        ctrl.exportar_asistencia_en_carpeta("2026-11-01", "2026-11-02", "/x")
    with pytest.raises(PermissionDeniedError):
        ctrl.primera_fecha()
    assert stub.calls == []


def test_primera_fecha_delega() -> None:
    ctrl, stub = _controller({perms.EXPORT_REPORTS})
    assert ctrl.primera_fecha() == "2026-09-03"
    assert stub.calls == [("primera_fecha", (None,), {})]
