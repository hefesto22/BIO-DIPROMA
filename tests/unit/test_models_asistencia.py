"""Tests de los modelos de dominio de Fase 3 (asistencia).

Alcance: los 5 modelos nuevos (``Dispositivo``, ``Feriado``,
``Sincronizacion``, ``RegistroRaw``, ``Asistencia``) más los 3 enums
acoplados (``EstadoSincronizacion``, ``TipoMarcada``, ``EstadoAsistencia``)
y la extensión de ``Turno`` con tolerancias.

No hay BD, servicios ni pyzk aquí — son modelos puros (``@dataclass`` +
``Enum``). Los invariantes persistidos (CHECKs, UNIQUEs) se validan en los
tests de repositorio de Sub-3.2.
"""

from __future__ import annotations

from core.models.asistencia import (
    ALL_ESTADOS_ASISTENCIA,
    Asistencia,
    EstadoAsistencia,
)
from core.models.dispositivo import PUERTO_ZKTECO_DEFAULT, Dispositivo
from core.models.feriado import Feriado
from core.models.registro_raw import ALL_TIPOS_MARCADA, RegistroRaw, TipoMarcada
from core.models.sincronizacion import (
    ALL_ESTADOS_SINCRONIZACION,
    EstadoSincronizacion,
    Sincronizacion,
)
from core.models.turno import DIAS_LABORALES, Turno


# ── Dispositivo ───────────────────────────────────────────────────────────────


def test_dispositivo_usa_puerto_4370_por_default() -> None:
    """El puerto estándar ZKTeco debe ser el default."""
    d = Dispositivo(id=None, nombre="Sede Principal", ip="192.168.1.100")
    assert d.puerto == 4370
    assert d.puerto == PUERTO_ZKTECO_DEFAULT


def test_dispositivo_es_activo_por_default() -> None:
    """Al crear un dispositivo debe quedar activo sin necesidad de pasar el flag."""
    d = Dispositivo(id=None, nombre="Alcaldía", ip="192.168.1.50")
    assert d.is_active is True


def test_dispositivo_puerto_custom_se_respeta() -> None:
    """Si el caller pasa un puerto distinto, se persiste ese y no el default."""
    d = Dispositivo(id=1, nombre="Bodega", ip="10.0.0.5", puerto=5000)
    assert d.puerto == 5000


# ── Feriado ───────────────────────────────────────────────────────────────────


def test_feriado_requiere_fecha_y_descripcion() -> None:
    """Ambos campos son obligatorios para instanciar."""
    f = Feriado(id=None, fecha="2026-09-15", descripcion="Día de la Independencia")
    assert f.fecha == "2026-09-15"
    assert f.descripcion == "Día de la Independencia"


# ── EstadoSincronizacion ──────────────────────────────────────────────────────


def test_estado_sincronizacion_tiene_3_valores() -> None:
    """El catálogo debe tener exactamente EN_CURSO/OK/FALLIDA."""
    assert set(e.value for e in EstadoSincronizacion) == {"EN_CURSO", "OK", "FALLIDA"}


def test_estado_sincronizacion_compara_como_string() -> None:
    """La herencia de str permite comparación directa contra el TEXT de BD."""
    assert EstadoSincronizacion.OK == "OK"
    assert EstadoSincronizacion.EN_CURSO == "EN_CURSO"


def test_all_estados_sincronizacion_es_frozenset_de_los_valores() -> None:
    """El set agregador debe contener los strings, no los miembros Enum."""
    assert isinstance(ALL_ESTADOS_SINCRONIZACION, frozenset)
    assert ALL_ESTADOS_SINCRONIZACION == {"EN_CURSO", "OK", "FALLIDA"}


# ── Sincronizacion ────────────────────────────────────────────────────────────


def test_sincronizacion_arranca_en_curso() -> None:
    """Al crearse, una sync recién iniciada no tiene fin ni error."""
    s = Sincronizacion(
        id=None,
        dispositivo_id=1,
        iniciada_por_user_id=42,
        inicio="2026-04-24T10:00:00+00:00",
        rango_desde="2026-04-01",
        rango_hasta="2026-04-24",
    )
    assert s.estado == EstadoSincronizacion.EN_CURSO.value
    assert s.fin is None
    assert s.registros_recibidos == 0
    assert s.error_mensaje is None


def test_sincronizacion_admite_user_null() -> None:
    """iniciada_por_user_id debe admitir None (FK ON DELETE SET NULL)."""
    s = Sincronizacion(
        id=None,
        dispositivo_id=1,
        iniciada_por_user_id=None,
        inicio="2026-04-24T10:00:00+00:00",
        rango_desde="2026-04-01",
        rango_hasta="2026-04-24",
    )
    assert s.iniciada_por_user_id is None


# ── TipoMarcada ───────────────────────────────────────────────────────────────


