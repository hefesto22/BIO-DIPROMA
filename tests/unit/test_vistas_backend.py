"""Tests de las consultas nuevas que alimentan Empleados, Turnos y Sincronización.

Usa una BD real sembrada con ``bin.seed_demo`` (1 semana): así se prueban
los servicios y controllers con el mismo cableado que usa la app.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest

from bin import seed_demo
from core.models import permissions as perms
from core.services.errors import PermissionDeniedError
from core.services.permission_service import PermissionService
from core.services.session import Session
from infrastructure.database.connection import Database
from ui.controllers.empleados_controller import EmpleadosController
from ui.controllers.sincronizacion_controller import SincronizacionController
from ui.controllers.turnos_controller import TurnosController


@pytest.fixture(scope="module")
def servicios(tmp_path_factory: pytest.TempPathFactory) -> Iterator[seed_demo._Servicios]:
    db_path: Path = tmp_path_factory.mktemp("demo") / "zkteco_app.db"
    assert seed_demo.main(["--db-path", str(db_path), "--semanas", "1"]) == 0
    yield seed_demo._Servicios(Database(db_path))


def _sesion(*permisos: str) -> Session:
    return Session(
        user_id=1,
        username="admin",
        role_id=1,
        role_code=perms.ROLE_SUPERADMIN,
        permissions=frozenset(permisos),
    )


def test_turnos_vigentes_por_empleado_agrupa_asignaciones_abiertas(
    servicios: seed_demo._Servicios,
) -> None:
    turnos = [t.id for t in servicios.turnos.list_turnos(solo_activos=False) if t.id]

    mapa = servicios.empleados.turnos_vigentes_por_empleado(turnos)

    # Los 20 empleados de la demo tienen UN turno (el sábado va dentro del mismo turno).
    assert len(mapa) == 20
    assert sorted({len(v) for v in mapa.values()}) == [1]
    assert all(a.fecha_fin is None for lista in mapa.values() for a in lista)


def test_mapa_turnos_vigentes_del_controller(servicios: seed_demo._Servicios) -> None:
    ctrl = EmpleadosController(
        session=_sesion(perms.MANAGE_EMPLOYEES),
        permission_service=PermissionService(),
        empleado_service=servicios.empleados,
        catalogo_service=servicios.catalogo,
        turno_service=servicios.turnos,
    )
    mapa = ctrl.mapa_turnos_vigentes()
    activos = ctrl.list_empleados(solo_activos=True)
    assert all(e.id in mapa for e in activos)


def test_empleados_por_turno_cuenta_solo_activos(servicios: seed_demo._Servicios) -> None:
    ctrl = TurnosController(
        session=_sesion(perms.MANAGE_EMPLOYEES),
        permission_service=PermissionService(),
        turno_service=servicios.turnos,
        empleado_service=servicios.empleados,
    )
    por_turno = ctrl.empleados_por_turno()
    nombres = {t.id: t.nombre for t in ctrl.list_turnos(solo_activos=False)}
    conteo = {nombres[k]: len(v) for k, v in por_turno.items()}
    assert conteo["Administrativo"] == 11
    assert conteo["Gerencia"] == 1
    assert conteo["Operativo"] == 8
    assert all(e.is_active for lista in por_turno.values() for e in lista)


def test_empleados_por_turno_sin_servicio_devuelve_vacio(servicios: seed_demo._Servicios) -> None:
    ctrl = TurnosController(
        session=_sesion(perms.MANAGE_EMPLOYEES),
        permission_service=PermissionService(),
        turno_service=servicios.turnos,
    )
    assert ctrl.empleados_por_turno() == {}


def test_empleados_por_turno_requiere_permiso(servicios: seed_demo._Servicios) -> None:
    ctrl = TurnosController(
        session=_sesion(perms.VIEW_ATTENDANCE),
        permission_service=PermissionService(),
        turno_service=servicios.turnos,
        empleado_service=servicios.empleados,
    )
    with pytest.raises(PermissionDeniedError):
        ctrl.empleados_por_turno()


def test_historial_y_nombres_de_relojes(servicios: seed_demo._Servicios) -> None:
    db = servicios.sincronizacion._sync_read  # noqa: SLF001 — mismo repo que usa la app
    ctrl = SincronizacionController(
        session=_sesion(perms.RUN_ZKTECO_SYNC),
        permission_service=PermissionService(),
        sincronizacion_service=servicios.sincronizacion,
        dispositivo_read=servicios.sincronizacion._dispositivo_read,  # noqa: SLF001
        sincronizacion_read=db,
    )
    historial = ctrl.historial()
    assert len(historial) == 1
    assert historial[0].estado == "OK"
    assert ctrl.nombres_dispositivos() == {historial[0].dispositivo_id: "Reloj Principal K40"}


def test_historial_requiere_permiso(servicios: seed_demo._Servicios) -> None:
    ctrl = SincronizacionController(
        session=_sesion(perms.VIEW_ATTENDANCE),
        permission_service=PermissionService(),
        sincronizacion_service=servicios.sincronizacion,
        dispositivo_read=servicios.sincronizacion._dispositivo_read,  # noqa: SLF001
        sincronizacion_read=servicios.sincronizacion._sync_read,  # noqa: SLF001
    )
    with pytest.raises(PermissionDeniedError):
        ctrl.historial()
