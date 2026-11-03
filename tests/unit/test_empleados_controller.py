"""Tests del EmpleadosController.

El controller es un wrapper delgado sobre ``EmpleadoService`` +
``CatalogoService`` + ``TurnoService`` (ya testeados), salvo por el
método ``create_empleado_con_turno`` que orquesta 3 llamadas (Opción A'
aprobada en Sub-2.6). Aquí validamos:

    - Cada método decorado delega al service con los argumentos correctos
      y pasa ``session.user_id`` como ``actor_user_id``.
    - Sin el permiso ``MANAGE_EMPLOYEES``, cualquier método lanza
      ``PermissionDeniedError`` sin tocar los services.
    - La orquestación de ``create_empleado_con_turno`` respeta los
      cinco caminos: turno inactivo, turno no existe, create falla,
      asignar_turno falla (compensación con warning), happy path.

Usamos stubs manuales (no MagicMock) para mantener los tests legibles,
alineado con ``test_turnos_controller``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pytest

from core.models import permissions as perms
from core.models.cargo import Cargo
from core.models.departamento import Departamento
from core.models.empleado import Empleado
from core.models.empleado_turno import EmpleadoTurno
from core.models.turno import DIAS_LABORALES, Turno
from core.services.errors import (
    EmpleadoNotFoundError,
    PermissionDeniedError,
    TurnoInactiveError,
    TurnoNotFoundError,
)
from core.services.permission_service import PermissionService
from core.services.session import Session
from ui.controllers.empleados_controller import (
    CreateEmpleadoResult,
    EmpleadosController,
)


# ── Fábricas de modelos ─────────────────────────────────────────────────────


def _empleado(emp_id: int = 1, dni: str = "0801-1990-12345") -> Empleado:
    return Empleado(
        id=emp_id,
        dni=dni,
        nombres="Juan",
        apellidos="Pérez",
        departamento_id=1,
        cargo_id=2,
        fecha_ingreso="2026-01-01",
        is_active=True,
    )


def _turno(turno_id: int = 1, is_active: bool = True) -> Turno:
    return Turno(
        id=turno_id,
        nombre="Admin 8-5",
        hora_entrada="08:00",
        hora_salida="17:00",
        minutos_descanso=60,
        dias_semana=DIAS_LABORALES,
        cruza_medianoche=False,
        is_active=is_active,
    )


def _empleado_turno(emp_id: int = 1, turno_id: int = 1) -> EmpleadoTurno:
    return EmpleadoTurno(
        id=10,
        empleado_id=emp_id,
        turno_id=turno_id,
        fecha_inicio="2026-01-01",
        fecha_fin=None,
    )


def _departamento(dep_id: int = 1, is_active: bool = True) -> Departamento:
    return Departamento(id=dep_id, nombre="Administración", is_active=is_active)


def _cargo(cargo_id: int = 2, is_active: bool = True) -> Cargo:
    return Cargo(id=cargo_id, nombre="Cajero", is_active=is_active)


# ── Stubs de servicios ──────────────────────────────────────────────────────


class _StubEmpleadoService:
    """Stub del EmpleadoService. Duck-typed, registra cada llamada."""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, Tuple[Any, ...], Dict[str, Any]]] = []
        self.create_raises: Optional[Exception] = None
        self.asignar_raises: Optional[Exception] = None

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))

    def list_empleados(self, solo_activos: bool = True) -> List[Empleado]:
        self._record("list_empleados", solo_activos=solo_activos)
        return [_empleado()]

    def get_empleado(self, empleado_id: int) -> Empleado:
        self._record("get_empleado", empleado_id)
        return _empleado(emp_id=empleado_id)

    def get_turno_vigente(self, empleado_id: int) -> Optional[EmpleadoTurno]:
        self._record("get_turno_vigente", empleado_id)
        return _empleado_turno(emp_id=empleado_id)

    def get_historial_turnos(self, empleado_id: int) -> List[EmpleadoTurno]:
        self._record("get_historial_turnos", empleado_id)
        return [_empleado_turno(emp_id=empleado_id)]

    def create_empleado(self, **kwargs: Any) -> Empleado:
        self._record("create_empleado", **kwargs)
        if self.create_raises is not None:
            raise self.create_raises
        return _empleado(emp_id=99, dni=kwargs["dni"])

    def update_empleado(self, **kwargs: Any) -> None:
        self._record("update_empleado", **kwargs)

    def deactivate_empleado(self, **kwargs: Any) -> None:
        self._record("deactivate_empleado", **kwargs)

    def reactivate_empleado(self, **kwargs: Any) -> None:
        self._record("reactivate_empleado", **kwargs)

    def asignar_turno(self, **kwargs: Any) -> EmpleadoTurno:
        self._record("asignar_turno", **kwargs)
        if self.asignar_raises is not None:
            raise self.asignar_raises
        return _empleado_turno(emp_id=kwargs["empleado_id"], turno_id=kwargs["turno_id"])

    def cambiar_turno(self, **kwargs: Any) -> EmpleadoTurno:
        self._record("cambiar_turno", **kwargs)
        return _empleado_turno(emp_id=kwargs["empleado_id"], turno_id=kwargs["turno_nuevo_id"])


class _StubCatalogoService:
    """Stub del CatalogoService."""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, Tuple[Any, ...], Dict[str, Any]]] = []

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))

    def list_departamentos(self, solo_activos: bool = True) -> List[Departamento]:
        self._record("list_departamentos", solo_activos=solo_activos)
        return [_departamento()]

    def list_cargos(self, solo_activos: bool = True) -> List[Cargo]:
        self._record("list_cargos", solo_activos=solo_activos)
        return [_cargo()]


class _StubTurnoService:
    """Stub del TurnoService (solo lo que usa el controller de empleados)."""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, Tuple[Any, ...], Dict[str, Any]]] = []
        self.get_turno_result: Turno = _turno()
        self.get_turno_raises: Optional[Exception] = None

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))

    def list_turnos(self, solo_activos: bool = True) -> List[Turno]:
        self._record("list_turnos", solo_activos=solo_activos)
        return [_turno()]

    def get_turno(self, turno_id: int) -> Turno:
        self._record("get_turno", turno_id)
        if self.get_turno_raises is not None:
            raise self.get_turno_raises
        return self.get_turno_result


# ── Factorías de controller + session ───────────────────────────────────────


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
) -> Tuple[EmpleadosController, _StubEmpleadoService, _StubCatalogoService, _StubTurnoService]:
    emp_stub = _StubEmpleadoService()
    cat_stub = _StubCatalogoService()
    tur_stub = _StubTurnoService()
    ctrl = EmpleadosController(
        session=_session(permissions, user_id=user_id),
        permission_service=PermissionService(),
        empleado_service=emp_stub,  # type: ignore[arg-type]  # duck-typed
        catalogo_service=cat_stub,  # type: ignore[arg-type]  # duck-typed
        turno_service=tur_stub,  # type: ignore[arg-type]  # duck-typed
    )
    return ctrl, emp_stub, cat_stub, tur_stub


# ── Reads delegan al service correcto ───────────────────────────────────────


def test_list_empleados_delega_default_activos() -> None:
    ctrl, emp, _c, _t = _controller({perms.MANAGE_EMPLOYEES})
    result = ctrl.list_empleados()
    assert len(result) == 1
    assert emp.calls == [("list_empleados", (), {"solo_activos": True})]


def test_list_empleados_pasa_flag() -> None:
    ctrl, emp, _c, _t = _controller({perms.MANAGE_EMPLOYEES})
    ctrl.list_empleados(solo_activos=False)
    assert emp.calls == [("list_empleados", (), {"solo_activos": False})]


def test_get_empleado_delega() -> None:
    ctrl, emp, _c, _t = _controller({perms.MANAGE_EMPLOYEES})
    result = ctrl.get_empleado(7)
    assert result.id == 7
    assert emp.calls == [("get_empleado", (7,), {})]


def test_get_turno_vigente_delega() -> None:
    ctrl, emp, _c, _t = _controller({perms.MANAGE_EMPLOYEES})
    ctrl.get_turno_vigente(3)
    assert emp.calls == [("get_turno_vigente", (3,), {})]


def test_get_historial_turnos_delega() -> None:
    ctrl, emp, _c, _t = _controller({perms.MANAGE_EMPLOYEES})
    ctrl.get_historial_turnos(3)
    assert emp.calls == [("get_historial_turnos", (3,), {})]


def test_list_departamentos_delega_catalogo() -> None:
    ctrl, _e, cat, _t = _controller({perms.MANAGE_EMPLOYEES})
    ctrl.list_departamentos()
    assert cat.calls == [("list_departamentos", (), {"solo_activos": True})]


def test_list_cargos_delega_catalogo() -> None:
    ctrl, _e, cat, _t = _controller({perms.MANAGE_EMPLOYEES})
    ctrl.list_cargos(solo_activos=False)
    assert cat.calls == [("list_cargos", (), {"solo_activos": False})]


def test_list_turnos_delega_turno_service() -> None:
    ctrl, _e, _c, tur = _controller({perms.MANAGE_EMPLOYEES})
    ctrl.list_turnos()
    assert tur.calls == [("list_turnos", (), {"solo_activos": True})]


# ── create_empleado_con_turno: orquestación (5 caminos) ─────────────────────


def test_create_con_turno_happy_path() -> None:
    ctrl, emp, _c, tur = _controller({perms.MANAGE_EMPLOYEES}, user_id=77)
    result = ctrl.create_empleado_con_turno(
        dni="0801-1990-11111",
        nombres="Ana",
        apellidos="López",
        departamento_id=1,
        cargo_id=2,
        fecha_ingreso="2026-04-24",
        turno_id=5,
        fecha_inicio_turno="2026-04-25",
    )
    assert isinstance(result, CreateEmpleadoResult)
    assert result.warning is None
    assert result.empleado.dni == "0801-1990-11111"
    # Validación previa con get_turno (1 sola llamada después del refactor).
    assert tur.calls == [("get_turno", (5,), {})]
    # Orden de llamadas al empleado service: create → asignar.
    assert [c[0] for c in emp.calls] == ["create_empleado", "asignar_turno"]
    # actor_user_id se propaga en ambas.
    assert emp.calls[0][2]["actor_user_id"] == 77
    assert emp.calls[1][2]["actor_user_id"] == 77
    # Opcionales vienen como None si no se pasan.
    assert emp.calls[0][2]["telefono"] is None
    assert emp.calls[0][2]["email"] is None
    assert emp.calls[0][2]["zkteco_id"] is None


def test_create_con_turno_lanza_si_turno_no_existe() -> None:
    ctrl, emp, _c, tur = _controller({perms.MANAGE_EMPLOYEES})
    tur.get_turno_raises = TurnoNotFoundError(99)
    with pytest.raises(TurnoNotFoundError):
        ctrl.create_empleado_con_turno(
            dni="0801-1990-11111",
            nombres="Ana",
            apellidos="López",
            departamento_id=1,
            cargo_id=2,
            fecha_ingreso="2026-04-24",
            turno_id=99,
            fecha_inicio_turno="2026-04-25",
        )
    # No se debe haber tocado al empleado service.
    assert emp.calls == []


def test_create_con_turno_lanza_si_turno_inactivo() -> None:
    ctrl, emp, _c, tur = _controller({perms.MANAGE_EMPLOYEES})
    tur.get_turno_result = _turno(turno_id=5, is_active=False)
    with pytest.raises(TurnoInactiveError):
        ctrl.create_empleado_con_turno(
            dni="0801-1990-11111",
            nombres="Ana",
            apellidos="López",
            departamento_id=1,
            cargo_id=2,
            fecha_ingreso="2026-04-24",
            turno_id=5,
            fecha_inicio_turno="2026-04-25",
        )
    assert emp.calls == []


def test_create_con_turno_propaga_error_de_create() -> None:
    ctrl, emp, _c, _t = _controller({perms.MANAGE_EMPLOYEES})
    emp.create_raises = EmpleadoNotFoundError(0)  # error arbitrario del service
    with pytest.raises(EmpleadoNotFoundError):
        ctrl.create_empleado_con_turno(
            dni="0801-1990-11111",
            nombres="Ana",
            apellidos="López",
            departamento_id=1,
            cargo_id=2,
            fecha_ingreso="2026-04-24",
            turno_id=5,
            fecha_inicio_turno="2026-04-25",
        )
    # Se intentó create pero no asignar (falló antes).
    assert [c[0] for c in emp.calls] == ["create_empleado"]


def test_create_con_turno_warning_si_asignar_falla() -> None:
    ctrl, emp, _c, _t = _controller({perms.MANAGE_EMPLOYEES})
    emp.asignar_raises = TurnoNotFoundError(5)
    result = ctrl.create_empleado_con_turno(
        dni="0801-1990-11111",
        nombres="Ana",
        apellidos="López",
        departamento_id=1,
        cargo_id=2,
        fecha_ingreso="2026-04-24",
        turno_id=5,
        fecha_inicio_turno="2026-04-25",
    )
    assert isinstance(result, CreateEmpleadoResult)
    assert result.empleado.dni == "0801-1990-11111"
    assert result.warning is not None
    assert "no se pudo asignar el turno inicial" in result.warning
    # Create sí se invocó, asignar también (pero falló).
    assert [c[0] for c in emp.calls] == ["create_empleado", "asignar_turno"]


# ── Writes restantes delegan con el user_id correcto ────────────────────────


def test_update_empleado_delega_con_actor() -> None:
    ctrl, emp, _c, _t = _controller({perms.MANAGE_EMPLOYEES}, user_id=11)
    ctrl.update_empleado(
        empleado_id=3,
        dni="0801-1990-22222",
        nombres="María",
        apellidos="Gómez",
        departamento_id=1,
        cargo_id=2,
        fecha_ingreso="2026-02-01",
    )
    assert emp.calls[0][0] == "update_empleado"
    assert emp.calls[0][2]["empleado_id"] == 3
    assert emp.calls[0][2]["actor_user_id"] == 11


def test_deactivate_empleado_delega_con_actor() -> None:
    ctrl, emp, _c, _t = _controller({perms.MANAGE_EMPLOYEES}, user_id=11)
    ctrl.deactivate_empleado(
        empleado_id=3,
        fecha_baja="2026-04-24",
        motivo_baja="RENUNCIA",
        nota_baja=None,
    )
    assert emp.calls[0][0] == "deactivate_empleado"
    assert emp.calls[0][2]["empleado_id"] == 3
    assert emp.calls[0][2]["motivo_baja"] == "RENUNCIA"
    assert emp.calls[0][2]["actor_user_id"] == 11


def test_reactivate_empleado_delega_con_actor() -> None:
    ctrl, emp, _c, _t = _controller({perms.MANAGE_EMPLOYEES}, user_id=11)
    ctrl.reactivate_empleado(5)
    assert emp.calls == [("reactivate_empleado", (), {"empleado_id": 5, "actor_user_id": 11})]


def test_asignar_turno_delega_con_actor() -> None:
    ctrl, emp, _c, _t = _controller({perms.MANAGE_EMPLOYEES}, user_id=11)
    ctrl.asignar_turno(empleado_id=5, turno_id=7, fecha_inicio="2026-04-24")
    assert emp.calls == [
        (
            "asignar_turno",
            (),
            {
                "empleado_id": 5,
                "turno_id": 7,
                "fecha_inicio": "2026-04-24",
                "actor_user_id": 11,
            },
        )
    ]


def test_cambiar_turno_delega_con_actor() -> None:
    ctrl, emp, _c, _t = _controller({perms.MANAGE_EMPLOYEES}, user_id=11)
    ctrl.cambiar_turno(empleado_id=5, turno_nuevo_id=8, fecha_inicio_nueva="2026-05-01")
    assert emp.calls == [
        (
            "cambiar_turno",
            (),
            {
                "empleado_id": 5,
                "turno_nuevo_id": 8,
                "fecha_inicio_nueva": "2026-05-01",
                "actor_user_id": 11,
            },
        )
    ]


# ── Permisos: sin MANAGE_EMPLOYEES, todo falla ──────────────────────────────


def test_list_empleados_sin_permiso_lanza() -> None:
    ctrl, emp, _c, _t = _controller(set())
    with pytest.raises(PermissionDeniedError) as exc:
        ctrl.list_empleados()
    assert exc.value.permission == perms.MANAGE_EMPLOYEES
    assert emp.calls == []


def test_create_con_turno_sin_permiso_no_valida_turno() -> None:
    ctrl, emp, _c, tur = _controller({perms.VIEW_ATTENDANCE})
    with pytest.raises(PermissionDeniedError):
        ctrl.create_empleado_con_turno(
            dni="0801-1990-11111",
            nombres="Ana",
            apellidos="López",
            departamento_id=1,
            cargo_id=2,
            fecha_ingreso="2026-04-24",
            turno_id=5,
            fecha_inicio_turno="2026-04-25",
        )
    # Ni siquiera se consultó get_turno — el guard corta antes.
    assert tur.calls == []
    assert emp.calls == []


def test_deactivate_sin_permiso_lanza() -> None:
    ctrl, emp, _c, _t = _controller({perms.EXPORT_REPORTS})
    with pytest.raises(PermissionDeniedError):
        ctrl.deactivate_empleado(
            empleado_id=3,
            fecha_baja="2026-04-24",
            motivo_baja="RENUNCIA",
        )
    assert emp.calls == []


def test_reportes_role_no_puede_tocar_empleados() -> None:
    """REPORTES no tiene MANAGE_EMPLOYEES."""
    ctrl, emp, _c, _t = _controller({perms.EXPORT_REPORTS, perms.VIEW_EXPORT_HISTORY})
    with pytest.raises(PermissionDeniedError):
        ctrl.list_empleados()
    with pytest.raises(PermissionDeniedError):
        ctrl.reactivate_empleado(1)
    assert emp.calls == []


def test_operador_role_no_puede_tocar_empleados() -> None:
    """OPERADOR tiene run_zkteco + view_attendance, no MANAGE_EMPLOYEES."""
    ctrl, emp, _c, tur = _controller({perms.RUN_ZKTECO_SYNC, perms.VIEW_ATTENDANCE})
    with pytest.raises(PermissionDeniedError):
        ctrl.asignar_turno(empleado_id=1, turno_id=1, fecha_inicio="2026-04-24")
    with pytest.raises(PermissionDeniedError):
        ctrl.cambiar_turno(empleado_id=1, turno_nuevo_id=1, fecha_inicio_nueva="2026-04-24")
    assert emp.calls == []
    assert tur.calls == []
