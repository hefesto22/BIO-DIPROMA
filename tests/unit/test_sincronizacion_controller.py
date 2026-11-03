"""Tests del SincronizacionController (Sub-3.4a).

El controller es un wrapper delgado alrededor de:

    - ``SincronizacionService`` (ya testeado en ``test_sincronizacion_service.py``).
    - ``IDispositivoReadRepository`` (ya testeado en ``test_dispositivo_repository.py``).
    - ``ISincronizacionReadRepository`` (ya testeado en ``test_sincronizacion_repository.py``).

Se testea el pegamento:

    - Delegación correcta + inyección de ``session.user_id`` en ``iniciada_por``.
    - Guard ``RUN_ZKTECO_SYNC`` en cada método decorado.
    - Algoritmo del rango default: usa última OK, fallback 7 días, ignora
      EN_CURSO/FALLIDA, ignora filas con fecha corrupta.
    - Semántica del ``aviso_recuperacion``: se consume una vez y se limpia.

Usamos stubs manuales (no MagicMock) por consistencia con el patrón del
proyecto (``test_turnos_controller.py``, ``test_empleados_controller.py``).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import pytest

from core.models import permissions as perms
from core.models.dispositivo import Dispositivo
from core.models.sincronizacion import EstadoSincronizacion, Sincronizacion
from core.services.errors import (
    DispositivoInactiveError,
    DispositivoNotFoundError,
    PermissionDeniedError,
)
from core.services.permission_service import PermissionService
from core.services.session import Session
from core.services.sincronizacion_result import (
    MarcadaDesconocida,
    ResultadoConsolidacion,
    ResultadoSincronizacion,
)
from ui.controllers.sincronizacion_controller import SincronizacionController


# ── Stubs ─────────────────────────────────────────────────────────────────────


def _dispositivo(id_: int = 1, nombre: str = "Sede Principal") -> Dispositivo:
    return Dispositivo(id=id_, nombre=nombre, ip="10.0.0.1", puerto=4370, is_active=True)


def _sync_row(
    id_: int,
    dispositivo_id: int,
    rango_desde: str,
    rango_hasta: str,
    estado: str = EstadoSincronizacion.OK.value,
) -> Sincronizacion:
    """Fabrica una fila de sincronización para el historial de stubs."""
    return Sincronizacion(
        id=id_,
        dispositivo_id=dispositivo_id,
        iniciada_por_user_id=1,
        inicio="2026-04-20T10:00:00+00:00",
        rango_desde=rango_desde,
        rango_hasta=rango_hasta,
        fin="2026-04-20T10:01:00+00:00",
        registros_recibidos=0,
        estado=estado,
        error_mensaje=None,
    )


class _StubDispositivoReadRepo:
    """Devuelve ``list_active`` desde una lista interna."""

    def __init__(self, activos: Optional[List[Dispositivo]] = None) -> None:
        self._activos: List[Dispositivo] = activos or []
        self.calls: List[str] = []

    def list_active(self) -> List[Dispositivo]:
        self.calls.append("list_active")
        return list(self._activos)

    # Métodos no usados por el controller — quedan como NotImplementedError.
    def get_by_id(self, dispositivo_id: int) -> Optional[Dispositivo]:  # pragma: no cover
        raise NotImplementedError

    def get_by_nombre(self, nombre: str) -> Optional[Dispositivo]:  # pragma: no cover
        raise NotImplementedError

    def get_by_ip_puerto(self, ip: str, puerto: int) -> Optional[Dispositivo]:  # pragma: no cover
        raise NotImplementedError

    def list_all(self) -> List[Dispositivo]:  # pragma: no cover
        raise NotImplementedError


class _StubSincronizacionReadRepo:
    """Devuelve ``list_by_dispositivo`` desde un dict interno."""

    def __init__(self, historial: Optional[Dict[int, List[Sincronizacion]]] = None) -> None:
        self._historial: Dict[int, List[Sincronizacion]] = historial or {}
        self.calls: List[Tuple[str, Tuple[Any, ...], Dict[str, Any]]] = []

    def list_by_dispositivo(self, dispositivo_id: int, limit: int = 50) -> List[Sincronizacion]:
        self.calls.append(("list_by_dispositivo", (dispositivo_id,), {"limit": limit}))
        return list(self._historial.get(dispositivo_id, []))

    def get_by_id(self, sincronizacion_id: int) -> Optional[Sincronizacion]:  # pragma: no cover
        raise NotImplementedError

    def list_recientes(self, limit: int = 50) -> List[Sincronizacion]:  # pragma: no cover
        raise NotImplementedError

    def list_en_curso(self) -> List[Sincronizacion]:  # pragma: no cover
        raise NotImplementedError


class _StubSincronizacionService:
    """Stub manual: registra cada llamada a ``ejecutar``.

    No hereda del service real — el controller solo invoca ``ejecutar``
    por nombre, así que un duck-typed stub es suficiente.
    """

    def __init__(self, respuesta: Optional[ResultadoSincronizacion] = None) -> None:
        self.calls: List[Dict[str, Any]] = []
        self._respuesta = respuesta
        self._exc: Optional[BaseException] = None

    def preparar_error(self, exc: BaseException) -> None:
        self._exc = exc

    def ejecutar(
        self,
        dispositivo_id: int,
        rango_desde: str,
        rango_hasta: str,
        iniciada_por_user_id: Optional[int],
    ) -> ResultadoSincronizacion:
        self.calls.append(
            {
                "dispositivo_id": dispositivo_id,
                "rango_desde": rango_desde,
                "rango_hasta": rango_hasta,
                "iniciada_por_user_id": iniciada_por_user_id,
            }
        )
        if self._exc is not None:
            raise self._exc
        if self._respuesta is not None:
            return self._respuesta
        return ResultadoSincronizacion(
            sincronizacion_id=1,
            dispositivo_id=dispositivo_id,
            registros_recibidos=0,
        )

    def recover_huerfanas(self) -> int:  # pragma: no cover — no usado por controller
        raise NotImplementedError


def _session(permissions: set[str], user_id: int = 42) -> Session:
    return Session(
        user_id=user_id,
        username="test_user",
        role_id=1,
        role_code="TEST",
        permissions=frozenset(permissions),
    )


def _controller(
    permissions: set[str],
    user_id: int = 42,
    dispositivos: Optional[List[Dispositivo]] = None,
    historial: Optional[Dict[int, List[Sincronizacion]]] = None,
    respuesta: Optional[ResultadoSincronizacion] = None,
) -> Tuple[
    SincronizacionController,
    _StubSincronizacionService,
    _StubDispositivoReadRepo,
    _StubSincronizacionReadRepo,
]:
    disp_stub = _StubDispositivoReadRepo(dispositivos)
    sync_repo_stub = _StubSincronizacionReadRepo(historial)
    service_stub = _StubSincronizacionService(respuesta=respuesta)
    ctrl = SincronizacionController(
        session=_session(permissions, user_id=user_id),
        permission_service=PermissionService(),
        sincronizacion_service=service_stub,  # type: ignore[arg-type]
        dispositivo_read=disp_stub,  # type: ignore[arg-type]
        sincronizacion_read=sync_repo_stub,  # type: ignore[arg-type]
    )
    return ctrl, service_stub, disp_stub, sync_repo_stub


# ── list_dispositivos_activos ────────────────────────────────────────────────


def test_list_dispositivos_activos_delega_al_repo() -> None:
    dispositivos = [_dispositivo(1, "Sede"), _dispositivo(2, "Alcaldía")]
    ctrl, _, disp, _ = _controller({perms.RUN_ZKTECO_SYNC}, dispositivos=dispositivos)
    result = ctrl.list_dispositivos_activos()
    assert [d.nombre for d in result] == ["Sede", "Alcaldía"]
    assert disp.calls == ["list_active"]


def test_list_dispositivos_activos_sin_permiso_lanza() -> None:
    ctrl, _, disp, _ = _controller(set())
    with pytest.raises(PermissionDeniedError) as exc:
        ctrl.list_dispositivos_activos()
    assert exc.value.permission == perms.RUN_ZKTECO_SYNC
    assert disp.calls == []


# ── calcular_rango_default ───────────────────────────────────────────────────


def test_rango_default_usa_ultima_sync_ok_del_dispositivo() -> None:
    historial = {
        1: [
            # list_by_dispositivo devuelve DESC por inicio — la primera es la más reciente.
            _sync_row(5, 1, "2026-04-15", "2026-04-20"),
            _sync_row(4, 1, "2026-04-10", "2026-04-14"),
        ]
    }
    ctrl, _, _, _ = _controller({perms.RUN_ZKTECO_SYNC}, historial=historial)
    desde, hasta = ctrl.calcular_rango_default(1, hoy=date(2026, 4, 24))
    assert desde == "2026-04-20"
    assert hasta == "2026-04-24"


def test_rango_default_cae_a_7_dias_si_nunca_sincronizado() -> None:
    ctrl, _, _, _ = _controller({perms.RUN_ZKTECO_SYNC}, historial={})
    desde, hasta = ctrl.calcular_rango_default(1, hoy=date(2026, 4, 24))
    assert desde == "2026-04-17"  # hoy - 7 días
    assert hasta == "2026-04-24"


def test_rango_default_ignora_syncs_en_curso_y_fallidas() -> None:
    historial = {
        1: [
            _sync_row(7, 1, "2026-04-22", "2026-04-23", EstadoSincronizacion.EN_CURSO.value),
            _sync_row(6, 1, "2026-04-18", "2026-04-21", EstadoSincronizacion.FALLIDA.value),
            _sync_row(5, 1, "2026-04-10", "2026-04-15", EstadoSincronizacion.OK.value),
        ]
    }
    ctrl, _, _, _ = _controller({perms.RUN_ZKTECO_SYNC}, historial=historial)
    desde, hasta = ctrl.calcular_rango_default(1, hoy=date(2026, 4, 24))
    assert desde == "2026-04-15"  # última OK, no la EN_CURSO ni la FALLIDA
    assert hasta == "2026-04-24"


def test_rango_default_ignora_filas_con_fecha_corrupta() -> None:
    historial = {
        1: [
            _sync_row(9, 1, "NO_ES_FECHA", "TAMPOCO", EstadoSincronizacion.OK.value),
            _sync_row(8, 1, "2026-04-01", "2026-04-05", EstadoSincronizacion.OK.value),
        ]
    }
    ctrl, _, _, _ = _controller({perms.RUN_ZKTECO_SYNC}, historial=historial)
    desde, hasta = ctrl.calcular_rango_default(1, hoy=date(2026, 4, 24))
    assert desde == "2026-04-05"  # saltó la fila corrupta, tomó la siguiente OK
    assert hasta == "2026-04-24"


def test_rango_default_usa_date_today_si_no_se_pasa_hoy() -> None:
    # No assertions exactas de fecha — solo que el resultado sea hoy.
    ctrl, _, _, _ = _controller({perms.RUN_ZKTECO_SYNC}, historial={})
    desde, hasta = ctrl.calcular_rango_default(1)
    assert hasta == date.today().isoformat()
    assert desde < hasta


def test_rango_default_sin_permiso_lanza() -> None:
    ctrl, _, _, sync_repo = _controller(set())
    with pytest.raises(PermissionDeniedError):
        ctrl.calcular_rango_default(1)
    assert sync_repo.calls == []


# ── ejecutar_sincronizacion ──────────────────────────────────────────────────


def test_ejecutar_pasa_user_id_como_iniciada_por() -> None:
    respuesta = ResultadoSincronizacion(
        sincronizacion_id=10, dispositivo_id=1, registros_recibidos=42
    )
    ctrl, service, _, _ = _controller({perms.RUN_ZKTECO_SYNC}, user_id=77, respuesta=respuesta)
    resultado = ctrl.ejecutar_sincronizacion(
        dispositivo_id=1, rango_desde="2026-04-20", rango_hasta="2026-04-24"
    )
    assert resultado.sincronizacion_id == 10
    assert resultado.registros_recibidos == 42
    assert service.calls == [
        {
            "dispositivo_id": 1,
            "rango_desde": "2026-04-20",
            "rango_hasta": "2026-04-24",
            "iniciada_por_user_id": 77,
        }
    ]


def test_ejecutar_re_propaga_dispositivo_not_found() -> None:
    ctrl, service, _, _ = _controller({perms.RUN_ZKTECO_SYNC})
    service.preparar_error(DispositivoNotFoundError(999))
    with pytest.raises(DispositivoNotFoundError):
        ctrl.ejecutar_sincronizacion(
            dispositivo_id=999, rango_desde="2026-04-20", rango_hasta="2026-04-24"
        )


def test_ejecutar_re_propaga_dispositivo_inactive() -> None:
    ctrl, service, _, _ = _controller({perms.RUN_ZKTECO_SYNC})
    service.preparar_error(DispositivoInactiveError(1))
    with pytest.raises(DispositivoInactiveError):
        ctrl.ejecutar_sincronizacion(
            dispositivo_id=1, rango_desde="2026-04-20", rango_hasta="2026-04-24"
        )


def test_ejecutar_devuelve_resultado_con_consolidacion() -> None:
    desconocidas = [MarcadaDesconocida(zkteco_user_id=501, cantidad_marcadas=12)]
    consolidacion = ResultadoConsolidacion(
        asistencias_upsertadas=30,
        empleados_procesados=10,
        marcadas_desconocidas=desconocidas,
        dias_procesados=5,
    )
    respuesta = ResultadoSincronizacion(
        sincronizacion_id=11,
        dispositivo_id=1,
        registros_recibidos=100,
        consolidacion=consolidacion,
    )
    ctrl, _, _, _ = _controller({perms.RUN_ZKTECO_SYNC}, respuesta=respuesta)
    resultado = ctrl.ejecutar_sincronizacion(
        dispositivo_id=1, rango_desde="2026-04-20", rango_hasta="2026-04-24"
    )
    assert resultado.consolidacion is not None
    assert resultado.consolidacion.marcadas_desconocidas == desconocidas


def test_ejecutar_sin_permiso_lanza_permission_denied() -> None:
    ctrl, service, _, _ = _controller(set())
    with pytest.raises(PermissionDeniedError) as exc:
        ctrl.ejecutar_sincronizacion(
            dispositivo_id=1, rango_desde="2026-04-20", rango_hasta="2026-04-24"
        )
    assert exc.value.permission == perms.RUN_ZKTECO_SYNC
    assert service.calls == []


# ── aviso_recuperacion ───────────────────────────────────────────────────────


def test_consumir_aviso_recuperacion_devuelve_valor_y_limpia() -> None:
    ctrl, _, _, _ = _controller({perms.RUN_ZKTECO_SYNC})
    ctrl.aviso_recuperacion = 3
    assert ctrl.consumir_aviso_recuperacion() == 3
    # Segundo consumo: ya no hay aviso.
    assert ctrl.consumir_aviso_recuperacion() is None


def test_consumir_aviso_recuperacion_sin_aviso_devuelve_none() -> None:
    ctrl, _, _, _ = _controller({perms.RUN_ZKTECO_SYNC})
    assert ctrl.consumir_aviso_recuperacion() is None


def test_consumir_aviso_no_requiere_permiso() -> None:
    """Puro estado local — no invoca service, no debe decorarse con require_permission."""
    ctrl, _, _, _ = _controller(set())  # sesión sin permisos
    ctrl.aviso_recuperacion = 2
    # No debe lanzar PermissionDeniedError.
    assert ctrl.consumir_aviso_recuperacion() == 2


# ── Casos de rol específico ──────────────────────────────────────────────────


def test_reportes_role_no_puede_sincronizar() -> None:
    """REPORTES tiene export_reports + view_export_history, no run_zkteco_sync."""
    ctrl, service, _, _ = _controller({perms.EXPORT_REPORTS, perms.VIEW_EXPORT_HISTORY})
    with pytest.raises(PermissionDeniedError):
        ctrl.list_dispositivos_activos()
    with pytest.raises(PermissionDeniedError):
        ctrl.ejecutar_sincronizacion(1, "2026-04-20", "2026-04-24")
    assert service.calls == []


def test_operador_role_si_puede_sincronizar() -> None:
    """OPERADOR tiene run_zkteco_sync + view_attendance."""
    dispositivos = [_dispositivo(1, "Sede")]
    ctrl, _, _, _ = _controller(
        {perms.RUN_ZKTECO_SYNC, perms.VIEW_ATTENDANCE},
        dispositivos=dispositivos,
    )
    result = ctrl.list_dispositivos_activos()
    assert len(result) == 1
