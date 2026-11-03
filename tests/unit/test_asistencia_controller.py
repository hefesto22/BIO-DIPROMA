"""Tests del AsistenciaController (Sub-3.4b + Sub-3.4c).

El controller es un wrapper delgado sobre ``AsistenciaService`` — aquí
validamos que cada método:

    1. Delega con los argumentos correctos.
    2. Inyecta ``actor_user_id`` desde ``self.session.user_id`` al
       escribir (el caller de la vista no debe conocer el user id).
    3. Está protegido por ``@require_permission(VIEW_ATTENDANCE)`` —
       o ``RUN_ZKTECO_SYNC`` en el caso de ``re_consolidar`` — sin ese
       permiso lanza ``PermissionDeniedError`` sin tocar el service,
       incluso con otros permisos válidos del sistema.

Usamos un stub manual (sin MagicMock) para mantener el test legible,
alineado con ``test_empleados_controller`` y ``test_turnos_controller``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pytest

from core.models import permissions as perms
from core.models.asistencia import Asistencia, EstadoAsistencia
from core.services.asistencia_service import (
    AsistenciaVista,
    ResultadoBusquedaAsistencia,
)
from core.services.errors import AsistenciaNotFoundError, PermissionDeniedError
from core.services.permission_service import PermissionService
from core.services.session import Session
from core.services.sincronizacion_result import ResultadoConsolidacion
from ui.controllers.asistencia_controller import AsistenciaController


# ── Fábricas ────────────────────────────────────────────────────────────────


def _asistencia(emp_id: int = 1, fecha: str = "2026-04-15") -> Asistencia:
    return Asistencia(
        id=100,
        empleado_id=emp_id,
        fecha=fecha,
        estado=EstadoAsistencia.PRESENTE.value,
        turno_id_aplicado=7,
        hora_entrada_real="08:00:00",
        hora_salida_real="17:00:00",
    )


def _vista(emp_id: int = 1) -> AsistenciaVista:
    return AsistenciaVista(
        asistencia=_asistencia(emp_id=emp_id),
        empleado_nombre_completo="Pérez Juan",
        empleado_dni="0801-1990-12345",
        turno_nombre="Admin 8-5",
    )


# ── Stub del service ────────────────────────────────────────────────────────


class _StubAsistenciaService:
    """Stub de AsistenciaService: duck-typed, registra llamadas."""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, Tuple[Any, ...], Dict[str, Any]]] = []
        self.list_result = ResultadoBusquedaAsistencia(items=[_vista()], truncado=False)
        self.empleados_result: List[Tuple[int, str]] = [(1, "Pérez Juan")]
        self.update_raises: Optional[Exception] = None
        self.re_consolidar_raises: Optional[Exception] = None
        self.re_consolidar_result = ResultadoConsolidacion(
            empleados_procesados=1,
            dias_procesados=3,
            asistencias_upsertadas=3,
            marcadas_desconocidas=[],
        )

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))

    def list_asistencias(
        self,
        desde: str,
        hasta: str,
        empleado_id: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> ResultadoBusquedaAsistencia:
        self._record(
            "list_asistencias",
            desde=desde,
            hasta=hasta,
            empleado_id=empleado_id,
            limit=limit,
        )
        return self.list_result

    def list_empleados_para_filtro(self) -> List[Tuple[int, str]]:
        self._record("list_empleados_para_filtro")
        return self.empleados_result

    def update_observaciones(
        self,
        asistencia_id: int,
        observaciones: Optional[str],
        actor_user_id: int,
    ) -> None:
        self._record(
            "update_observaciones",
            asistencia_id=asistencia_id,
            observaciones=observaciones,
            actor_user_id=actor_user_id,
        )
        if self.update_raises is not None:
            raise self.update_raises

    def re_consolidar(
        self,
        desde: str,
        hasta: str,
        empleado_id: Optional[int],
        actor_user_id: int,
    ) -> ResultadoConsolidacion:
        self._record(
            "re_consolidar",
            desde=desde,
            hasta=hasta,
            empleado_id=empleado_id,
            actor_user_id=actor_user_id,
        )
        if self.re_consolidar_raises is not None:
            raise self.re_consolidar_raises
        return self.re_consolidar_result


# ── Fábricas de controller + session ────────────────────────────────────────


def _session(permissions: set[str], user_id: int = 42) -> Session:
    return Session(
        user_id=user_id,
        username="test_user",
        role_id=1,
        role_code="TEST",
        permissions=frozenset(permissions),
    )


def _controller(
    permissions: set[str], user_id: int = 42
) -> Tuple[AsistenciaController, _StubAsistenciaService]:
    stub = _StubAsistenciaService()
    ctrl = AsistenciaController(
        session=_session(permissions, user_id=user_id),
        permission_service=PermissionService(),
        asistencia_service=stub,  # type: ignore[arg-type]  # duck-typed
    )
    return ctrl, stub


# ── list_asistencias ────────────────────────────────────────────────────────


def test_list_asistencias_delega_sin_filtro_de_empleado() -> None:
    ctrl, stub = _controller({perms.VIEW_ATTENDANCE})
    result = ctrl.list_asistencias(desde="2026-04-01", hasta="2026-04-30")
    assert result.items[0].asistencia.empleado_id == 1
    assert stub.calls == [
        (
            "list_asistencias",
            (),
            {
                "desde": "2026-04-01",
                "hasta": "2026-04-30",
                "empleado_id": None,
                "limit": None,
            },
        )
    ]


def test_list_asistencias_delega_con_filtro_de_empleado() -> None:
    ctrl, stub = _controller({perms.VIEW_ATTENDANCE})
    ctrl.list_asistencias(desde="2026-04-01", hasta="2026-04-30", empleado_id=7)
    assert stub.calls[0][2]["empleado_id"] == 7


# ── list_empleados_para_filtro ──────────────────────────────────────────────


def test_list_empleados_para_filtro_delega() -> None:
    ctrl, stub = _controller({perms.VIEW_ATTENDANCE})
    tuplas = ctrl.list_empleados_para_filtro()
    assert tuplas == [(1, "Pérez Juan")]
    assert stub.calls == [("list_empleados_para_filtro", (), {})]


# ── update_observaciones ────────────────────────────────────────────────────


def test_update_observaciones_inyecta_actor_desde_sesion() -> None:
    """La vista no pasa user_id — el controller lo toma de la session."""
    ctrl, stub = _controller({perms.VIEW_ATTENDANCE}, user_id=77)
    ctrl.update_observaciones(asistencia_id=100, observaciones="Nota")
    assert stub.calls == [
        (
            "update_observaciones",
            (),
            {
                "asistencia_id": 100,
                "observaciones": "Nota",
                "actor_user_id": 77,
            },
        )
    ]


def test_update_observaciones_propaga_asistencia_not_found() -> None:
    ctrl, stub = _controller({perms.VIEW_ATTENDANCE})
    stub.update_raises = AsistenciaNotFoundError(999)
    with pytest.raises(AsistenciaNotFoundError):
        ctrl.update_observaciones(999, "X")


# ── Guardas de permisos ─────────────────────────────────────────────────────


def test_sin_permiso_list_asistencias_lanza_denied() -> None:
    # Usuario con MANAGE_EMPLOYEES pero SIN VIEW_ATTENDANCE.
    ctrl, stub = _controller({perms.MANAGE_EMPLOYEES})
    with pytest.raises(PermissionDeniedError):
        ctrl.list_asistencias(desde="2026-04-01", hasta="2026-04-30")
    assert stub.calls == []  # no debe llegar al service


def test_sin_permiso_list_empleados_para_filtro_lanza_denied() -> None:
    ctrl, stub = _controller({perms.MANAGE_EMPLOYEES})
    with pytest.raises(PermissionDeniedError):
        ctrl.list_empleados_para_filtro()
    assert stub.calls == []


def test_sin_permiso_update_observaciones_lanza_denied() -> None:
    ctrl, stub = _controller({perms.MANAGE_EMPLOYEES})
    with pytest.raises(PermissionDeniedError):
        ctrl.update_observaciones(1, "X")
    assert stub.calls == []


# ── re_consolidar / puede_re_consolidar (Sub-3.4c) ──────────────────────────


def test_re_consolidar_delega_e_inyecta_actor() -> None:
    """Delega al service con empleado_id + ``actor_user_id`` desde la sesión."""
    ctrl, stub = _controller({perms.RUN_ZKTECO_SYNC}, user_id=55)
    resultado = ctrl.re_consolidar(desde="2026-04-13", hasta="2026-04-15", empleado_id=7)
    assert resultado.asistencias_upsertadas == 3
    assert stub.calls == [
        (
            "re_consolidar",
            (),
            {
                "desde": "2026-04-13",
                "hasta": "2026-04-15",
                "empleado_id": 7,
                "actor_user_id": 55,
            },
        )
    ]


def test_re_consolidar_sin_filtro_pasa_empleado_id_none() -> None:
    """Sin filtro de empleado, ``empleado_id`` se propaga como ``None``."""
    ctrl, stub = _controller({perms.RUN_ZKTECO_SYNC})
    ctrl.re_consolidar(desde="2026-04-13", hasta="2026-04-15")
    assert stub.calls[0][2]["empleado_id"] is None


def test_re_consolidar_sin_permiso_lanza_denied() -> None:
    """Sin RUN_ZKTECO_SYNC (incluso teniendo VIEW_ATTENDANCE), lanza Denied."""
    ctrl, stub = _controller({perms.VIEW_ATTENDANCE})
    with pytest.raises(PermissionDeniedError):
        ctrl.re_consolidar(desde="2026-04-13", hasta="2026-04-15")
    assert stub.calls == []  # no debe llegar al service


def test_puede_re_consolidar_true_con_run_sync() -> None:
    ctrl, _ = _controller({perms.RUN_ZKTECO_SYNC})
    assert ctrl.puede_re_consolidar() is True


def test_puede_re_consolidar_false_sin_run_sync() -> None:
    """REPORTES (sin RUN_ZKTECO_SYNC) → la vista oculta el botón."""
    ctrl, _ = _controller({perms.EXPORT_REPORTS})
    assert ctrl.puede_re_consolidar() is False
