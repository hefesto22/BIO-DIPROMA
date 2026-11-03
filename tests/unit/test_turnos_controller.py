"""Tests del TurnosController.

El controller es un wrapper delgado alrededor de ``TurnoService``
(ya testeado en ``test_turno_service.py``), así que aquí validamos:

    - Cada método decorado delega al service con los argumentos correctos
      y pasa ``session.user_id`` como ``actor_user_id``.
    - Sin el permiso ``MANAGE_EMPLOYEES``, cualquier método lanza
      ``PermissionDeniedError`` sin tocar el service.

Usamos un stub manual del service (no MagicMock) para mantener los tests
explícitos y legibles — alineado con el patrón de ``test_configuracion_controller``
y ``test_main_controller``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pytest

from core.models import permissions as perms
from core.models.turno import DIAS_LABORALES, Turno
from core.services.errors import PermissionDeniedError
from core.services.permission_service import PermissionService
from core.services.session import Session
from ui.controllers.turnos_controller import TurnosController


# ── Stubs ─────────────────────────────────────────────────────────────────────


def _turno(turno_id: int = 1, nombre: str = "Admin 8-5") -> Turno:
    return Turno(
        id=turno_id,
        nombre=nombre,
        hora_entrada="08:00",
        hora_salida="17:00",
        minutos_descanso=60,
        dias_semana=DIAS_LABORALES,
        cruza_medianoche=False,
        is_active=True,
    )


class _StubTurnoService:
    """Stub manual del TurnoService que registra cada llamada.

    No hereda del service real — el controller solo invoca métodos por
    nombre, así que un duck-typed stub es suficiente y evita arrastrar
    dependencias reales (repos, audit_logger) a los tests del controller.
    """

    def __init__(self) -> None:
        self.calls: List[Tuple[str, Tuple[Any, ...], Dict[str, Any]]] = []
        self._next_id = 1

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))

    def list_turnos(self, solo_activos: bool = True) -> List[Turno]:
        self._record("list_turnos", solo_activos=solo_activos)
        return [_turno()]

    def get_turno(self, turno_id: int) -> Turno:
        self._record("get_turno", turno_id)
        return _turno(turno_id=turno_id)

    def create_turno(
        self,
        nombre: str,
        hora_entrada: str,
        hora_salida: str,
        minutos_descanso: int,
        dias_semana: int,
        actor_user_id: int,
        horarios_extra: object = None,
    ) -> Turno:
        self._record(
            "create_turno",
            nombre=nombre,
            hora_entrada=hora_entrada,
            hora_salida=hora_salida,
            minutos_descanso=minutos_descanso,
            dias_semana=dias_semana,
            actor_user_id=actor_user_id,
        )
        creado = _turno(turno_id=self._next_id, nombre=nombre)
        self._next_id += 1
        return creado

    def update_turno(
        self,
        turno_id: int,
        nombre: str,
        hora_entrada: str,
        hora_salida: str,
        minutos_descanso: int,
        dias_semana: int,
        actor_user_id: int,
        horarios_extra: object = None,
    ) -> None:
        self._record(
            "update_turno",
            turno_id=turno_id,
            nombre=nombre,
            hora_entrada=hora_entrada,
            hora_salida=hora_salida,
            minutos_descanso=minutos_descanso,
            dias_semana=dias_semana,
            actor_user_id=actor_user_id,
        )

    def archive_turno(self, turno_id: int, actor_user_id: int) -> None:
        self._record("archive_turno", turno_id, actor_user_id)

    def unarchive_turno(self, turno_id: int, actor_user_id: int) -> None:
        self._record("unarchive_turno", turno_id, actor_user_id)


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
) -> Tuple[TurnosController, _StubTurnoService]:
    stub = _StubTurnoService()
    ctrl = TurnosController(
        session=_session(permissions, user_id=user_id),
        permission_service=PermissionService(),
        turno_service=stub,  # type: ignore[arg-type]  # duck-typed
    )
    return ctrl, stub


# ── Permiso correcto: delega + pasa user_id ──────────────────────────────────


def test_list_turnos_delega_al_service() -> None:
    ctrl, stub = _controller({perms.MANAGE_EMPLOYEES})
    result = ctrl.list_turnos()
    assert len(result) == 1
    assert stub.calls == [("list_turnos", (), {"solo_activos": True})]


def test_list_turnos_con_archivados_pasa_flag() -> None:
    ctrl, stub = _controller({perms.MANAGE_EMPLOYEES})
    ctrl.list_turnos(solo_activos=False)
    assert stub.calls == [("list_turnos", (), {"solo_activos": False})]


def test_get_turno_delega() -> None:
    ctrl, stub = _controller({perms.MANAGE_EMPLOYEES})
    turno = ctrl.get_turno(7)
    assert turno.id == 7
    assert stub.calls == [("get_turno", (7,), {})]


def test_create_turno_pasa_user_id_como_actor() -> None:
    ctrl, stub = _controller({perms.MANAGE_EMPLOYEES}, user_id=99)
    creado = ctrl.create_turno(
        nombre="Vigilancia",
        hora_entrada="22:00",
        hora_salida="06:00",
        minutos_descanso=30,
        dias_semana=DIAS_LABORALES,
    )
    assert creado.nombre == "Vigilancia"
    assert stub.calls == [
        (
            "create_turno",
            (),
            {
                "nombre": "Vigilancia",
                "hora_entrada": "22:00",
                "hora_salida": "06:00",
                "minutos_descanso": 30,
                "dias_semana": DIAS_LABORALES,
                "actor_user_id": 99,
            },
        )
    ]


def test_update_turno_pasa_todos_los_args() -> None:
    ctrl, stub = _controller({perms.MANAGE_EMPLOYEES}, user_id=7)
    ctrl.update_turno(
        turno_id=5,
        nombre="Admin renombrado",
        hora_entrada="09:00",
        hora_salida="18:00",
        minutos_descanso=45,
        dias_semana=DIAS_LABORALES,
    )
    assert stub.calls == [
        (
            "update_turno",
            (),
            {
                "turno_id": 5,
                "nombre": "Admin renombrado",
                "hora_entrada": "09:00",
                "hora_salida": "18:00",
                "minutos_descanso": 45,
                "dias_semana": DIAS_LABORALES,
                "actor_user_id": 7,
            },
        )
    ]


def test_archive_turno_delega() -> None:
    ctrl, stub = _controller({perms.MANAGE_EMPLOYEES}, user_id=3)
    ctrl.archive_turno(10)
    assert stub.calls == [("archive_turno", (10, 3), {})]


def test_unarchive_turno_delega() -> None:
    ctrl, stub = _controller({perms.MANAGE_EMPLOYEES}, user_id=3)
    ctrl.unarchive_turno(10)
    assert stub.calls == [("unarchive_turno", (10, 3), {})]


# ── Permiso faltante: lanza sin tocar el service ─────────────────────────────


def test_list_turnos_sin_permiso_lanza_permission_denied() -> None:
    ctrl, stub = _controller(set())
    with pytest.raises(PermissionDeniedError) as exc:
        ctrl.list_turnos()
    assert exc.value.permission == perms.MANAGE_EMPLOYEES
    assert stub.calls == []


def test_create_turno_sin_permiso_lanza_permission_denied() -> None:
    ctrl, stub = _controller({perms.VIEW_ATTENDANCE})
    with pytest.raises(PermissionDeniedError):
        ctrl.create_turno(
            nombre="X",
            hora_entrada="08:00",
            hora_salida="17:00",
            minutos_descanso=0,
            dias_semana=DIAS_LABORALES,
        )
    assert stub.calls == []


def test_update_turno_sin_permiso_lanza_permission_denied() -> None:
    ctrl, stub = _controller({perms.EXPORT_REPORTS})
    with pytest.raises(PermissionDeniedError):
        ctrl.update_turno(
            turno_id=1,
            nombre="X",
            hora_entrada="08:00",
            hora_salida="17:00",
            minutos_descanso=0,
            dias_semana=DIAS_LABORALES,
        )
    assert stub.calls == []


def test_archive_turno_sin_permiso_lanza_permission_denied() -> None:
    ctrl, stub = _controller({perms.EXPORT_REPORTS})
    with pytest.raises(PermissionDeniedError):
        ctrl.archive_turno(1)
    assert stub.calls == []


def test_reportes_role_no_puede_tocar_turnos() -> None:
    """REPORTES tiene export_reports + view_export_history, no manage_employees."""
    ctrl, stub = _controller({perms.EXPORT_REPORTS, perms.VIEW_EXPORT_HISTORY})
    with pytest.raises(PermissionDeniedError):
        ctrl.list_turnos()
    with pytest.raises(PermissionDeniedError):
        ctrl.create_turno(
            nombre="X",
            hora_entrada="08:00",
            hora_salida="17:00",
            minutos_descanso=0,
            dias_semana=DIAS_LABORALES,
        )
    assert stub.calls == []


def test_operador_role_no_puede_tocar_turnos() -> None:
    """OPERADOR tiene run_zkteco_sync + view_attendance, no manage_employees."""
    ctrl, stub = _controller({perms.RUN_ZKTECO_SYNC, perms.VIEW_ATTENDANCE})
    with pytest.raises(PermissionDeniedError):
        ctrl.list_turnos()
    with pytest.raises(PermissionDeniedError):
        ctrl.archive_turno(1)
    assert stub.calls == []
