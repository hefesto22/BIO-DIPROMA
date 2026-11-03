"""Feriados, períodos rápidos, formato de asistencia y consultas nuevas de las pantallas.

BD real sembrada con ``bin.seed_demo`` (1 semana) para servicios y
controllers; funciones puras para el formato.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Iterator

import pytest

from bin import seed_demo
from core.models import permissions as perms
from core.models.asistencia import Asistencia, EstadoAsistencia
from core.repositories.sincronizacion_repository_sqlite import SincronizacionRepositorySQLite
from core.services.errors import (
    CatalogoInUseError,
    FeriadoDuplicadoError,
    FeriadoNotFoundError,
    MissingRequiredFieldError,
    PermissionDeniedError,
)
from core.services.feriado_service import FeriadoService, domingo_de_pascua, feriados_nacionales
from core.services.permission_service import PermissionService
from core.services.session import Session
from infrastructure.database.connection import Database
from ui import asistencia_formato as af
from ui import periodos
from ui.controllers.asistencia_controller import AsistenciaController
from ui.controllers.configuracion_controller import ConfiguracionController
from ui.controllers.reporte_controller import ReporteController

E = EstadoAsistencia


@pytest.fixture
def demo(tmp_path: Path) -> Iterator[tuple[Database, seed_demo._Servicios]]:
    db_path = tmp_path / "zkteco_app.db"
    # 2 semanas: un lunes temprano la semana actual todavía no tiene marcas.
    assert seed_demo.main(["--db-path", str(db_path), "--semanas", "2"]) == 0
    db = Database(db_path)
    yield db, seed_demo._Servicios(db)


def _sesion(*permisos: str) -> Session:
    return Session(
        user_id=1,
        username="admin",
        role_id=1,
        role_code=perms.ROLE_SUPERADMIN,
        permissions=frozenset(permisos),
    )


def _feriados(db: Database, s: seed_demo._Servicios) -> FeriadoService:
    return FeriadoService(
        feriado_read=s.feriados,
        feriado_write=s.feriados,
        audit_logger=s.audit,
        consolidacion_service=s.consolidacion,
        asistencia_read=s.asistencia_repo,
    )


def _estados_del_dia(db: Database, fecha: str) -> set[str]:
    with db.transaction() as conn:
        return {
            r["estado"]
            for r in conn.execute("SELECT estado FROM asistencias WHERE fecha = ?", (fecha,))
        }


def _lunes_de_esta_semana() -> date:
    hoy = date.today()
    return date.fromordinal(hoy.toordinal() - hoy.weekday())


# ── Feriados ──────────────────────────────────────────────────────────────────


def test_pascua_y_semana_santa_de_varios_anios() -> None:
    assert domingo_de_pascua(2025) == date(2025, 4, 20)
    assert domingo_de_pascua(2026) == date(2026, 4, 5)
    assert domingo_de_pascua(2027) == date(2027, 3, 28)
    nacionales = dict(feriados_nacionales(2026))
    assert nacionales["2026-04-02"] == "Jueves Santo"
    assert nacionales["2026-04-04"] == "Sábado Santo"
    assert "2026-09-15" in nacionales and "2026-12-25" in nacionales
    # Los que se trasladan por decreto no se cargan solos.
    assert "2026-04-14" not in nacionales and "2026-10-03" not in nacionales


def test_crear_feriado_en_un_dia_ya_calculado_lo_marca_feriado(
    demo: tuple[Database, seed_demo._Servicios],
) -> None:
    db, s = demo
    lunes = _lunes_de_esta_semana().isoformat()
    assert E.FERIADO.value not in _estados_del_dia(db, lunes)

    _feriados(db, s).crear(lunes, "  Feriado   de prueba ", actor_user_id=1)

    assert _estados_del_dia(db, lunes) == {E.FERIADO.value}


def test_eliminar_feriado_recalcula_el_dia(demo: tuple[Database, seed_demo._Servicios]) -> None:
    db, s = demo
    servicio = _feriados(db, s)
    lunes = _lunes_de_esta_semana().isoformat()
    f = servicio.crear(lunes, "Prueba", actor_user_id=1)
    assert f.id is not None and f.descripcion == "Prueba"

    servicio.eliminar(f.id, actor_user_id=1)

    assert E.FERIADO.value not in _estados_del_dia(db, lunes)
    with pytest.raises(FeriadoNotFoundError):
        servicio.get(f.id)


def test_feriado_en_un_dia_sin_calcular_no_crea_asistencias(
    demo: tuple[Database, seed_demo._Servicios],
) -> None:
    db, s = demo
    _feriados(db, s).crear("2025-01-01", "Año Nuevo", actor_user_id=1)
    assert _estados_del_dia(db, "2025-01-01") == set()


def test_feriado_duplicado_y_validaciones(demo: tuple[Database, seed_demo._Servicios]) -> None:
    db, s = demo
    servicio = _feriados(db, s)
    servicio.crear("2027-09-15", "Independencia", actor_user_id=1)
    with pytest.raises(FeriadoDuplicadoError, match="15/09/2027"):
        servicio.crear("2027-09-15", "Otra cosa", actor_user_id=1)
    with pytest.raises(MissingRequiredFieldError, match="la descripción"):
        servicio.crear("2027-09-16", "   ", actor_user_id=1)


def test_actualizar_feriado_cambia_descripcion_o_fecha(
    demo: tuple[Database, seed_demo._Servicios],
) -> None:
    db, s = demo
    servicio = _feriados(db, s)
    f = servicio.crear("2027-10-03", "Día del Soldado", actor_user_id=1)
    assert f.id is not None
    misma = servicio.actualizar(f.id, "2027-10-03", "Semana Morazánica", actor_user_id=1)
    assert (misma.id, misma.descripcion) == (f.id, "Semana Morazánica")
    movida = servicio.actualizar(f.id, "2027-10-06", "Semana Morazánica", actor_user_id=1)
    assert [x.fecha for x in servicio.listar(2027)] == ["2027-10-06"]
    assert movida.fecha == "2027-10-06"


def test_cargar_nacionales_no_repite(demo: tuple[Database, seed_demo._Servicios]) -> None:
    db, s = demo
    servicio = _feriados(db, s)
    servicio.crear("2028-01-01", "Año Nuevo (a mano)", actor_user_id=1)
    agregados = servicio.cargar_nacionales(2028, actor_user_id=1)
    assert len(agregados) == 6
    assert servicio.cargar_nacionales(2028, actor_user_id=1) == []
    assert len(servicio.listar(2028)) == 7


# ── Controller de Configuración ───────────────────────────────────────────────


def _config(db: Database, s: seed_demo._Servicios, *permisos: str) -> ConfiguracionController:
    return ConfiguracionController(
        session=_sesion(*permisos),
        permission_service=PermissionService(),
        catalogo_service=s.catalogo,
        dispositivo_service=s.dispositivos,
        empleado_service=s.empleados,
        sincronizacion_read=SincronizacionRepositorySQLite(db),
        feriado_service=_feriados(db, s),
    )


def test_conteos_y_ultimas_sincronizaciones(demo: tuple[Database, seed_demo._Servicios]) -> None:
    db, s = demo
    ctrl = _config(db, s, perms.MANAGE_SETTINGS)
    por_depto, por_cargo = ctrl.conteo_empleados()
    activos = len(s.empleados.list_empleados(solo_activos=True))
    assert sum(por_depto.values()) == sum(por_cargo.values()) == activos
    [reloj] = ctrl.list_dispositivos()
    assert reloj.id is not None
    assert ctrl.ultimas_sincronizaciones()[reloj.id].estado == "OK"


def test_feriados_desde_el_controller_y_permiso(
    demo: tuple[Database, seed_demo._Servicios],
) -> None:
    db, s = demo
    ctrl = _config(db, s, perms.MANAGE_SETTINGS)
    ctrl.create_feriado("2029-05-01", "Día del Trabajo")
    assert [f.descripcion for f in ctrl.list_feriados(2029)] == ["Día del Trabajo"]
    with pytest.raises(PermissionDeniedError):
        _config(db, s, perms.VIEW_ATTENDANCE).list_feriados(2029)


def test_archivar_departamento_con_empleados_explica_el_motivo(
    demo: tuple[Database, seed_demo._Servicios],
) -> None:
    db, s = demo
    ctrl = _config(db, s, perms.MANAGE_SETTINGS)
    por_depto, _ = ctrl.conteo_empleados()
    depto_id = max(por_depto, key=lambda k: por_depto[k])
    with pytest.raises(CatalogoInUseError) as exc:
        ctrl.archive_departamento(depto_id)
    assert "empleados activos" in str(exc.value) and "id" not in str(exc.value)


# ── Asistencia y Reportes ─────────────────────────────────────────────────────


def test_asistencia_controller_trae_turnos_y_sube_el_limite(
    demo: tuple[Database, seed_demo._Servicios],
) -> None:
    db, s = demo
    from core.services.asistencia_service import AsistenciaService

    asistencia = AsistenciaService(
        asistencia_read=s.asistencia_repo,
        asistencia_write=s.asistencia_repo,
        empleado_read=s.empleado_repo,
        turno_read=s.turno_repo,
        audit_logger=s.audit,
        consolidacion_service=s.consolidacion,
    )
    ctrl = AsistenciaController(
        session=_sesion(perms.VIEW_ATTENDANCE),
        permission_service=PermissionService(),
        asistencia_service=asistencia,
        turno_service=s.turnos,
    )
    assert {t.nombre for t in ctrl.turnos().values()} == {"Administrativo", "Operativo", "Gerencia"}
    lunes = _lunes_de_esta_semana()
    corto = ctrl.list_asistencias(lunes.isoformat(), date.today().isoformat(), limit=3)
    assert corto.truncado and len(corto.items) == 3
    completo = ctrl.list_asistencias(lunes.isoformat(), date.today().isoformat(), limit=10000)
    assert not completo.truncado


def test_vista_previa_del_reporte_coincide_con_el_resumen(
    demo: tuple[Database, seed_demo._Servicios],
) -> None:
    _db, s = demo
    ctrl = ReporteController(
        session=_sesion(perms.EXPORT_REPORTS),
        permission_service=PermissionService(),
        reporte_service=s.reportes,
    )
    # Últimos 7 días: "esta semana" un lunes temprano todavía no tiene marcas.
    hoy = date.today()
    desde = (hoy - timedelta(days=7)).isoformat()
    resumen = ctrl.vista_previa(desde, hoy.isoformat())
    assert len(resumen) == 20
    assert sum(r.dias_presente for r in resumen) > 0
    solo_uno = ctrl.vista_previa(desde, hoy.isoformat(), resumen[0].empleado_id)
    assert [r.empleado_id for r in solo_uno] == [resumen[0].empleado_id]


# ── Funciones puras ───────────────────────────────────────────────────────────


def test_periodos_rapidos() -> None:
    hoy = date(2026, 10, 31)  # sábado
    assert periodos.rango(periodos.HOY, hoy) == (hoy, hoy)
    assert periodos.rango(periodos.AYER, hoy) == (date(2026, 10, 30),) * 2
    assert periodos.rango(periodos.ESTA_SEMANA, hoy) == (date(2026, 10, 26), hoy)
    assert periodos.rango(periodos.ESTE_MES, hoy) == (date(2026, 10, 1), hoy)
    assert periodos.rango(periodos.MES_PASADO, date(2026, 3, 10)) == (
        date(2026, 2, 1),
        date(2026, 2, 28),
    )
    assert periodos.rango(periodos.QUINCENA_1, hoy) == (date(2026, 10, 1), date(2026, 10, 15))
    assert periodos.rango(periodos.QUINCENA_2, hoy) == (date(2026, 10, 16), date(2026, 10, 31))
    assert periodos.descripcion(date(2026, 10, 26), hoy) == "26/10 al 31/10/2026"


def _asistencia(estado: str, **campos: object) -> Asistencia:
    datos: dict[str, object] = dict(
        id=1,
        empleado_id=1,
        fecha="2026-10-26",
        estado=estado,
        turno_id_aplicado=1,
        hora_entrada_real="09:17:05",
        hora_salida_real="17:00:00",
        minutos_tarde=0,
        minutos_salida_temprana=0,
    )
    datos.update(campos)
    return Asistencia(**datos)  # type: ignore[arg-type]


def test_formato_de_estados_de_asistencia() -> None:
    assert af.texto_estado(_asistencia(E.TARDE.value, minutos_tarde=17)) == "Tarde (+17 min)"
    assert (
        af.texto_estado(_asistencia(E.SALIDA_TEMPRANA.value, minutos_salida_temprana=20))
        == "Salida temprana (−20 min)"
    )
    assert af.texto_estado(_asistencia(E.VACACIONES.value)) == "Vacaciones"
    assert af.hora("09:17:05") == "09:17" and af.hora(None) == "—"
    asumida = _asistencia(
        E.PRESENTE.value, observaciones="Salida asumida según turno: sin segunda marcada."
    )
    assert af.salida(asumida) == "17:00 *" and not af.tiene_nota(asumida)
    assert af.tiene_nota(_asistencia(E.PRESENTE.value, observaciones="Cita médica"))
    assert af.fecha_con_dia("2026-10-26") == "Lun 26/10"


def test_todos_los_estados_tienen_texto_y_los_que_cuentan_tienen_categoria() -> None:
    assert set(af.ESTADO_TEXTO) == {e.value for e in EstadoAsistencia}
    sin_categoria = {e.value for e in EstadoAsistencia} - set(af.CATEGORIA)
    assert sin_categoria == {E.FERIADO.value, E.SIN_TURNO.value}
