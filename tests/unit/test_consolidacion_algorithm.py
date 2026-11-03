"""Tests del ``core.services.consolidacion_algorithm``.

Funciones puras sin BD ni fakes. Cubren:

    - ``resolver_turno_en_fecha``: resolución por rango histórico + huecos.
    - ``ventana_del_dia``: tolerancias + turno normal + turno nocturno.
    - ``parear_marcadas`` (Sub-2.7e, "ventana dinámica"): primera marcada
      como entrada, ventana de rebote de 60 min, salida real prevalece
      sobre hora del turno, y salida asumida cuando el día ya cerró.
    - ``derivar_estado``: AUSENTE, INCOMPLETO, PRESENTE, TARDE,
      SALIDA_TEMPRANA, TARDE_Y_SALIDA_TEMPRANA — con y sin tolerancias.
    - ``estado_no_trabaja``: prioridad FERIADO > SIN_TURNO, bitmask de días.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import List, Optional

import pytest

from core.models.asistencia import EstadoAsistencia
from core.models.empleado_turno import EmpleadoTurno
from core.models.registro_raw import RegistroRaw, TipoMarcada
from core.models.turno import (
    DIAS_FIN_DE_SEMANA,
    DIAS_LABORALES,
    DIAS_TODA_LA_SEMANA,
    LUNES,
    Turno,
)
from core.services.consolidacion_algorithm import (
    HORA_LIMITE_DIA,
    VENTANA_REBOTE_MINUTOS,
    MarcadasDelDia,
    derivar_estado,
    estado_no_trabaja,
    parear_marcadas,
    resolver_turno_en_fecha,
    ventana_del_dia,
)

# ── Factories ────────────────────────────────────────────────────────────────


def _turno(
    id_: int = 1,
    hora_entrada: str = "08:00",
    hora_salida: str = "17:00",
    dias: int = DIAS_LABORALES,
    tol_entrada: int = 10,
    tol_salida: int = 0,
    cruza_medianoche: bool = False,
) -> Turno:
    return Turno(
        id=id_,
        nombre=f"Turno {id_}",
        hora_entrada=hora_entrada,
        hora_salida=hora_salida,
        dias_semana=dias,
        cruza_medianoche=cruza_medianoche,
        minutos_tolerancia_entrada=tol_entrada,
        minutos_tolerancia_salida=tol_salida,
    )


def _asignacion(
    id_: int,
    turno_id: int,
    fecha_inicio: str,
    fecha_fin: Optional[str] = None,
) -> EmpleadoTurno:
    return EmpleadoTurno(
        id=id_,
        empleado_id=100,
        turno_id=turno_id,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
    )


def _raw(
    timestamp: str,
    tipo: str = TipoMarcada.UNKNOWN.value,
    zkteco_user_id: int = 42,
) -> RegistroRaw:
    return RegistroRaw(
        id=None,
        dispositivo_id=1,
        sincronizacion_id=1,
        zkteco_user_id=zkteco_user_id,
        timestamp=timestamp,
        tipo_marcada=tipo,
    )


# ── resolver_turno_en_fecha ──────────────────────────────────────────────────


def test_resolver_turno_vigente_actual() -> None:
    historial: List[EmpleadoTurno] = [_asignacion(1, turno_id=10, fecha_inicio="2026-01-01")]
    assert resolver_turno_en_fecha(historial, "2026-04-15") == 10


def test_resolver_turno_cerrado_cubre_fecha() -> None:
    historial = [
        _asignacion(1, turno_id=10, fecha_inicio="2026-01-01", fecha_fin="2026-03-31"),
        _asignacion(2, turno_id=20, fecha_inicio="2026-04-01"),
    ]
    assert resolver_turno_en_fecha(historial, "2026-02-15") == 10
    assert resolver_turno_en_fecha(historial, "2026-04-15") == 20


def test_resolver_turno_en_borde_fecha_fin_inclusive() -> None:
    """``fecha_fin`` es inclusive — el último día aún pertenece a esa asignación."""
    historial = [
        _asignacion(1, turno_id=10, fecha_inicio="2026-01-01", fecha_fin="2026-03-31"),
        _asignacion(2, turno_id=20, fecha_inicio="2026-04-01"),
    ]
    assert resolver_turno_en_fecha(historial, "2026-03-31") == 10
    assert resolver_turno_en_fecha(historial, "2026-04-01") == 20


def test_resolver_turno_antes_de_primera_asignacion_devuelve_none() -> None:
    historial = [_asignacion(1, turno_id=10, fecha_inicio="2026-02-01")]
    assert resolver_turno_en_fecha(historial, "2026-01-15") is None


def test_resolver_turno_hueco_entre_asignaciones_cerradas_devuelve_none() -> None:
    historial = [
        _asignacion(1, turno_id=10, fecha_inicio="2026-01-01", fecha_fin="2026-01-31"),
        _asignacion(2, turno_id=20, fecha_inicio="2026-03-01"),
    ]
    assert resolver_turno_en_fecha(historial, "2026-02-15") is None


def test_resolver_turno_sin_historial_devuelve_none() -> None:
    assert resolver_turno_en_fecha([], "2026-04-15") is None


def test_resolver_turno_fecha_invalida_lanza_value_error() -> None:
    with pytest.raises(ValueError, match="Fecha inválida"):
        resolver_turno_en_fecha([], "no-fecha")


def test_resolver_turno_filtra_por_dia_cuando_hay_multiples_vigentes() -> None:
    """Sub-3.2.B: con dos asignaciones vigentes (una L-V y otra Sáb), la
    función devuelve el turno cuyo bitmask incluye el día de ``fecha``.

    Lunes (weekday=0) → turno_id 10 (L-V).
    Sábado (weekday=5) → turno_id 20 (Sáb).
    """
    from core.models.turno import DIAS_LABORALES, SABADO

    historial = [
        _asignacion(1, turno_id=10, fecha_inicio="2026-01-01"),
        _asignacion(2, turno_id=20, fecha_inicio="2026-01-02"),
    ]
    turnos_por_id = {
        10: _turno(id_=10, dias=DIAS_LABORALES),
        20: _turno(id_=20, dias=SABADO),
    }
    # 2026-04-13 es lunes (weekday 0)
    assert resolver_turno_en_fecha(historial, "2026-04-13", turnos_por_id) == 10
    # 2026-04-18 es sábado (weekday 5)
    assert resolver_turno_en_fecha(historial, "2026-04-18", turnos_por_id) == 20
    # 2026-04-19 es domingo: ninguno aplica
    assert resolver_turno_en_fecha(historial, "2026-04-19", turnos_por_id) is None


def test_resolver_turno_legacy_sin_turnos_por_id_devuelve_la_primera_match() -> None:
    """Comportamiento previo a Sub-3.2.B: sin catálogo, no se filtra por día."""
    historial = [_asignacion(1, turno_id=10, fecha_inicio="2026-01-01")]
    # Cualquier fecha cubierta por la asignación devuelve el turno_id sin
    # mirar dias_semana.
    assert resolver_turno_en_fecha(historial, "2026-04-13") == 10


# ── ventana_del_dia ──────────────────────────────────────────────────────────


def test_ventana_turno_normal_captura_4h_antes_y_6h_despues() -> None:
    """La ventana de captura NO son las tolerancias: quien llega 15 min antes
    o sale 17:05 debe contar (antes quedaba AUSENTE o con salida asumida)."""
    turno = _turno(hora_entrada="08:00", hora_salida="17:00", tol_entrada=10, tol_salida=5)
    inicio, fin = ventana_del_dia(turno, "2026-04-15")
    assert inicio == datetime(2026, 4, 15, 4, 0)
    assert fin == datetime(2026, 4, 15, 23, 0)


def test_ventana_turno_nocturno_cruza_medianoche() -> None:
    turno = _turno(
        hora_entrada="22:00",
        hora_salida="06:00",
        cruza_medianoche=True,
        tol_entrada=15,
        tol_salida=10,
    )
    inicio, fin = ventana_del_dia(turno, "2026-04-15")
    assert inicio == datetime(2026, 4, 15, 18, 0)
    # Fin cae al día siguiente.
    assert fin == datetime(2026, 4, 16, 12, 0)


def test_ventana_con_tolerancias_cero_igual_captura_alrededor() -> None:
    turno = _turno(hora_entrada="08:00", hora_salida="17:00", tol_entrada=0, tol_salida=0)
    inicio, fin = ventana_del_dia(turno, "2026-04-15")
    assert inicio == datetime(2026, 4, 15, 4, 0)
    assert fin == datetime(2026, 4, 15, 23, 0)


def test_ventana_de_turno_largo_no_se_pisa_con_el_dia_siguiente() -> None:
    """Turno de 20 h: los márgenes se recortan para que la ventana dure < 24 h."""
    turno = _turno(hora_entrada="04:00", hora_salida="23:59", tol_entrada=0, tol_salida=0)
    inicio, fin = ventana_del_dia(turno, "2026-04-15")
    siguiente, _ = ventana_del_dia(turno, "2026-04-16")
    assert fin - inicio < timedelta(hours=24)
    assert fin < siguiente


def test_llega_temprano_y_sale_tarde_cuenta_como_presente() -> None:
    """El caso del bug: 07:42 → 17:08 con turno 08-17 es PRESENTE, no AUSENTE."""
    turno = _turno(hora_entrada="08:00", hora_salida="17:00")
    inicio, fin = ventana_del_dia(turno, "2026-04-15")
    marcadas = parear_marcadas(
        [_raw("2026-04-15T07:42:00"), _raw("2026-04-15T17:08:00")],
        inicio,
        fin,
        turno,
        "2026-04-15",
        ahora=datetime(2026, 4, 16, 9, 0),
    )
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    assert marcadas.salida_es_asumida is False
    assert resultado.estado == EstadoAsistencia.PRESENTE.value
    assert (resultado.hora_entrada_real, resultado.hora_salida_real) == ("07:42:00", "17:08:00")


# ── parear_marcadas (algoritmo "ventana dinámica", Sub-2.7e) ─────────────────


_FECHA_TEST = "2026-04-15"
# ``ahora`` por defecto en la mayoría de los tests: día siguiente al de
# la asistencia. Eso garantiza que el día de la asistencia ya cerró,
# así la salida asumida se aplica cuando corresponde, sin ruido del
# reloj de pared. Los tests que precisan otro escenario lo construyen
# explícitamente.
_AHORA_DIA_CERRADO = datetime(2026, 4, 16, 9, 0)


def _ventana_default(fecha: str = _FECHA_TEST) -> tuple[datetime, datetime]:
    """Helper: ventana del turno 08:00-17:00 con tolerancia 10/0 en la fecha."""
    return ventana_del_dia(_turno(hora_entrada="08:00", hora_salida="17:00"), fecha)


def test_constantes_publicas_tienen_los_valores_documentados() -> None:
    """Sanity check sobre las constantes para evitar drift accidental."""
    assert VENTANA_REBOTE_MINUTOS == 60
    assert HORA_LIMITE_DIA == time(20, 0)


def test_parear_entrada_y_salida_clasicas() -> None:
    """Caso típico: 08:00 entrada y 17:00 salida, separadas por más de 1h."""
    turno = _turno(hora_entrada="08:00", hora_salida="17:00")
    raws = [
        _raw("2026-04-15T08:00:00"),
        _raw("2026-04-15T17:00:00"),
    ]
    inicio, fin = _ventana_default()
    marcadas = parear_marcadas(raws, inicio, fin, turno, _FECHA_TEST, ahora=_AHORA_DIA_CERRADO)
    assert marcadas.entrada == datetime(2026, 4, 15, 8, 0)
    assert marcadas.salida == datetime(2026, 4, 15, 17, 0)
    assert marcadas.salida_es_asumida is False
    assert marcadas.observacion is None


def test_parear_primera_marcada_gana_aunque_llegue_tarde() -> None:
    """Llega 10:30 y marca: esa es entrada (no se descarta por hora)."""
    turno = _turno(hora_entrada="08:00", hora_salida="17:00", tol_salida=15)
    raws = [
        _raw("2026-04-15T10:30:00"),  # entrada real (llegada tarde)
        _raw("2026-04-15T17:05:00"),  # salida (>1h después, dentro de tol_salida)
    ]
    inicio, fin = ventana_del_dia(turno, _FECHA_TEST)
    marcadas = parear_marcadas(raws, inicio, fin, turno, _FECHA_TEST, ahora=_AHORA_DIA_CERRADO)
    assert marcadas.entrada == datetime(2026, 4, 15, 10, 30)
    assert marcadas.salida == datetime(2026, 4, 15, 17, 5)
    assert marcadas.salida_es_asumida is False


def test_parear_rebotes_dentro_de_la_ventana_se_ignoran() -> None:
    """Si apoya el dedo varias veces dentro de la primera hora, todas las
    marcadas extra se ignoran — la salida es la PRIMERA después del cutoff."""
    turno = _turno(hora_entrada="08:00", hora_salida="17:00")
    raws = [
        _raw("2026-04-15T08:00:00"),  # entrada
        _raw("2026-04-15T08:05:00"),  # rebote
        _raw("2026-04-15T08:30:00"),  # rebote
        _raw("2026-04-15T08:59:00"),  # rebote (justo dentro de cutoff)
        _raw("2026-04-15T17:00:00"),  # salida
    ]
    inicio, fin = _ventana_default()
    marcadas = parear_marcadas(raws, inicio, fin, turno, _FECHA_TEST, ahora=_AHORA_DIA_CERRADO)
    assert marcadas.entrada == datetime(2026, 4, 15, 8, 0)
    assert marcadas.salida == datetime(2026, 4, 15, 17, 0)


def test_parear_marcada_justo_en_el_cutoff_es_rebote() -> None:
    """La marcada exactamente en ``entrada + 60min`` cae en la ventana de
    rebote (frontera inclusive). Recién la siguiente cuenta como salida."""
    turno = _turno(hora_entrada="08:00", hora_salida="17:00")
    raws = [
        _raw("2026-04-15T08:00:00"),  # entrada
        _raw("2026-04-15T09:00:00"),  # exactamente cutoff → rebote
        _raw("2026-04-15T17:00:00"),  # salida real
    ]
    inicio, fin = _ventana_default()
    marcadas = parear_marcadas(raws, inicio, fin, turno, _FECHA_TEST, ahora=_AHORA_DIA_CERRADO)
    assert marcadas.entrada == datetime(2026, 4, 15, 8, 0)
    assert marcadas.salida == datetime(2026, 4, 15, 17, 0)


def test_parear_multiples_despues_de_cutoff_la_ultima_gana() -> None:
    """Varios apoyos al irse: la última marcada > cutoff es la salida."""
    turno = _turno(hora_entrada="08:00", hora_salida="17:00")
    raws = [
        _raw("2026-04-15T08:00:00"),  # entrada
        _raw("2026-04-15T16:30:00"),  # ya pasó el cutoff — salida candidata
        _raw("2026-04-15T16:55:00"),  # otra
        _raw("2026-04-15T17:00:00"),  # salida final — gana la última
    ]
    inicio, fin = _ventana_default()
    marcadas = parear_marcadas(raws, inicio, fin, turno, _FECHA_TEST, ahora=_AHORA_DIA_CERRADO)
    assert marcadas.entrada == datetime(2026, 4, 15, 8, 0)
    assert marcadas.salida == datetime(2026, 4, 15, 17, 0)


def test_parear_salida_real_prevalece_sobre_hora_del_turno() -> None:
    """Entró 7:00 y se fue a las 19:00 — la salida es 19:00 aunque el
    turno termine a las 17:00. El reloj manda."""
    turno = _turno(hora_entrada="08:00", hora_salida="17:00", tol_salida=30)
    raws = [
        _raw("2026-04-15T07:00:00"),
        _raw("2026-04-15T19:00:00"),  # 2h más tarde que la hora oficial
    ]
    # ampliar la ventana para que la marcada de 19:00 entre
    inicio = datetime(2026, 4, 15, 6, 30)
    fin = datetime(2026, 4, 15, 20, 0)
    marcadas = parear_marcadas(raws, inicio, fin, turno, _FECHA_TEST, ahora=_AHORA_DIA_CERRADO)
    assert marcadas.entrada == datetime(2026, 4, 15, 7, 0)
    assert marcadas.salida == datetime(2026, 4, 15, 19, 0)


def test_parear_caso_real_K40_4_marcadas_todas_check_out() -> None:
    """K40 estándar reporta todo como CHECK_OUT — el algoritmo no mira
    ``tipo_marcada``, solo el orden temporal."""
    turno = _turno(hora_entrada="08:00", hora_salida="17:00", tol_salida=10)
    raws = [
        _raw("2026-04-15T07:55:00", tipo=TipoMarcada.CHECK_OUT.value),  # entrada
        _raw("2026-04-15T08:02:00", tipo=TipoMarcada.CHECK_OUT.value),  # rebote
        _raw("2026-04-15T16:45:00", tipo=TipoMarcada.CHECK_OUT.value),  # salida cand.
        _raw("2026-04-15T17:05:00", tipo=TipoMarcada.CHECK_OUT.value),  # salida final
    ]
    inicio, fin = ventana_del_dia(turno, _FECHA_TEST)
    marcadas = parear_marcadas(raws, inicio, fin, turno, _FECHA_TEST, ahora=_AHORA_DIA_CERRADO)
    assert marcadas.entrada == datetime(2026, 4, 15, 7, 55)
    assert marcadas.salida == datetime(2026, 4, 15, 17, 5)


def test_parear_sin_marcadas_retorna_ambos_none() -> None:
    """Sin marcadas → AUSENTE, sin observación."""
    turno = _turno(hora_entrada="08:00", hora_salida="17:00")
    raws: List[RegistroRaw] = []
    inicio, fin = _ventana_default()
    marcadas = parear_marcadas(raws, inicio, fin, turno, _FECHA_TEST, ahora=_AHORA_DIA_CERRADO)
    assert marcadas.entrada is None
    assert marcadas.salida is None
    assert marcadas.salida_es_asumida is False
    assert marcadas.observacion is None


def test_parear_marcadas_fuera_de_ventana_se_descartan() -> None:
    """Marcadas anteriores a inicio o posteriores a fin se ignoran."""
    turno = _turno(hora_entrada="08:00", hora_salida="17:00")
    raws = [
        _raw("2026-04-14T23:00:00"),  # día previo — fuera
        _raw("2026-04-15T08:00:00"),  # entrada válida
        _raw("2026-04-15T17:00:00"),  # salida válida
        _raw("2026-04-15T23:30:00"),  # > fin (17:00 + 6 h) — fuera
    ]
    inicio, fin = _ventana_default()
    marcadas = parear_marcadas(raws, inicio, fin, turno, _FECHA_TEST, ahora=_AHORA_DIA_CERRADO)
    assert marcadas.entrada == datetime(2026, 4, 15, 8, 0)
    assert marcadas.salida == datetime(2026, 4, 15, 17, 0)


def test_parear_solo_entrada_dia_ya_cerrado_asume_salida_del_turno() -> None:
    """Llegó tarde, marcó solo a las 11:56, pero ya pasaron las 20:00 →
    salida asumida = hora_salida del turno (17:00) + observación."""
    turno = _turno(hora_entrada="08:00", hora_salida="17:00")
    raws = [_raw("2026-04-15T11:56:00")]
    # ventana amplia para que entren marcadas tardías
    inicio = datetime(2026, 4, 15, 7, 50)
    fin = datetime(2026, 4, 15, 23, 0)
    ahora = datetime(2026, 4, 15, 21, 0)  # mismo día, después de las 20:00
    marcadas = parear_marcadas(raws, inicio, fin, turno, _FECHA_TEST, ahora=ahora)
    assert marcadas.entrada == datetime(2026, 4, 15, 11, 56)
    assert marcadas.salida == datetime(2026, 4, 15, 17, 0)
    assert marcadas.salida_es_asumida is True
    assert marcadas.observacion is not None
    assert "Salida asumida" in marcadas.observacion


def test_parear_solo_entrada_fecha_pasada_asume_salida_del_turno() -> None:
    """Día previo, solo hay entrada — el día está cerrado por fecha. Se
    asume salida según turno."""
    turno = _turno(hora_entrada="08:00", hora_salida="17:00")
    raws = [_raw("2026-04-15T08:00:00")]
    inicio, fin = _ventana_default()
    ahora = datetime(2026, 4, 20, 9, 0)  # 5 días después
    marcadas = parear_marcadas(raws, inicio, fin, turno, _FECHA_TEST, ahora=ahora)
    assert marcadas.entrada == datetime(2026, 4, 15, 8, 0)
    assert marcadas.salida == datetime(2026, 4, 15, 17, 0)
    assert marcadas.salida_es_asumida is True


def test_parear_solo_entrada_dia_aun_abierto_deja_salida_none() -> None:
    """Mismo día, antes de las 20:00 — el empleado todavía puede volver
    a marcar. NO se asume salida; queda en None → INCOMPLETO."""
    turno = _turno(hora_entrada="08:00", hora_salida="17:00")
    raws = [_raw("2026-04-15T11:56:00")]
    inicio = datetime(2026, 4, 15, 7, 50)
    fin = datetime(2026, 4, 15, 23, 0)
    ahora = datetime(2026, 4, 15, 14, 0)  # 2 PM, día abierto
    marcadas = parear_marcadas(raws, inicio, fin, turno, _FECHA_TEST, ahora=ahora)
    assert marcadas.entrada == datetime(2026, 4, 15, 11, 56)
    assert marcadas.salida is None
    assert marcadas.salida_es_asumida is False
    assert marcadas.observacion is None


def test_parear_solo_entrada_justo_a_las_20_00_se_asume_salida() -> None:
    """Frontera: a las 20:00 exactas el día se considera cerrado."""
    turno = _turno(hora_entrada="08:00", hora_salida="17:00")
    raws = [_raw("2026-04-15T08:00:00")]
    inicio, fin = _ventana_default()
    ahora = datetime(2026, 4, 15, 20, 0, 0)  # justo a las 20:00
    marcadas = parear_marcadas(raws, inicio, fin, turno, _FECHA_TEST, ahora=ahora)
    assert marcadas.salida == datetime(2026, 4, 15, 17, 0)
    assert marcadas.salida_es_asumida is True


def test_parear_solo_entrada_un_minuto_antes_de_las_20_no_asume() -> None:
    """A las 19:59 todavía no se asume salida (puede volver a marcar)."""
    turno = _turno(hora_entrada="08:00", hora_salida="17:00")
    raws = [_raw("2026-04-15T08:00:00")]
    inicio, fin = _ventana_default()
    ahora = datetime(2026, 4, 15, 19, 59)
    marcadas = parear_marcadas(raws, inicio, fin, turno, _FECHA_TEST, ahora=ahora)
    assert marcadas.salida is None
    assert marcadas.salida_es_asumida is False


def test_parear_salida_asumida_en_turno_nocturno_cae_dia_siguiente() -> None:
    """Turno nocturno 22:00-06:00, solo entrada a las 22:05. Cuando se
    asume salida, va al día siguiente (06:00 del 16)."""
    turno = _turno(
        hora_entrada="22:00",
        hora_salida="06:00",
        cruza_medianoche=True,
        tol_entrada=10,
        tol_salida=10,
    )
    raws = [_raw("2026-04-15T22:05:00")]
    inicio, fin = ventana_del_dia(turno, _FECHA_TEST)
    ahora = datetime(2026, 4, 17, 9, 0)  # día más-más cerrado
    marcadas = parear_marcadas(raws, inicio, fin, turno, _FECHA_TEST, ahora=ahora)
    assert marcadas.entrada == datetime(2026, 4, 15, 22, 5)
    assert marcadas.salida == datetime(2026, 4, 16, 6, 0)
    assert marcadas.salida_es_asumida is True


def test_parear_marcadas_todas_dentro_de_la_hora_de_rebote_son_incompletas() -> None:
    """Entró 8:00 y volvió a marcar 8:30 — ninguna después del cutoff.
    Si el día sigue abierto: salida = None. Si cerró: salida asumida."""
    turno = _turno(hora_entrada="08:00", hora_salida="17:00")
    raws = [
        _raw("2026-04-15T08:00:00"),
        _raw("2026-04-15T08:30:00"),  # rebote
    ]
    inicio, fin = _ventana_default()
    # día abierto → salida None
    abierto = datetime(2026, 4, 15, 12, 0)
    m1 = parear_marcadas(raws, inicio, fin, turno, _FECHA_TEST, ahora=abierto)
    assert m1.entrada == datetime(2026, 4, 15, 8, 0)
    assert m1.salida is None

    # día cerrado → salida asumida
    cerrado = datetime(2026, 4, 16, 9, 0)
    m2 = parear_marcadas(raws, inicio, fin, turno, _FECHA_TEST, ahora=cerrado)
    assert m2.entrada == datetime(2026, 4, 15, 8, 0)
    assert m2.salida == datetime(2026, 4, 15, 17, 0)
    assert m2.salida_es_asumida is True


# ── derivar_estado ───────────────────────────────────────────────────────────


def test_derivar_ausente_cuando_no_hay_marcadas() -> None:
    turno = _turno()
    marcadas = MarcadasDelDia(entrada=None, salida=None)
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    assert resultado.estado == EstadoAsistencia.AUSENTE.value
    assert resultado.hora_entrada_real is None
    assert resultado.hora_salida_real is None
    assert resultado.minutos_tarde == 0
    assert resultado.minutos_salida_temprana == 0


def test_derivar_incompleto_solo_entrada() -> None:
    turno = _turno()
    marcadas = MarcadasDelDia(entrada=datetime(2026, 4, 15, 8, 0), salida=None)
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    assert resultado.estado == EstadoAsistencia.INCOMPLETO.value
    assert resultado.hora_entrada_real == "08:00:00"
    assert resultado.hora_salida_real is None


def test_derivar_incompleto_solo_salida() -> None:
    turno = _turno()
    marcadas = MarcadasDelDia(entrada=None, salida=datetime(2026, 4, 15, 17, 0))
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    assert resultado.estado == EstadoAsistencia.INCOMPLETO.value
    assert resultado.hora_entrada_real is None
    assert resultado.hora_salida_real == "17:00:00"


def test_derivar_presente_exacto() -> None:
    turno = _turno(hora_entrada="08:00", hora_salida="17:00")
    marcadas = MarcadasDelDia(
        entrada=datetime(2026, 4, 15, 8, 0),
        salida=datetime(2026, 4, 15, 17, 0),
    )
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    assert resultado.estado == EstadoAsistencia.PRESENTE.value
    assert resultado.minutos_tarde == 0
    assert resultado.minutos_salida_temprana == 0


def test_derivar_presente_dentro_de_tolerancia() -> None:
    """Entrada con 8 min tarde, tolerancia = 10 → PRESENTE."""
    turno = _turno(hora_entrada="08:00", hora_salida="17:00", tol_entrada=10, tol_salida=5)
    marcadas = MarcadasDelDia(
        entrada=datetime(2026, 4, 15, 8, 8),
        salida=datetime(2026, 4, 15, 16, 57),
    )
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    assert resultado.estado == EstadoAsistencia.PRESENTE.value
    # Pero los minutos se reportan igual (desviación estricta).
    assert resultado.minutos_tarde == 8
    assert resultado.minutos_salida_temprana == 3


def test_derivar_tarde_cuando_supera_tolerancia() -> None:
    turno = _turno(hora_entrada="08:00", hora_salida="17:00", tol_entrada=10)
    marcadas = MarcadasDelDia(
        entrada=datetime(2026, 4, 15, 8, 15),  # 15 min > tol 10
        salida=datetime(2026, 4, 15, 17, 0),
    )
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    assert resultado.estado == EstadoAsistencia.TARDE.value
    assert resultado.minutos_tarde == 15
    assert resultado.minutos_salida_temprana == 0


def test_derivar_salida_temprana() -> None:
    turno = _turno(hora_entrada="08:00", hora_salida="17:00", tol_salida=5)
    marcadas = MarcadasDelDia(
        entrada=datetime(2026, 4, 15, 8, 0),
        salida=datetime(2026, 4, 15, 16, 30),  # 30 min antes > tol 5
    )
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    assert resultado.estado == EstadoAsistencia.SALIDA_TEMPRANA.value
    assert resultado.minutos_tarde == 0
    assert resultado.minutos_salida_temprana == 30


def test_derivar_tarde_y_salida_temprana() -> None:
    turno = _turno(hora_entrada="08:00", hora_salida="17:00", tol_entrada=10, tol_salida=5)
    marcadas = MarcadasDelDia(
        entrada=datetime(2026, 4, 15, 8, 30),
        salida=datetime(2026, 4, 15, 16, 0),
    )
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    assert resultado.estado == EstadoAsistencia.TARDE_Y_SALIDA_TEMPRANA.value
    assert resultado.minutos_tarde == 30
    assert resultado.minutos_salida_temprana == 60


def test_derivar_turno_nocturno_presente() -> None:
    """Turno de vigilancia 22:00 → 06:00 del día siguiente."""
    turno = _turno(
        hora_entrada="22:00",
        hora_salida="06:00",
        cruza_medianoche=True,
        tol_entrada=10,
        tol_salida=10,
    )
    marcadas = MarcadasDelDia(
        entrada=datetime(2026, 4, 15, 22, 0),
        salida=datetime(2026, 4, 16, 6, 0),
    )
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    assert resultado.estado == EstadoAsistencia.PRESENTE.value


def test_derivar_turno_nocturno_tarde() -> None:
    turno = _turno(
        hora_entrada="22:00",
        hora_salida="06:00",
        cruza_medianoche=True,
        tol_entrada=10,
    )
    marcadas = MarcadasDelDia(
        entrada=datetime(2026, 4, 15, 22, 25),  # 25 min tarde
        salida=datetime(2026, 4, 16, 6, 0),
    )
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    assert resultado.estado == EstadoAsistencia.TARDE.value
    assert resultado.minutos_tarde == 25


def test_derivar_minutos_se_truncan_a_cero_si_entrada_temprana() -> None:
    """Si el empleado llega antes de la hora oficial, minutos_tarde = 0."""
    turno = _turno(hora_entrada="08:00", hora_salida="17:00")
    marcadas = MarcadasDelDia(
        entrada=datetime(2026, 4, 15, 7, 45),
        salida=datetime(2026, 4, 15, 17, 0),
    )
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    assert resultado.minutos_tarde == 0


# ── estado_no_trabaja ────────────────────────────────────────────────────────


def test_estado_feriado_gana_sobre_sin_turno() -> None:
    turno = _turno(dias=DIAS_LABORALES)
    # 2026-04-11 es sábado (no laborable) + feriado.
    assert estado_no_trabaja(turno, "2026-04-11", es_feriado=True) == EstadoAsistencia.FERIADO.value


def test_estado_feriado_cuando_turno_aplica() -> None:
    turno = _turno(dias=DIAS_LABORALES)
    # 2026-04-15 es miércoles (laboral) pero es feriado.
    assert estado_no_trabaja(turno, "2026-04-15", es_feriado=True) == EstadoAsistencia.FERIADO.value


def test_estado_sin_turno_porque_no_hay_asignacion() -> None:
    assert estado_no_trabaja(None, "2026-04-15", es_feriado=False) == (
        EstadoAsistencia.SIN_TURNO.value
    )


def test_estado_sin_turno_porque_dia_no_aplica() -> None:
    turno = _turno(dias=DIAS_LABORALES)  # lun-vie
    # 2026-04-11 = sábado → no aplica.
    assert estado_no_trabaja(turno, "2026-04-11", es_feriado=False) == (
        EstadoAsistencia.SIN_TURNO.value
    )


def test_estado_dia_aplica_devuelve_none_para_continuar() -> None:
    turno = _turno(dias=DIAS_LABORALES)
    # 2026-04-15 = miércoles → aplica.
    assert estado_no_trabaja(turno, "2026-04-15", es_feriado=False) is None


def test_estado_turno_solo_domingo_sabado() -> None:
    turno = _turno(dias=DIAS_FIN_DE_SEMANA)
    # 2026-04-15 = miércoles → no aplica a turno de fin de semana.
    assert estado_no_trabaja(turno, "2026-04-15", es_feriado=False) == (
        EstadoAsistencia.SIN_TURNO.value
    )
    # 2026-04-11 = sábado → sí aplica.
    assert estado_no_trabaja(turno, "2026-04-11", es_feriado=False) is None


def test_estado_turno_toda_semana_nunca_sin_turno() -> None:
    turno = _turno(dias=DIAS_TODA_LA_SEMANA)
    for fecha in ("2026-04-11", "2026-04-12", "2026-04-13", "2026-04-17"):
        assert estado_no_trabaja(turno, fecha, es_feriado=False) is None


def test_estado_turno_solo_lunes() -> None:
    turno = _turno(dias=LUNES)
    # 2026-04-13 = lunes → aplica.
    assert estado_no_trabaja(turno, "2026-04-13", es_feriado=False) is None
    # 2026-04-14 = martes → no aplica.
    assert estado_no_trabaja(turno, "2026-04-14", es_feriado=False) == (
        EstadoAsistencia.SIN_TURNO.value
    )


# ── Integración ligera algoritmo completo ────────────────────────────────────


def test_flujo_completo_empleado_tarde() -> None:
    """Flujo integrado: ventana → parear → derivar, con llegada tarde.

    Sub-2.7e: 8:15 sigue siendo "tarde" frente a una hora oficial 8:00
    con tolerancia 10. El nuevo algoritmo no descarta esta marcada — al
    contrario, la respeta como entrada real.
    """
    turno = _turno(hora_entrada="08:00", hora_salida="17:00", tol_entrada=10, tol_salida=5)
    raws = [
        _raw("2026-04-15T08:15:00", tipo=TipoMarcada.CHECK_IN.value),
        _raw("2026-04-15T17:00:00", tipo=TipoMarcada.CHECK_OUT.value),
    ]
    inicio, fin = ventana_del_dia(turno, "2026-04-15")
    marcadas = parear_marcadas(raws, inicio, fin, turno, "2026-04-15", ahora=_AHORA_DIA_CERRADO)
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    assert resultado.estado == EstadoAsistencia.TARDE.value
    assert resultado.minutos_tarde == 15


def test_flujo_completo_turno_nocturno() -> None:
    """Turno nocturno 22:00-06:00 — entrada 22:03, salida 05:58 al día
    siguiente. Sub-2.7e: la entrada es la PRIMERA marcada y la salida
    cae > 60 min después → directo a la salida."""
    turno = _turno(
        hora_entrada="22:00",
        hora_salida="06:00",
        cruza_medianoche=True,
        tol_entrada=10,
        tol_salida=10,
    )
    raws = [
        _raw("2026-04-15T22:03:00"),
        _raw("2026-04-16T05:58:00"),
    ]
    inicio, fin = ventana_del_dia(turno, "2026-04-15")
    marcadas = parear_marcadas(raws, inicio, fin, turno, "2026-04-15", ahora=_AHORA_DIA_CERRADO)
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    assert resultado.estado == EstadoAsistencia.PRESENTE.value
    assert resultado.hora_entrada_real == "22:03:00"
    assert resultado.hora_salida_real == "05:58:00"


def test_flujo_completo_empleado_marca_en_varios_relojes() -> None:
    """Un empleado con marcadas de 2 dispositivos distintos se trata igual."""
    turno = _turno()
    raws = [
        RegistroRaw(  # reloj A, entrada
            id=None,
            dispositivo_id=1,
            sincronizacion_id=10,
            zkteco_user_id=42,
            timestamp="2026-04-15T08:00:00",
            tipo_marcada=TipoMarcada.CHECK_IN.value,
        ),
        RegistroRaw(  # reloj B, salida (otro edificio)
            id=None,
            dispositivo_id=2,
            sincronizacion_id=11,
            zkteco_user_id=42,
            timestamp="2026-04-15T17:00:00",
            tipo_marcada=TipoMarcada.CHECK_OUT.value,
        ),
    ]
    inicio, fin = ventana_del_dia(turno, "2026-04-15")
    marcadas = parear_marcadas(raws, inicio, fin, turno, "2026-04-15", ahora=_AHORA_DIA_CERRADO)
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    assert resultado.estado == EstadoAsistencia.PRESENTE.value


def test_flujo_completo_salida_al_dia_siguiente_queda_fuera_de_turno_diurno() -> None:
    """Si una marcada está al día siguiente y el turno NO cruza medianoche,
    la marcada no entra a la ventana. Sub-2.7e: el día está cerrado, así
    que la falta de salida real se compensa con la asumida del turno."""
    turno = _turno(hora_entrada="08:00", hora_salida="17:00")  # diurno
    raws = [
        _raw("2026-04-15T08:00:00", tipo=TipoMarcada.CHECK_IN.value),
        _raw("2026-04-16T02:00:00", tipo=TipoMarcada.CHECK_OUT.value),  # día siguiente
    ]
    inicio, fin = ventana_del_dia(turno, "2026-04-15")
    marcadas = parear_marcadas(raws, inicio, fin, turno, "2026-04-15", ahora=_AHORA_DIA_CERRADO)
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    # La marcada del 16 cae fuera de la ventana del 15 (diurno).
    # Sólo queda la entrada — y como ahora ya pasó el cierre del día,
    # se asume salida = 17:00 (hora oficial del turno).
    assert resultado.estado == EstadoAsistencia.PRESENTE.value
    assert resultado.hora_entrada_real == "08:00:00"
    assert resultado.hora_salida_real == "17:00:00"
    assert marcadas.salida_es_asumida is True


def test_parear_timestamp_invalido_lanza_value_error() -> None:
    raws = [_raw("not-a-timestamp")]
    turno = _turno(hora_entrada="08:00", hora_salida="17:00")
    with pytest.raises(ValueError, match="Timestamp inválido"):
        parear_marcadas(
            raws,
            datetime(2026, 4, 15),
            datetime(2026, 4, 16),
            turno,
            "2026-04-15",
            ahora=_AHORA_DIA_CERRADO,
        )


# ── Edge: delta de segundos se trunca hacia abajo ────────────────────────────


def test_minutos_tarde_trunca_segundos() -> None:
    """89 segundos tarde → 1 minuto (segundos se truncan con //)."""
    turno = _turno(hora_entrada="08:00", hora_salida="17:00", tol_entrada=0)
    marcadas = MarcadasDelDia(
        entrada=datetime(2026, 4, 15, 8, 1, 29),
        salida=datetime(2026, 4, 15, 17, 0),
    )
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    assert resultado.minutos_tarde == 1
    assert resultado.estado == EstadoAsistencia.TARDE.value


def test_ventana_del_dia_acepta_turno_de_un_segundo() -> None:
    """Sanity: con tolerancias 0 y horas extremas, la ventana sigue siendo válida."""
    turno = _turno(hora_entrada="00:00", hora_salida="23:59", tol_entrada=0, tol_salida=0)
    inicio, fin = ventana_del_dia(turno, "2026-04-15")
    assert fin > inicio
    assert (fin - inicio) == timedelta(hours=23, minutes=59)
