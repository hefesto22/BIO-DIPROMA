"""Vacaciones, días libres, permisos e incapacidades (``AusenciaService``).

BD real migrada + servicios con el mismo cableado de la app (vía
``bin.seed_demo._Servicios``). Fechas de enero 2026 (siempre en el pasado):
al registrar, la asistencia de esos días se recalcula al momento.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

import config
from bin import seed_demo
from core.models import permissions as perms
from core.models.asistencia import Asistencia
from core.models.ausencia import Ausencia, TipoAusencia
from core.models.feriado import Feriado
from core.models.turno import DIAS_LABORALES, SABADO, HorarioTurno
from core.services.asistencia_exporter import calcular_resumen
from core.services.asistencia_service import AsistenciaVista
from core.services.errors import (
    AusenciaEmpleadoInactivoError,
    AusenciaNotFoundError,
    AusenciaSolapadaError,
    PermissionDeniedError,
    RangoAusenciaInvalidoError,
    TipoAusenciaInvalidoError,
)
from core.services.permission_service import PermissionService
from core.services.session import Session
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner
from ui import formato
from ui.controllers.ausencias_controller import AusenciasController

VAC = TipoAusencia.VACACIONES.value
PERMISO = TipoAusencia.PERMISO.value


@dataclass
class _Entorno:
    db: Database
    s: seed_demo._Servicios
    actor: int
    empleado_id: int


@pytest.fixture
def entorno(tmp_path: Path) -> _Entorno:
    db = Database(tmp_path / "t.db")
    MigrationsRunner(db, config.MIGRATIONS_DIR).run()
    s = seed_demo._Servicios(db)
    actor = seed_demo._crear_usuarios(s)
    dep = s.catalogo.create_departamento("Ventas", actor)
    assert dep.id is not None
    cargo = s.catalogo.create_cargo("Vendedor", actor, departamento_id=dep.id)
    assert cargo.id is not None
    turno = s.turnos.create_turno(
        "Administrativo",
        "09:00",
        "17:00",
        60,
        DIAS_LABORALES,
        actor,
        horarios_extra=[HorarioTurno(SABADO, "09:00", "12:00")],
    )
    emp = s.empleados.create_empleado(
        dni="0801-1990-00001",
        nombres="Karla",
        apellidos="Villeda",
        departamento_id=dep.id,
        cargo_id=cargo.id,
        fecha_ingreso="2025-06-02",
        actor_user_id=actor,
        zkteco_id=5,
    )
    assert turno.id is not None and emp.id is not None
    s.empleados.asignar_turno(emp.id, turno.id, "2025-06-02", actor)
    return _Entorno(db, s, actor, emp.id)


def _registrar(e: _Entorno, desde: str, hasta: str, tipo: str = VAC) -> Ausencia:
    return e.s.ausencias.registrar(e.empleado_id, tipo, desde, hasta, "  nota  ", e.actor)


def _estados(e: _Entorno, desde: str, hasta: str) -> dict[str, str]:
    with e.db.transaction() as conn:
        rows = conn.execute(
            "SELECT fecha, estado FROM asistencias WHERE empleado_id = ? AND fecha BETWEEN ? AND ?",
            (e.empleado_id, desde, hasta),
        ).fetchall()
    return {r["fecha"]: r["estado"] for r in rows}


# ── Registro y validaciones ───────────────────────────────────────────────────


def test_registrar_guarda_y_normaliza_la_nota(entorno: _Entorno) -> None:
    a = _registrar(entorno, "2026-01-12", "2026-01-16")
    assert a.id is not None
    leida = entorno.s.ausencias.get(a.id)
    assert (leida.tipo, leida.fecha_desde, leida.fecha_hasta, leida.nota) == (
        VAC,
        "2026-01-12",
        "2026-01-16",
        "nota",
    )
    assert leida.creado_por_user_id == entorno.actor and leida.creado_en
    assert entorno.s.ausencias.listar_por_empleado(entorno.empleado_id) == [leida]


@pytest.mark.parametrize(
    ("tipo", "desde", "hasta", "error"),
    [
        ("FERIADO", "2026-01-12", "2026-01-12", TipoAusenciaInvalidoError),
        (VAC, "2026-01-16", "2026-01-12", RangoAusenciaInvalidoError),
        (VAC, "2026-01-01", "2027-03-01", RangoAusenciaInvalidoError),
    ],
)
def test_rechaza_datos_invalidos(
    entorno: _Entorno, tipo: str, desde: str, hasta: str, error: type[Exception]
) -> None:
    with pytest.raises(error):
        entorno.s.ausencias.registrar(entorno.empleado_id, tipo, desde, hasta, None, entorno.actor)


def test_no_se_cruzan_dos_ausencias_del_mismo_empleado(entorno: _Entorno) -> None:
    _registrar(entorno, "2026-01-12", "2026-01-16")
    with pytest.raises(AusenciaSolapadaError, match="12/01/2026 al 16/01/2026"):
        _registrar(entorno, "2026-01-16", "2026-01-19", PERMISO)
    # Pegadas pero sin cruzarse: válido.
    _registrar(entorno, "2026-01-19", "2026-01-19", PERMISO)


def test_empleado_de_baja_no_recibe_ausencias(entorno: _Entorno) -> None:
    entorno.s.empleados.deactivate_empleado(
        empleado_id=entorno.empleado_id,
        fecha_baja="2026-01-31",
        motivo_baja="RENUNCIA",
        actor_user_id=entorno.actor,
    )
    with pytest.raises(AusenciaEmpleadoInactivoError):
        _registrar(entorno, "2026-02-02", "2026-02-02")


def test_get_de_id_inexistente(entorno: _Entorno) -> None:
    with pytest.raises(AusenciaNotFoundError):
        entorno.s.ausencias.get(999)


# ── Días laborables ───────────────────────────────────────────────────────────


def test_dias_laborables_excluye_domingos_y_feriados(entorno: _Entorno) -> None:
    entorno.s.feriados.create(Feriado(id=None, fecha="2026-01-14", descripcion="Feriado"))
    # Lunes 12 al domingo 18: lun-vie (5) + sáb (1) - feriado miércoles (1) = 5.
    assert entorno.s.ausencias.dias_laborables(entorno.empleado_id, "2026-01-12", "2026-01-18") == 5
    assert entorno.s.ausencias.dias_laborables(entorno.empleado_id, "2026-01-18", "2026-01-18") == 0


def test_resumen_anual_suma_por_tipo_dentro_del_anio(entorno: _Entorno) -> None:
    _registrar(entorno, "2025-12-29", "2026-01-02")  # cruza de año
    _registrar(entorno, "2026-02-02", "2026-02-02", PERMISO)
    resumen = entorno.s.ausencias.resumen_anual(entorno.empleado_id, 2026)
    assert resumen[VAC] == 2  # jue 1 y vie 2 de enero
    assert resumen[PERMISO] == 1


# ── Efecto en la asistencia ───────────────────────────────────────────────────


def test_registrar_en_el_pasado_marca_vacaciones_en_vez_de_ausente(entorno: _Entorno) -> None:
    entorno.s.consolidacion.consolidar_rango(
        "2026-01-12", "2026-01-18", empleado_id=entorno.empleado_id
    )
    assert set(_estados(entorno, "2026-01-12", "2026-01-16").values()) == {"AUSENTE"}

    _registrar(entorno, "2026-01-12", "2026-01-18")

    estados = _estados(entorno, "2026-01-12", "2026-01-18")
    assert [estados[f"2026-01-{d}"] for d in range(12, 18)] == ["VACACIONES"] * 6
    assert estados["2026-01-18"] == "SIN_TURNO"  # domingo: no consume vacaciones


def test_feriado_gana_a_las_vacaciones(entorno: _Entorno) -> None:
    entorno.s.feriados.create(Feriado(id=None, fecha="2026-01-14", descripcion="Feriado"))
    _registrar(entorno, "2026-01-12", "2026-01-16")
    assert _estados(entorno, "2026-01-14", "2026-01-14") == {"2026-01-14": "FERIADO"}


def test_eliminar_devuelve_los_dias_a_su_calculo_normal(entorno: _Entorno) -> None:
    a = _registrar(entorno, "2026-01-12", "2026-01-12", PERMISO)
    assert a.id is not None
    assert _estados(entorno, "2026-01-12", "2026-01-12") == {"2026-01-12": "PERMISO"}

    entorno.s.ausencias.eliminar(a.id, entorno.actor)

    assert _estados(entorno, "2026-01-12", "2026-01-12") == {"2026-01-12": "AUSENTE"}
    assert entorno.s.ausencias.listar() == []


def test_actualizar_recalcula_el_rango_viejo_y_el_nuevo(entorno: _Entorno) -> None:
    a = _registrar(entorno, "2026-01-12", "2026-01-13")
    assert a.id is not None
    entorno.s.ausencias.actualizar(a.id, PERMISO, "2026-01-13", "2026-01-14", None, entorno.actor)
    assert _estados(entorno, "2026-01-12", "2026-01-14") == {
        "2026-01-12": "AUSENTE",
        "2026-01-13": "PERMISO",
        "2026-01-14": "PERMISO",
    }
    assert entorno.s.ausencias.get(a.id).nota is None


def test_ausencia_futura_no_crea_asistencias_todavia(entorno: _Entorno) -> None:
    futuro = date.today().replace(year=date.today().year + 1).isoformat()
    _registrar(entorno, futuro, futuro)
    assert _estados(entorno, futuro, futuro) == {}


def test_queda_en_la_auditoria(entorno: _Entorno) -> None:
    a = _registrar(entorno, "2026-01-12", "2026-01-12")
    assert a.id is not None
    entorno.s.ausencias.eliminar(a.id, entorno.actor)
    with entorno.db.transaction() as conn:
        acciones = [
            r["action"]
            for r in conn.execute(
                "SELECT action FROM audit_log WHERE action LIKE 'ausencia_%' ORDER BY id"
            )
        ]
    assert acciones == ["ausencia_registrada", "ausencia_eliminada"]


# ── Reporte y pantalla ────────────────────────────────────────────────────────


def test_el_resumen_del_reporte_cuenta_cada_tipo() -> None:
    def vista(estado: str, dia: int) -> AsistenciaVista:
        return AsistenciaVista(
            asistencia=Asistencia(
                id=dia,
                empleado_id=1,
                fecha=f"2026-01-{dia:02d}",
                estado=estado,
                turno_id_aplicado=1,
                hora_entrada_real=None,
                hora_salida_real=None,
                minutos_tarde=0,
                minutos_salida_temprana=0,
            ),
            empleado_nombre_completo="Villeda Karla",
            empleado_dni="0801-1990-00001",
            turno_nombre="Administrativo",
        )

    estados = ["VACACIONES", "VACACIONES", "DIA_LIBRE", "PERMISO", "INCAPACIDAD", "PRESENTE"]
    [resumen] = calcular_resumen([vista(e, i + 1) for i, e in enumerate(estados)])
    assert (
        resumen.dias_vacaciones,
        resumen.dias_libre,
        resumen.dias_permiso,
        resumen.dias_incapacidad,
        resumen.dias_presente,
        resumen.dias_ausente,
    ) == (2, 1, 1, 1, 1, 0)


def test_formatos_de_fechas_para_ausencias() -> None:
    assert formato.fecha_larga("2026-11-02") == "lunes 2 de noviembre de 2026"
    assert formato.rango_fechas("2026-11-02", "2026-11-02") == "02/11/2026"
    assert formato.rango_fechas("2026-11-02", "2026-11-06") == "02/11 al 06/11/2026"
    assert formato.rango_fechas("2026-12-28", "2027-01-04") == "28/12/2026 al 04/01/2027"
    assert formato.dias_cantidad(1) == "1 día" and formato.dias_cantidad(5) == "5 días"


def test_el_controller_exige_permiso_de_empleados(entorno: _Entorno) -> None:
    def ctrl(*permisos: str) -> AusenciasController:
        return AusenciasController(
            session=Session(
                user_id=entorno.actor,
                username="x",
                role_id=1,
                role_code=perms.ROLE_OPERADOR,
                permissions=frozenset(permisos),
            ),
            permission_service=PermissionService(),
            ausencia_service=entorno.s.ausencias,
            empleado_service=entorno.s.empleados,
        )

    with pytest.raises(PermissionDeniedError):
        ctrl(perms.VIEW_ATTENDANCE).listar()
    con_permiso = ctrl(perms.MANAGE_EMPLOYEES)
    a = con_permiso.registrar(entorno.empleado_id, VAC, "2026-01-12", "2026-01-12", None)
    assert a.creado_por_user_id == entorno.actor
    assert con_permiso.dias_laborables_de([a]) == {a.id: 1}
