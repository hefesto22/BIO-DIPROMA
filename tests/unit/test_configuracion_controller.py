"""Tests del ConfiguracionController.

El controller es un wrapper delgado alrededor de ``CatalogoService`` y
``DispositivoConfigService`` (ambos ya testeados con SQLite real), así
que aquí validamos:

    - Cada método decorado delega al service con los argumentos correctos
      y pasa ``session.user_id`` como ``actor_user_id``.
    - Sin el permiso ``MANAGE_SETTINGS``, cualquier método lanza
      ``PermissionDeniedError`` sin tocar el service.

Usamos stubs manuales de ambos services (no MagicMock) para mantener los
tests explícitos y legibles — alineado con el patrón ya establecido en
Sub-2.5/2.6/3.4a.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import pytest

from core.models import permissions as perms
from core.models.cargo import Cargo
from core.models.departamento import Departamento
from core.models.dispositivo import Dispositivo
from core.services.errors import PermissionDeniedError
from core.services.permission_service import PermissionService
from core.services.session import Session
from ui.controllers.configuracion_controller import ConfiguracionController

# ── Stubs ─────────────────────────────────────────────────────────────────────


class _StubCatalogoService:
    """Stub manual del CatalogoService que registra cada llamada.

    No hereda del service real — el controller solo invoca métodos por
    nombre, así que un duck-typed stub es suficiente y evita arrastrar
    dependencias reales (repos, audit_logger) a los tests del controller.
    """

    def __init__(self) -> None:
        self.calls: List[Tuple[str, tuple[object, ...], dict[str, object]]] = []
        self._next_dep_id = 1
        self._next_cargo_id = 1

    def _record(self, name: str, *args: object, **kwargs: object) -> None:
        self.calls.append((name, args, kwargs))

    # Departamentos
    def list_departamentos(self, solo_activos: bool = True) -> List[Departamento]:
        self._record("list_departamentos", solo_activos=solo_activos)
        return [Departamento(id=1, nombre="Admin")]

    def create_departamento(self, nombre: str, actor_user_id: int) -> Departamento:
        self._record("create_departamento", nombre, actor_user_id)
        dep = Departamento(id=self._next_dep_id, nombre=nombre)
        self._next_dep_id += 1
        return dep

    def rename_departamento(
        self, departamento_id: int, nuevo_nombre: str, actor_user_id: int
    ) -> None:
        self._record("rename_departamento", departamento_id, nuevo_nombre, actor_user_id)

    def archive_departamento(self, departamento_id: int, actor_user_id: int) -> None:
        self._record("archive_departamento", departamento_id, actor_user_id)

    def unarchive_departamento(self, departamento_id: int, actor_user_id: int) -> None:
        self._record("unarchive_departamento", departamento_id, actor_user_id)

    # Cargos
    def list_cargos(self, solo_activos: bool = True) -> List[Cargo]:
        self._record("list_cargos", solo_activos=solo_activos)
        return [Cargo(id=1, nombre="Analista")]

    def create_cargo(
        self,
        nombre: str,
        actor_user_id: int,
        departamento_id: Optional[int] = None,
    ) -> Cargo:
        self._record("create_cargo", nombre, actor_user_id, departamento_id)
        cargo = Cargo(
            id=self._next_cargo_id,
            nombre=nombre,
            departamento_id=departamento_id,
        )
        self._next_cargo_id += 1
        return cargo

    def rename_cargo(self, cargo_id: int, nuevo_nombre: str, actor_user_id: int) -> None:
        self._record("rename_cargo", cargo_id, nuevo_nombre, actor_user_id)

    def archive_cargo(self, cargo_id: int, actor_user_id: int) -> None:
        self._record("archive_cargo", cargo_id, actor_user_id)

    def unarchive_cargo(self, cargo_id: int, actor_user_id: int) -> None:
        self._record("unarchive_cargo", cargo_id, actor_user_id)

    def set_cargo_departamento(
        self,
        cargo_id: int,
        departamento_id: Optional[int],
        actor_user_id: int,
    ) -> None:
        self._record("set_cargo_departamento", cargo_id, departamento_id, actor_user_id)

    def list_cargos_para_departamento(self, departamento_id: int) -> List[Cargo]:
        self._record("list_cargos_para_departamento", departamento_id)
        return [Cargo(id=11, nombre="Tesorero", departamento_id=departamento_id)]


class _StubDispositivoConfigService:
    """Stub manual del DispositivoConfigService."""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, tuple[object, ...], dict[str, object]]] = []
        self._next_id = 1

    def _record(self, name: str, *args: object, **kwargs: object) -> None:
        self.calls.append((name, args, kwargs))

    def list_dispositivos(self, solo_activos: bool = True) -> List[Dispositivo]:
        self._record("list_dispositivos", solo_activos=solo_activos)
        return [Dispositivo(id=1, nombre="Sede", ip="192.168.0.101", puerto=4370)]

    def create_dispositivo(
        self,
        nombre: str,
        ip: str,
        puerto: int,
        actor_user_id: int,
        **conexion: object,
    ) -> Dispositivo:
        self._record(
            "create_dispositivo",
            nombre=nombre,
            ip=ip,
            puerto=puerto,
            actor_user_id=actor_user_id,
            **conexion,
        )
        disp = Dispositivo(id=self._next_id, nombre=nombre, ip=ip, puerto=puerto)
        self._next_id += 1
        return disp

    def update_dispositivo(
        self,
        dispositivo_id: int,
        nombre: str,
        ip: str,
        puerto: int,
        actor_user_id: int,
        **conexion: object,
    ) -> None:
        self._record(
            "update_dispositivo",
            dispositivo_id=dispositivo_id,
            nombre=nombre,
            ip=ip,
            puerto=puerto,
            actor_user_id=actor_user_id,
            **conexion,
        )

    def archive_dispositivo(self, dispositivo_id: int, actor_user_id: int) -> None:
        self._record("archive_dispositivo", dispositivo_id, actor_user_id)

    def unarchive_dispositivo(self, dispositivo_id: int, actor_user_id: int) -> None:
        self._record("unarchive_dispositivo", dispositivo_id, actor_user_id)


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
) -> Tuple[ConfiguracionController, _StubCatalogoService, _StubDispositivoConfigService]:
    stub_cat = _StubCatalogoService()
    stub_disp = _StubDispositivoConfigService()
    ctrl = ConfiguracionController(
        session=_session(permissions, user_id=user_id),
        permission_service=PermissionService(),
        catalogo_service=stub_cat,  # type: ignore[arg-type]  # duck-typed
        dispositivo_service=stub_disp,  # type: ignore[arg-type]  # duck-typed
    )
    return ctrl, stub_cat, stub_disp


# ── Permiso correcto: delega + pasa user_id ──────────────────────────────────


def test_list_departamentos_delega_al_service() -> None:
    ctrl, stub, _stub_disp = _controller({perms.MANAGE_SETTINGS})
    result = ctrl.list_departamentos()
    assert len(result) == 1
    assert stub.calls == [("list_departamentos", (), {"solo_activos": True})]


def test_list_departamentos_con_archivados_pasa_flag() -> None:
    ctrl, stub, _stub_disp = _controller({perms.MANAGE_SETTINGS})
    ctrl.list_departamentos(solo_activos=False)
    assert stub.calls == [("list_departamentos", (), {"solo_activos": False})]


def test_create_departamento_pasa_user_id_como_actor() -> None:
    ctrl, stub, _stub_disp = _controller({perms.MANAGE_SETTINGS}, user_id=99)
    ctrl.create_departamento("RRHH")
    assert stub.calls == [("create_departamento", ("RRHH", 99), {})]


def test_rename_departamento_pasa_todos_los_args() -> None:
    ctrl, stub, _stub_disp = _controller({perms.MANAGE_SETTINGS}, user_id=7)
    ctrl.rename_departamento(5, "Nuevo nombre")
    assert stub.calls == [("rename_departamento", (5, "Nuevo nombre", 7), {})]


def test_archive_departamento_delega() -> None:
    ctrl, stub, _stub_disp = _controller({perms.MANAGE_SETTINGS}, user_id=3)
    ctrl.archive_departamento(10)
    assert stub.calls == [("archive_departamento", (10, 3), {})]


def test_unarchive_departamento_delega() -> None:
    ctrl, stub, _stub_disp = _controller({perms.MANAGE_SETTINGS}, user_id=3)
    ctrl.unarchive_departamento(10)
    assert stub.calls == [("unarchive_departamento", (10, 3), {})]


def test_create_cargo_delega() -> None:
    ctrl, stub, _stub_disp = _controller({perms.MANAGE_SETTINGS}, user_id=11)
    ctrl.create_cargo("Contador")
    # Sub-3.2.A: el controller pasa también departamento_id (None por default).
    assert stub.calls == [("create_cargo", ("Contador", 11, None), {})]


def test_create_cargo_con_departamento_delega() -> None:
    """Sub-3.2.A: ``departamento_id`` se propaga al servicio."""
    ctrl, stub, _stub_disp = _controller({perms.MANAGE_SETTINGS}, user_id=11)
    ctrl.create_cargo("Tesorero", departamento_id=5)
    assert stub.calls == [("create_cargo", ("Tesorero", 11, 5), {})]


def test_set_cargo_departamento_delega() -> None:
    """Sub-3.2.A: cambiar el depto de un cargo audita en el service."""
    ctrl, stub, _stub_disp = _controller({perms.MANAGE_SETTINGS}, user_id=11)
    ctrl.set_cargo_departamento(7, 5)
    assert stub.calls == [("set_cargo_departamento", (7, 5, 11), {})]


def test_list_cargos_para_departamento_delega() -> None:
    """Sub-3.2.A: el listado filtrado pasa al service."""
    ctrl, stub, _stub_disp = _controller({perms.MANAGE_SETTINGS}, user_id=11)
    cargos = ctrl.list_cargos_para_departamento(5)
    assert stub.calls == [("list_cargos_para_departamento", (5,), {})]
    assert len(cargos) == 1
    assert cargos[0].nombre == "Tesorero"


def test_archive_cargo_delega() -> None:
    ctrl, stub, _stub_disp = _controller({perms.MANAGE_SETTINGS}, user_id=11)
    ctrl.archive_cargo(8)
    assert stub.calls == [("archive_cargo", (8, 11), {})]


# ── Permiso faltante: lanza sin tocar el service ─────────────────────────────


def test_list_departamentos_sin_permiso_lanza_permission_denied() -> None:
    ctrl, stub, _stub_disp = _controller(set())
    with pytest.raises(PermissionDeniedError) as exc:
        ctrl.list_departamentos()
    assert exc.value.permission == perms.MANAGE_SETTINGS
    assert stub.calls == []


def test_create_departamento_sin_permiso_lanza_permission_denied() -> None:
    ctrl, stub, _stub_disp = _controller({perms.VIEW_ATTENDANCE})
    with pytest.raises(PermissionDeniedError):
        ctrl.create_departamento("X")
    assert stub.calls == []


def test_archive_cargo_sin_permiso_lanza_permission_denied() -> None:
    ctrl, stub, _stub_disp = _controller({perms.EXPORT_REPORTS})
    with pytest.raises(PermissionDeniedError):
        ctrl.archive_cargo(1)
    assert stub.calls == []


def test_reportes_role_no_puede_tocar_configuracion() -> None:
    """REPORTES tiene export_reports + view_export_history, no manage_settings."""
    ctrl, stub, _stub_disp = _controller({perms.EXPORT_REPORTS, perms.VIEW_EXPORT_HISTORY})
    with pytest.raises(PermissionDeniedError):
        ctrl.list_cargos()
    with pytest.raises(PermissionDeniedError):
        ctrl.create_departamento("X")
    assert stub.calls == []


def test_operador_role_no_puede_tocar_configuracion() -> None:
    """OPERADOR tiene run_zkteco_sync + view_attendance, no manage_settings."""
    ctrl, stub, _stub_disp = _controller({perms.RUN_ZKTECO_SYNC, perms.VIEW_ATTENDANCE})
    with pytest.raises(PermissionDeniedError):
        ctrl.list_departamentos()
    assert stub.calls == []


# ── Dispositivos: delegación + permisos ──────────────────────────────────────


def test_list_dispositivos_delega_al_service() -> None:
    ctrl, _stub, stub_disp = _controller({perms.MANAGE_SETTINGS})
    result = ctrl.list_dispositivos()
    assert len(result) == 1
    assert stub_disp.calls == [("list_dispositivos", (), {"solo_activos": True})]


def test_list_dispositivos_con_archivados_pasa_flag() -> None:
    ctrl, _stub, stub_disp = _controller({perms.MANAGE_SETTINGS})
    ctrl.list_dispositivos(solo_activos=False)
    assert stub_disp.calls == [("list_dispositivos", (), {"solo_activos": False})]


def test_create_dispositivo_pasa_user_id_como_actor() -> None:
    ctrl, _stub, stub_disp = _controller({perms.MANAGE_SETTINGS}, user_id=99)
    ctrl.create_dispositivo("Sede Principal", "192.168.0.101", 4370)
    assert stub_disp.calls == [
        (
            "create_dispositivo",
            (),
            {
                "nombre": "Sede Principal",
                "ip": "192.168.0.101",
                "puerto": 4370,
                "actor_user_id": 99,
                "modo": "DIRECTO",
                "clave_comunicacion": 0,
                "usar_udp": False,
                "numero_serie": None,
            },
        )
    ]


def test_update_dispositivo_pasa_todos_los_args() -> None:
    ctrl, _stub, stub_disp = _controller({perms.MANAGE_SETTINGS}, user_id=7)
    ctrl.update_dispositivo(5, "Sede X", "10.0.0.5", 4371)
    assert stub_disp.calls == [
        (
            "update_dispositivo",
            (),
            {
                "dispositivo_id": 5,
                "nombre": "Sede X",
                "ip": "10.0.0.5",
                "puerto": 4371,
                "actor_user_id": 7,
                "modo": None,
                "clave_comunicacion": None,
                "usar_udp": None,
                "numero_serie": None,
            },
        )
    ]


def test_archive_dispositivo_delega() -> None:
    ctrl, _stub, stub_disp = _controller({perms.MANAGE_SETTINGS}, user_id=3)
    ctrl.archive_dispositivo(10)
    assert stub_disp.calls == [("archive_dispositivo", (10, 3), {})]


def test_unarchive_dispositivo_delega() -> None:
    ctrl, _stub, stub_disp = _controller({perms.MANAGE_SETTINGS}, user_id=3)
    ctrl.unarchive_dispositivo(10)
    assert stub_disp.calls == [("unarchive_dispositivo", (10, 3), {})]


def test_create_dispositivo_sin_permiso_lanza_permission_denied() -> None:
    ctrl, _stub, stub_disp = _controller({perms.VIEW_ATTENDANCE})
    with pytest.raises(PermissionDeniedError) as exc:
        ctrl.create_dispositivo("X", "1.2.3.4", 4370)
    assert exc.value.permission == perms.MANAGE_SETTINGS
    assert stub_disp.calls == []


def test_archive_dispositivo_sin_permiso_lanza_permission_denied() -> None:
    ctrl, _stub, stub_disp = _controller({perms.EXPORT_REPORTS})
    with pytest.raises(PermissionDeniedError):
        ctrl.archive_dispositivo(1)
    assert stub_disp.calls == []


def test_operador_role_no_puede_administrar_dispositivos() -> None:
    """OPERADOR ejecuta sync pero NO administra dispositivos."""
    ctrl, _stub, stub_disp = _controller({perms.RUN_ZKTECO_SYNC})
    with pytest.raises(PermissionDeniedError):
        ctrl.list_dispositivos()
    with pytest.raises(PermissionDeniedError):
        ctrl.create_dispositivo("X", "1.2.3.4", 4370)
    assert stub_disp.calls == []