def test_tipo_marcada_tiene_5_valores_incluyendo_unknown() -> None:
    """CHECK_IN/OUT + OVERTIME_IN/OUT + UNKNOWN de fallback."""
    assert set(t.value for t in TipoMarcada) == {
        "CHECK_IN",
        "CHECK_OUT",
        "OVERTIME_IN",
        "OVERTIME_OUT",
        "UNKNOWN",
    }


def test_all_tipos_marcada_incluye_unknown() -> None:
    """UNKNOWN debe estar en el frozenset agregador (se usa como default)."""
    assert "UNKNOWN" in ALL_TIPOS_MARCADA


# ── RegistroRaw ───────────────────────────────────────────────────────────────


def test_registro_raw_default_tipo_es_unknown() -> None:
    """Si el device devolvió un código no reconocido, no perdemos el registro."""
    r = RegistroRaw(
        id=None,
        dispositivo_id=1,
        sincronizacion_id=5,
        zkteco_user_id=100,
        timestamp="2026-04-24T08:02:00",
    )
    assert r.tipo_marcada == TipoMarcada.UNKNOWN.value


def test_registro_raw_acepta_tipo_explicito() -> None:
    """El adapter puede poblar el tipo mapeando el código del device."""
    r = RegistroRaw(
        id=None,
        dispositivo_id=1,
        sincronizacion_id=5,
        zkteco_user_id=100,
        timestamp="2026-04-24T08:02:00",
        tipo_marcada=TipoMarcada.CHECK_IN.value,
    )
    assert r.tipo_marcada == "CHECK_IN"


# ── EstadoAsistencia ──────────────────────────────────────────────────────────


def test_estado_asistencia_cubre_los_casos_del_prd_y_ausencias() -> None:
    """Los 8 estados del PRD + los 4 de ausencias registradas (migración 012)."""
    esperados = {
        "PRESENTE",
        "TARDE",
        "SALIDA_TEMPRANA",
        "TARDE_Y_SALIDA_TEMPRANA",
        "AUSENTE",
        "SIN_TURNO",
        "FERIADO",
        "INCOMPLETO",
        "VACACIONES",
        "DIA_LIBRE",
        "PERMISO",
        "INCAPACIDAD",
    }
    assert set(e.value for e in EstadoAsistencia) == esperados


def test_all_estados_asistencia_es_frozenset_inmutable() -> None:
    """Defensa: ningún caller debe poder mutar el set de estados válidos."""
    assert isinstance(ALL_ESTADOS_ASISTENCIA, frozenset)
    assert len(ALL_ESTADOS_ASISTENCIA) == 12


# ── Asistencia ────────────────────────────────────────────────────────────────


def test_asistencia_ausente_permite_turno_aplicado_con_horas_null() -> None:
    """AUSENTE: turno esperado presente, pero sin marcadas reales."""
    a = Asistencia(
        id=None,
        empleado_id=10,
        fecha="2026-04-24",
        estado=EstadoAsistencia.AUSENTE.value,
        turno_id_aplicado=3,
    )
    assert a.turno_id_aplicado == 3
    assert a.hora_entrada_real is None
    assert a.hora_salida_real is None
    assert a.minutos_tarde == 0
    assert a.minutos_salida_temprana == 0


def test_asistencia_feriado_no_requiere_turno() -> None:
    """FERIADO: campo turno_id_aplicado puede ser None."""
    a = Asistencia(
        id=None,
        empleado_id=10,
        fecha="2026-09-15",
        estado=EstadoAsistencia.FERIADO.value,
    )
    assert a.turno_id_aplicado is None
    assert a.estado == "FERIADO"


def test_asistencia_tarde_almacena_minutos() -> None:
    """TARDE: se guarda el delta de minutos entre hora oficial y real."""
    a = Asistencia(
        id=None,
        empleado_id=10,
        fecha="2026-04-24",
        estado=EstadoAsistencia.TARDE.value,
        turno_id_aplicado=3,
        hora_entrada_real="08:23:00",
        hora_salida_real="17:00:00",
        minutos_tarde=23,
    )
    assert a.minutos_tarde == 23
    assert a.minutos_salida_temprana == 0


# ── Turno extendido con tolerancias ───────────────────────────────────────────


def test_turno_default_10_min_tolerancia_entrada_0_salida() -> None:
    """Los defaults elegidos en D1: 10 entrada, 0 salida."""
    t = Turno(
        id=None,
        nombre="Admin 8-5",
        hora_entrada="08:00",
        hora_salida="17:00",
    )
    assert t.minutos_tolerancia_entrada == 10
    assert t.minutos_tolerancia_salida == 0


def test_turno_tolerancias_son_configurables_por_turno() -> None:
    """Un turno nocturno/vigilancia puede ser más estricto que el default."""
    t = Turno(
        id=None,
        nombre="Vigilancia Nocturna",
        hora_entrada="22:00",
        hora_salida="06:00",
        dias_semana=DIAS_LABORALES,
        cruza_medianoche=True,
        minutos_tolerancia_entrada=0,
        minutos_tolerancia_salida=0,
    )
    assert t.minutos_tolerancia_entrada == 0
    assert t.minutos_tolerancia_salida == 0
