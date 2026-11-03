"""Tests del UsuariosController (Sub-2.7a).

Wrapper delgado del UsuarioAdminService — usamos stubs manuales
(no MagicMock) para verificar que cada método:
    - delega al service con los kwargs correctos,
    - propaga session.user_id como actor_user_id,
    - propaga session.role_code como actor_role_code,
    - lanza PermissionDeniedError sin tocar el service si falta MANAGE_USERS,
    - filtra SUPERADMIN del combo cuando el actor es ADMIN.
"""

from __future__ import annotations

from typing import Any, List, Tuple

import pytest

from core.models import permissions as perms
from core.models.rol import Rol
from core.models.usuario import Usuario
from core.services.errors import PermissionDeniedError
from core.services.permission_service import PermissionService
from core.services.session import Session
from core.services.usuario_admin_service import UsuarioConRol
from ui.controllers.usuarios_controller import UsuariosController


# ── Stubs ─────────────────────────────────────────────────────────────────────


class _StubUsuarioAdminService:
    def __init__(self) -> None:
        self.calls: List[Tuple[str, tuple[object, ...], dict[str, object]]] = []

    def _record(self, name: str, *args: object, **kwargs: object) -> None:
        self.calls.append((name, args, kwargs))

    def list_usuarios(self, solo_activos: bool = False) -> List[UsuarioConRol]:
        self._record("list_usuarios", solo_activos=solo_activos)
        u = Usuario(
            id=1,
            username="x",
            password_hash="h",
            full_name="X",
            role_id=2,
            is_active=True,
        )
        return [UsuarioConRol(usuario=u, rol_code="ADMIN", rol_name="Administrador")]

    def create_usuario(
        self,
        username: str,
        full_name: str,
        role_id: int,
        password: str,
        actor_user_id: int,
        actor_role_code: str,
    ) -> Usuario:
        self._record(
            "create_usuario",
            username=username,
            full_name=full_name,
            role_id=role_id,
            password=password,
            actor_user_id=actor_user_id,
            actor_role_code=actor_role_code,
        )
        return Usuario(
            id=99,
            username=username,
            password_hash="hash",
            full_name=full_name,
            role_id=role_id,
            is_active=True,
        )

    def update_usuario(
        self,
        user_id: int,
        full_name: str,
        role_id: int,
        actor_user_id: int,
        actor_role_code: str,
    ) -> None:
        self._record(
            "update_usuario",
            user_id=user_id,
            full_name=full_name,
            role_id=role_id,
            actor_user_id=actor_user_id,
            actor_role_code=actor_role_code,
        )

    def reset_password(
        self,
        user_id: int,
        new_password: str,
        actor_user_id: int,
        actor_role_code: str,
    ) -> None:
        self._record(
            "reset_password",
            user_id=user_id,
            new_password=new_password,
            actor_user_id=actor_user_id,
            actor_role_code=actor_role_code,
        )

    def deactivate_usuario(self, user_id: int, actor_user_id: int) -> None:
        self._record("deactivate_usuario", user_id, actor_user_id)

    def reactivate_usuario(self, user_id: int, actor_user_id: int) -> None:
        self._record("reactivate_usuario", user_id, actor_user_id)

    def unlock_usuario(self, user_id: int, actor_user_id: int) -> None:
        self._record("unlock_usuario", user_id, actor_user_id)


class _StubRolReadRepo:
    def __init__(self) -> None:
        self.calls: List[str] = []

    def list_all(self) -> List[Rol]:
        self.calls.append("list_all")
        return [
            Rol(id=1, code="SUPERADMIN", name="Super", description="", permissions=frozenset()),
            Rol(id=2, code="ADMIN", name="Admin", description="", permissions=frozenset()),
            Rol(id=3, code="REPORTES", name="Reportes", description="", permissions=frozenset()),
            Rol(id=4, code="OPERADOR", name="Operador", description="", permissions=frozenset()),
        ]

    def get_by_id(self, role_id: int) -> Rol | None:  # pragma: no cover
        return None

    def get_by_code(self, code: str) -> Rol | None:  # pragma: no cover
        return None


def _session(permissions: set[str], user_id: int = 42, role_code: str = "ADMIN") -> Session:
    return Session(
        user_id=user_id,
        username="test_user",
        role_id=1,
        role_code=role_code,
        permissions=frozenset(permissions),
    )


