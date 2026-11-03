"""Controller de Configuración: pasar personal al archivar y probar relojes."""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Tuple

import pytest

from core.models import permissions as perms
from core.services.catalogo_service import ResultadoMovimiento
from core.services.errors import PermissionDeniedError
from core.services.permission_service import PermissionService
from tests.unit.test_configuracion_controller import _session
from ui.controllers.configuracion_controller import ConfiguracionController

Llamada = Tuple[str, Tuple[Any, ...]]


class _Registro:
    def __init__(self) -> None:
        self.llamadas: List[Llamada] = []


class _Catalogo(_Registro):
    def archive_departamento_moviendo(self, *args: Any) -> ResultadoMovimiento:
        self.llamadas.append(("depto", args))
        return ResultadoMovimiento(empleados=3, cargos=2)

    def archive_cargo_moviendo(self, *args: Any) -> ResultadoMovimiento:
        self.llamadas.append(("cargo", args))
        return ResultadoMovimiento(empleados=1)

    def cargos_destino_para(self, *args: Any) -> List[Any]:
        self.llamadas.append(("destinos", args))
        return []


class _Diagnostico(_Registro):
    def probar(self, *args: Any) -> str:
        self.llamadas.append(("probar", args))
        return "diagnostico"

    def poner_en_hora(self, *args: Any) -> datetime:
        self.llamadas.append(("hora", args))
        return datetime(2026, 10, 31, 9, 0)


def _controller(
    permisos: set[str], con_diagnostico: bool = True
) -> Tuple[ConfiguracionController, _Catalogo, _Diagnostico]:
    catalogo, diagnostico = _Catalogo(), _Diagnostico()
    ctrl = ConfiguracionController(
        session=_session(permisos, user_id=7),
        permission_service=PermissionService(),
        catalogo_service=catalogo,  # type: ignore[arg-type]  # duck-typed
        dispositivo_service=object(),  # type: ignore[arg-type]
        diagnostico_reloj=diagnostico if con_diagnostico else None,  # type: ignore[arg-type]
    )
    return ctrl, catalogo, diagnostico


def test_pasar_y_archivar_delegan_con_el_usuario() -> None:
    ctrl, catalogo, _ = _controller({perms.MANAGE_SETTINGS})
    assert ctrl.archive_departamento_moviendo(1, 2).cargos == 2
    assert ctrl.archive_cargo_moviendo(5, 6).empleados == 1
    assert ctrl.cargos_destino_para(5) == []
    assert catalogo.llamadas == [
        ("depto", (1, 2, 7)),
        ("cargo", (5, 6, 7)),
        ("destinos", (5,)),
    ]


def test_relojes_delegan_con_el_usuario() -> None:
    ctrl, _, diagnostico = _controller({perms.MANAGE_SETTINGS})
    assert ctrl.puede_probar_relojes is True
    assert ctrl.probar_reloj(3) == "diagnostico"  # type: ignore[comparison-overlap]
    ctrl.poner_reloj_en_hora(3)
    assert diagnostico.llamadas == [("probar", (3,)), ("hora", (3, 7))]


def test_sin_permiso_no_toca_nada() -> None:
    ctrl, catalogo, diagnostico = _controller(set())
    with pytest.raises(PermissionDeniedError):
        ctrl.archive_departamento_moviendo(1, 2)
    with pytest.raises(PermissionDeniedError):
        ctrl.archive_cargo_moviendo(1, 2)
    with pytest.raises(PermissionDeniedError):
        ctrl.poner_reloj_en_hora(1)
    assert catalogo.llamadas == [] and diagnostico.llamadas == []


def test_sin_servicio_de_relojes() -> None:
    ctrl, _, _ = _controller({perms.MANAGE_SETTINGS}, con_diagnostico=False)
    assert ctrl.puede_probar_relojes is False
    with pytest.raises(RuntimeError):
        ctrl.probar_reloj(1)
    assert ctrl.empleados_activos() == []