def _controller(
    permissions: set[str], user_id: int = 42, role_code: str = "ADMIN"
) -> Tuple[UsuariosController, _StubUsuarioAdminService, _StubRolReadRepo]:
    stub_svc = _StubUsuarioAdminService()
    stub_rol = _StubRolReadRepo()
    ctrl = UsuariosController(
        session=_session(permissions, user_id=user_id, role_code=role_code),
        permission_service=PermissionService(),
        usuario_admin_service=stub_svc,  # type: ignore[arg-type]
        rol_read=stub_rol,  # type: ignore[arg-type]
    )
    return ctrl, stub_svc, stub_rol


# ── Delegación + actor desde la sesión ───────────────────────────────────────


def test_create_usuario_propaga_actor_y_role() -> None:
    ctrl, stub, _ = _controller({perms.MANAGE_USERS}, user_id=99, role_code=perms.ROLE_ADMIN)
    ctrl.create_usuario("nuevo", "Nuevo X", role_id=2, password="Pass1234")
    assert stub.calls == [
        (
            "create_usuario",
            (),
            {
                "username": "nuevo",
                "full_name": "Nuevo X",
                "role_id": 2,
                "password": "Pass1234",
                "actor_user_id": 99,
                "actor_role_code": perms.ROLE_ADMIN,
            },
        )
    ]


def test_update_usuario_delega() -> None:
    ctrl, stub, _ = _controller({perms.MANAGE_USERS}, user_id=7, role_code=perms.ROLE_SUPERADMIN)
    ctrl.update_usuario(5, "Nuevo Nombre", role_id=3)
    assert stub.calls == [
        (
            "update_usuario",
            (),
            {
                "user_id": 5,
                "full_name": "Nuevo Nombre",
                "role_id": 3,
                "actor_user_id": 7,
                "actor_role_code": perms.ROLE_SUPERADMIN,
            },
        )
    ]


def test_reset_password_delega() -> None:
    ctrl, stub, _ = _controller({perms.MANAGE_USERS}, user_id=7, role_code=perms.ROLE_ADMIN)
    ctrl.reset_password(5, "NuevaPass99")
    assert stub.calls == [
        (
            "reset_password",
            (),
            {
                "user_id": 5,
                "new_password": "NuevaPass99",
                "actor_user_id": 7,
                "actor_role_code": perms.ROLE_ADMIN,
            },
        )
    ]


def test_deactivate_y_reactivate_delegan() -> None:
    ctrl, stub, _ = _controller({perms.MANAGE_USERS}, user_id=3)
    ctrl.deactivate_usuario(10)
    ctrl.reactivate_usuario(10)
    ctrl.unlock_usuario(10)
    assert stub.calls == [
        ("deactivate_usuario", (10, 3), {}),
        ("reactivate_usuario", (10, 3), {}),
        ("unlock_usuario", (10, 3), {}),
    ]


def test_list_usuarios_delega() -> None:
    ctrl, stub, _ = _controller({perms.MANAGE_USERS}, role_code=perms.ROLE_SUPERADMIN)
    res = ctrl.list_usuarios(solo_activos=True)
    assert len(res) == 1
    assert stub.calls == [("list_usuarios", (), {"solo_activos": True})]


def test_list_usuarios_admin_no_ve_al_superadmin() -> None:
    """Defensa R7: el ADMIN no debe poder ver al SUPERADMIN en la lista
    (info disclosure — aunque R1/R4 ya impiden tocarlo)."""

    class _StubConSuperadmin(_StubUsuarioAdminService):
        def list_usuarios(self, solo_activos: bool = False) -> List[UsuarioConRol]:
            self._record("list_usuarios", solo_activos=solo_activos)
            return [
                UsuarioConRol(
                    usuario=Usuario(
                        id=1,
                        username="admin",
                        password_hash="h",
                        full_name="A",
                        role_id=1,
                        is_active=True,
                    ),
                    rol_code=perms.ROLE_SUPERADMIN,
                    rol_name="Super Administrador",
                ),
                UsuarioConRol(
                    usuario=Usuario(
                        id=2,
                        username="prueba",
                        password_hash="h",
                        full_name="P",
                        role_id=2,
                        is_active=True,
                    ),
                    rol_code=perms.ROLE_ADMIN,
                    rol_name="Administrador",
                ),
            ]

    stub_svc = _StubConSuperadmin()
    stub_rol = _StubRolReadRepo()
    ctrl = UsuariosController(
        session=_session({perms.MANAGE_USERS}, role_code=perms.ROLE_ADMIN),
        permission_service=PermissionService(),
        usuario_admin_service=stub_svc,  # type: ignore[arg-type]
        rol_read=stub_rol,  # type: ignore[arg-type]
    )
    res = ctrl.list_usuarios()
    usernames = {u.usuario.username for u in res}
    assert usernames == {"prueba"}  # superadmin no aparece


def test_list_usuarios_superadmin_ve_a_todos() -> None:
    """El SUPERADMIN sí se ve a sí mismo y al resto."""

    class _StubConSuperadmin(_StubUsuarioAdminService):
        def list_usuarios(self, solo_activos: bool = False) -> List[UsuarioConRol]:
            self._record("list_usuarios", solo_activos=solo_activos)
            return [
                UsuarioConRol(
                    usuario=Usuario(
                        id=1,
                        username="admin",
                        password_hash="h",
                        full_name="A",
                        role_id=1,
                        is_active=True,
                    ),
                    rol_code=perms.ROLE_SUPERADMIN,
                    rol_name="Super Administrador",
                ),
                UsuarioConRol(
                    usuario=Usuario(
                        id=2,
                        username="prueba",
                        password_hash="h",
                        full_name="P",
                        role_id=2,
                        is_active=True,
                    ),
                    rol_code=perms.ROLE_ADMIN,
                    rol_name="Administrador",
                ),
            ]

    stub_svc = _StubConSuperadmin()
    stub_rol = _StubRolReadRepo()
    ctrl = UsuariosController(
        session=_session({perms.MANAGE_USERS}, role_code=perms.ROLE_SUPERADMIN),
        permission_service=PermissionService(),
        usuario_admin_service=stub_svc,  # type: ignore[arg-type]
        rol_read=stub_rol,  # type: ignore[arg-type]
    )
    res = ctrl.list_usuarios()
    usernames = {u.usuario.username for u in res}
    assert usernames == {"admin", "prueba"}


# ── Filtro de roles según rol del actor ──────────────────────────────────────


def test_list_roles_asignables_admin_excluye_superadmin() -> None:
    ctrl, _, _ = _controller({perms.MANAGE_USERS}, role_code=perms.ROLE_ADMIN)
    roles = ctrl.list_roles_asignables()
    codes = {r.code for r in roles}
    assert "SUPERADMIN" not in codes
    assert codes == {"ADMIN", "REPORTES", "OPERADOR"}


def test_list_roles_asignables_superadmin_ve_todos() -> None:
    ctrl, _, _ = _controller({perms.MANAGE_USERS}, role_code=perms.ROLE_SUPERADMIN)
    roles = ctrl.list_roles_asignables()
    codes = {r.code for r in roles}
    assert codes == {"SUPERADMIN", "ADMIN", "REPORTES", "OPERADOR"}


# ── Permisos: sin MANAGE_USERS lanza PermissionDeniedError ───────────────────


@pytest.mark.parametrize(
    "method,args",
    [
        ("list_usuarios", ()),
        ("list_roles_asignables", ()),
        ("create_usuario", ("u", "U", 2, "Pass1234")),
        ("update_usuario", (1, "U", 2)),
        ("reset_password", (1, "Pass1234")),
        ("deactivate_usuario", (1,)),
        ("reactivate_usuario", (1,)),
        ("unlock_usuario", (1,)),
    ],
)
def test_sin_permiso_lanza_permission_denied(method: str, args: Tuple[Any, ...]) -> None:
    ctrl, stub, _ = _controller(set())  # sin MANAGE_USERS
    with pytest.raises(PermissionDeniedError):
        getattr(ctrl, method)(*args)
    assert stub.calls == []


def test_admin_role_no_alcanza_si_falta_manage_users() -> None:
    """Tener rol ADMIN pero session sin manage_users = denied (defense)."""
    ctrl, stub, _ = _controller({perms.MANAGE_SETTINGS}, role_code=perms.ROLE_ADMIN)
    with pytest.raises(PermissionDeniedError):
        ctrl.list_usuarios()
    assert stub.calls == []
