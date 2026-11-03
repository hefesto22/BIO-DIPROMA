"""Funciones puras del algoritmo de consolidación raw → asistencia.

Este módulo contiene la lógica determinística que decide, para un
empleado y un día:

    1. Qué turno le aplicaba (consultando su historial de asignaciones).
    2. Qué marcadas pertenecen a ese día (ventana temporal correcta,
       incluyendo turnos que cruzan medianoche).
    3. Cuáles son la entrada y la salida (algoritmo "ventana dinámica"
       — Sub-2.7e, redefinido a partir de Sub-2.7d).
    4. Qué estado final de asistencia corresponde (aplicando tolerancias
       de entrada y salida configuradas por turno).

Algoritmo "ventana dinámica" (Sub-2.7e):
    El K40 (firmware estándar ZKTeco) NO distingue entrada vs salida al
    pulsar — todas las marcadas suelen llegar como CHECK_OUT o UNKNOWN.
    En vez de confiar en ese campo Y en vez de fijar el cutoff a la
    hora oficial del turno (Sub-2.7d), usamos la PRIMERA marcada del
    empleado como ancla:

        - La PRIMERA marcada del día = ENTRADA, sin importar la hora
          (sea 7:00, 10:30 o 14:00). Cubre los casos en los que el
          empleado llega tarde — se respeta su hora real de llegada.
        - cutoff_salida = entrada + ``VENTANA_REBOTE_MINUTOS`` (default 60)
        - Marcadas dentro de ``(entrada, cutoff_salida]`` son rebotes
          (el operador apoya el dedo varias veces al entrar) y se
          ignoran.
        - Marcadas > cutoff_salida son candidatas a SALIDA. La última
          gana — incluso si está después de la hora_salida del turno
          (ej. entró 7:00, marcó 19:00 → salida = 19:00, no 17:00).
        - Si NO hay marcada > cutoff_salida y el caller indica que el
          día ya cerró (fecha pasada, o fecha actual con hora >= 20:00),
          se asume salida = hora_salida del turno asignado y se anota
          una observación auditable. Si el día aún no cerró, salida
          queda en None y el estado deriva a INCOMPLETO.

    El campo ``tipo_marcada`` del raw queda como auditoría histórica
    pero NO se usa para el pareo. Esto generaliza al hecho que en la
    mayoría de relojes ZKTeco low-end el operador no presiona la tecla
    de entrada/salida explícita — solo apoya el dedo.

Se extrae a un módulo aparte — separado de ``ConsolidacionService`` —
porque son funciones puras sin side effects. Esto permite testearlas
exhaustivamente sin montar BD ni fakes.

Convenciones:
    - Todas las horas en formato ``"HH:MM"`` 24h (entrada/salida del turno).
    - Todos los timestamps en formato ``"YYYY-MM-DDTHH:MM:SS"`` (raw).
    - Todas las fechas en formato ISO ``"YYYY-MM-DD"``.
    - Turno que cruza medianoche: la fecha de la asistencia es el día de
      ENTRADA aunque la salida caiga al día siguiente (convención del
      modelo ``Asistencia``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import List, Optional, Tuple

from core.models.asistencia import OBSERVACION_SALIDA_ASUMIDA, EstadoAsistencia
from core.models.empleado_turno import EmpleadoTurno
from core.models.registro_raw import RegistroRaw
from core.models.turno import Turno

# Minutos después de la PRIMERA marcada del día durante los cuales las
# marcadas posteriores se consideran "rebote" (el operador apoyó el
# dedo varias veces al entrar) y se ignoran. La siguiente marcada
# fuera de esta ventana es candidata a salida.
#
# 60 cubre con holgura los rebotes típicos sin cerrar la puerta a
# salidas legítimas tempranas (algún empleado puede salir 1h+ después
# de entrar, por ejemplo en pausas largas). Si una organización quisiera
# otra ventana, mover a config.py o a un campo del turno.
VENTANA_REBOTE_MINUTOS: int = 60

# Ventana de CAPTURA alrededor del horario del día: las marcas dentro de
# ella cuentan para ese día. Es independiente de las tolerancias, que solo
# deciden si fue TARDE o SALIDA_TEMPRANA. Antes la ventana ERAN las
# tolerancias (10 min antes de la entrada, 0 después de la salida): quien
# llegaba 15 min antes o salía a las 17:05 perdía sus marcas y quedaba
# AUSENTE o con "salida asumida".
#
# 4 h antes de la entrada y 6 h después de la salida cubren llegadas
# tempranas y horas extra. Si el turno es tan largo que la ventana pasaría
# de 24 h, los márgenes se recortan para que dos días seguidos del mismo
# turno nunca se pisen.
VENTANA_ANTES_ENTRADA_MINUTOS: int = 240
VENTANA_DESPUES_SALIDA_MINUTOS: int = 360

# Hora del día (formato local) a partir de la cual, si el empleado
# tiene entrada pero no salida marcada, se asume que su salida fue la
# hora oficial de su turno ("hizo el día completo"). Antes de las
# 20:00 dejamos la asistencia como INCOMPLETO porque el empleado todavía
# podría volver a marcar.
HORA_LIMITE_DIA: time = time(20, 0)

# ── Formatos ──────────────────────────────────────────────────────────────────

_FMT_FECHA: str = "%Y-%m-%d"
_FMT_TIMESTAMP: str = "%Y-%m-%dT%H:%M:%S"
_FMT_HORA: str = "%H:%M"


# ── Resultado del pareo de marcadas ───────────────────────────────────────────


@dataclass(frozen=True)
class MarcadasDelDia:
    """Entrada y salida inferidas de un grupo de marcadas para un día.

    Attributes:
        entrada: Timestamp de entrada efectiva, o ``None`` si no hay marcada
            clasificable como entrada.
        salida: Timestamp de salida efectiva, o ``None`` si no hay marcada
            clasificable como salida (y el día aún no cerró).
        salida_es_asumida: ``True`` cuando la salida fue derivada del
            turno (no marcada físicamente) por haber pasado la hora
            límite del día sin segunda marcada. Útil para reportes
            auditables.
        observacion: Texto en español apto para mostrar/exportar cuando
            corresponde explicar el origen de la salida (típicamente,
            "Salida asumida según turno: sin segunda marcada"). ``None``
            cuando no hay nada relevante que comunicar.
    """

    entrada: Optional[datetime]
    salida: Optional[datetime]
    salida_es_asumida: bool = False
    observacion: Optional[str] = None


# ── Resolución de turno vigente en una fecha histórica ────────────────────────


def resolver_turno_en_fecha(
    historial: List[EmpleadoTurno],
    fecha_iso: str,
    turnos_por_id: Optional[dict[int, Turno]] = None,
) -> Optional[int]:
    """Devuelve el ``turno_id`` aplicable al empleado en la fecha dada.

    Sub-3.2.B: cuando se pasa ``turnos_por_id``, además de chequear que
    la asignación esté vigente en la fecha, se valida que el bitmask
    ``dias_semana`` del turno incluya el día de la semana de ``fecha``.
    Esto permite que un empleado tenga varias asignaciones vigentes
    paralelas (ej. un turno para lun-vie y otro para sáb), y que la
    consolidación elija la correcta para cada día.

    Cuando ``turnos_por_id`` es ``None``, se mantiene el comportamiento
    legacy (devuelve la primera asignación que cubra la fecha) — útil
    para flujos que aún no tengan el catálogo de turnos a mano.

    Args:
        historial: Todas las filas de ``empleado_turnos`` del empleado
            (cualquier orden). Vacío significa "sin asignaciones".
        fecha_iso: Fecha a consultar en formato ``"YYYY-MM-DD"``.
        turnos_por_id: Catálogo de turnos. Si se pasa, se filtra por
            ``turno.aplica_en(weekday)`` (todos sus horarios).

    Returns:
        El ``turno_id`` aplicable, o ``None`` si ninguna asignación
        cubre esa fecha y ese día de la semana.

    Raises:
        ValueError: Si ``fecha_iso`` no es parseable.
    """
    fecha_consulta = _parse_fecha(fecha_iso, campo="fecha_iso")
    weekday = fecha_consulta.weekday()
    for asignacion in historial:
        if not _asignacion_cubre_fecha(asignacion, fecha_consulta):
            continue
        if turnos_por_id is not None:
            turno = turnos_por_id.get(asignacion.turno_id)
            # ``aplica_en`` mira todos los horarios del turno (p. ej. lun-vie
            # y sábado con otra hora), no solo el principal.
            if turno is None or not turno.aplica_en(weekday):
                continue
        return asignacion.turno_id
    return None


def _asignacion_cubre_fecha(asignacion: EmpleadoTurno, fecha: date) -> bool:
    """Predicado puro: ¿la asignación cubre la fecha dada?

    Una asignación cubre la fecha si:
        fecha_inicio <= fecha  AND
        (fecha_fin is None  OR  fecha_fin >= fecha)
    """
    inicio = _parse_fecha(asignacion.fecha_inicio, campo="fecha_inicio")
    if fecha < inicio:
        return False
    if asignacion.fecha_fin is None:
        return True
    fin = _parse_fecha(asignacion.fecha_fin, campo="fecha_fin")
    return fecha <= fin


# ── Ventana temporal para un día de turno ─────────────────────────────────────


def ventana_del_dia(turno: Turno, fecha_iso: str) -> Tuple[datetime, datetime]:
    """Calcula la ventana ``[inicio, fin]`` en la que caen las marcadas del día.

    La ventana se dimensiona así:

        - Turno normal (``cruza_medianoche=False``):
              [fecha T hora_entrada - margen_antes,
               fecha T hora_salida  + margen_despues]

        - Turno nocturno (``cruza_medianoche=True``):
              [fecha      T hora_entrada - margen_antes,
               fecha+1día T hora_salida  + margen_despues]

    Márgenes: ``VENTANA_ANTES_ENTRADA_MINUTOS`` (4 h) y
    ``VENTANA_DESPUES_SALIDA_MINUTOS`` (6 h), recortados en proporción si
    la ventana pasaría de 24 h. Las tolerancias del turno NO acotan la
    ventana (solo deciden el estado); se usan como mínimo por si algún
    turno tuviera una tolerancia mayor que el margen.

    Args:
        turno: Turno vigente del empleado ese día.
        fecha_iso: Día de ENTRADA del turno, formato ``"YYYY-MM-DD"``.

    Returns:
        Tupla ``(inicio_ventana, fin_ventana)`` como ``datetime`` naive.

    Raises:
        ValueError: Si alguna de las horas o la fecha son inválidas.
    """
    dia = _parse_fecha(fecha_iso, campo="fecha_iso")
    hora_entrada = _parse_hora(turno.hora_entrada, campo="hora_entrada")
    hora_salida = _parse_hora(turno.hora_salida, campo="hora_salida")

    entrada_oficial = datetime.combine(dia, hora_entrada)
    dia_salida = dia + timedelta(days=1) if turno.cruza_medianoche else dia
    salida_oficial = datetime.combine(dia_salida, hora_salida)

    antes, despues = _margenes_de_captura(
        int((salida_oficial - entrada_oficial).total_seconds() // 60)
    )
    inicio_ventana = entrada_oficial - timedelta(
        minutes=max(antes, turno.minutos_tolerancia_entrada)
    )
    fin_ventana = salida_oficial + timedelta(minutes=max(despues, turno.minutos_tolerancia_salida))
    return inicio_ventana, fin_ventana


def _margenes_de_captura(duracion_minutos: int) -> Tuple[int, int]:
    """Minutos de captura antes y después del horario, sin pasar de 24 h en total."""
    libre = max(0, 24 * 60 - duracion_minutos - 1)  # -1: días seguidos nunca comparten un minuto
    total = VENTANA_ANTES_ENTRADA_MINUTOS + VENTANA_DESPUES_SALIDA_MINUTOS
    if libre >= total:
        return VENTANA_ANTES_ENTRADA_MINUTOS, VENTANA_DESPUES_SALIDA_MINUTOS
    antes = libre * VENTANA_ANTES_ENTRADA_MINUTOS // total
    return antes, libre - antes


# ── Pareo de marcadas (algoritmo "ventana dinámica", Sub-2.7e) ───────────────


_OBS_SALIDA_ASUMIDA: str = OBSERVACION_SALIDA_ASUMIDA


def parear_marcadas(
    registros: List[RegistroRaw],
    inicio_ventana: datetime,
    fin_ventana: datetime,
    turno: Turno,
    fecha_iso: str,
    ahora: datetime,
) -> MarcadasDelDia:
    """Determina la entrada y la salida efectivas para un grupo de marcadas.

    Algoritmo "ventana dinámica" (Sub-2.7e):

        1. Filtra marcadas dentro de ``[inicio_ventana, fin_ventana]``.
        2. La PRIMERA marcada de la ventana es la ENTRADA, sin importar
           la hora del día. Cubre llegadas tardías como entrada real.
        3. ``cutoff_salida = entrada + VENTANA_REBOTE_MINUTOS``. Las
           marcadas dentro de ``(entrada, cutoff_salida]`` se ignoran
           como rebote (el operador apoyó el dedo más de una vez al
           entrar).
        4. Las marcadas con ``timestamp > cutoff_salida`` son candidatas
           a SALIDA. La ÚLTIMA gana — y prevalece sobre la hora_salida
           del turno (ej. si entró 7:00 y marcó 19:00, la salida es 19:00
           aunque su turno fuera 8-17).
        5. Si NO hay marcadas posteriores al cutoff y el día ya cerró
           (fecha de la asistencia anterior a ``ahora.date()`` o, si es
           el mismo día, ``ahora.time() >= HORA_LIMITE_DIA``), se asume
           ``salida = hora_salida`` del turno y se anota la observación.
           Si el día aún no cerró, ``salida = None`` y el estado caerá
           en INCOMPLETO.
        6. NO se inspecciona ``tipo_marcada``: el K40 estándar reporta
           todo como CHECK_OUT/UNKNOWN, así que no es confiable.

    Args:
        registros: Marcadas candidatas (cualquier orden; se ordena
            internamente). Pueden venir de cualquier dispositivo — se
            asume que el caller ya filtró por empleado.
        inicio_ventana: Límite inferior inclusive (marcadas anteriores
            se descartan).
        fin_ventana: Límite superior inclusive (marcadas posteriores se
            descartan).
        turno: Turno vigente del empleado ese día. Necesario para poder
            asumir la salida cuando no hay segunda marcada y el día ya
            cerró.
        fecha_iso: Día de ENTRADA del turno, ``"YYYY-MM-DD"``.
        ahora: Datetime de referencia para decidir si el día ya cerró.
            En producción es ``datetime.now()``; en tests se inyecta
            fijo para validar las dos ramas (día abierto vs cerrado)
            sin acoplar a un reloj real.

    Returns:
        ``MarcadasDelDia`` con entrada y salida, banderas si la salida
        fue asumida y la observación correspondiente.
    """
    en_ventana = _filtrar_y_ordenar(registros, inicio_ventana, fin_ventana)
    if not en_ventana:
        return MarcadasDelDia(entrada=None, salida=None)

    timestamps = [_parse_timestamp(r.timestamp) for r in en_ventana]
    entrada = timestamps[0]
    cutoff_salida = entrada + timedelta(minutes=VENTANA_REBOTE_MINUTOS)

    candidatas_salida = [ts for ts in timestamps[1:] if ts > cutoff_salida]
    if candidatas_salida:
        return MarcadasDelDia(entrada=entrada, salida=candidatas_salida[-1])

    # No hay segunda marcada válida: decidimos si el día ya cerró.
    salida_asumida = _calcular_salida_asumida(turno, fecha_iso, ahora)
    if salida_asumida is None:
        return MarcadasDelDia(entrada=entrada, salida=None)
    return MarcadasDelDia(
        entrada=entrada,
        salida=salida_asumida,
        salida_es_asumida=True,
        observacion=_OBS_SALIDA_ASUMIDA,
    )


def _calcular_salida_asumida(turno: Turno, fecha_iso: str, ahora: datetime) -> Optional[datetime]:
    """Devuelve la salida asumida según turno, o ``None`` si el día sigue abierto.

    Reglas:
        - ``fecha < ahora.date()``: día cerrado → asumir.
        - ``fecha == ahora.date()`` y ``ahora.time() >= HORA_LIMITE_DIA``
          → día cerrado → asumir.
        - En cualquier otro caso (incluido el futuro) → ``None``.

    La hora asumida es ``hora_salida`` del turno, con la corrección de
    día siguiente cuando el turno cruza medianoche — análogo a cómo
    ``ventana_del_dia`` calcula el fin de ventana.
    """
    fecha = _parse_fecha(fecha_iso, campo="fecha_iso")
    if not _dia_ya_cerrado(fecha, ahora):
        return None
    hora_salida = _parse_hora(turno.hora_salida, campo="hora_salida")
    dia_salida = fecha + timedelta(days=1) if turno.cruza_medianoche else fecha
    return datetime.combine(dia_salida, hora_salida)


def _dia_ya_cerrado(fecha: date, ahora: datetime) -> bool:
    """Predicado puro: ¿el día de la asistencia ya está cerrado para registrar?"""
    if fecha < ahora.date():
        return True
    if fecha == ahora.date() and ahora.time() >= HORA_LIMITE_DIA:
        return True
    return False


def _filtrar_y_ordenar(
    registros: List[RegistroRaw],
    inicio: datetime,
    fin: datetime,
) -> List[RegistroRaw]:
    """Descarta marcadas fuera de la ventana y ordena por timestamp ASC."""
    dentro: List[Tuple[datetime, RegistroRaw]] = []
    for r in registros:
        ts = _parse_timestamp(r.timestamp)
        if inicio <= ts <= fin:
            dentro.append((ts, r))
    dentro.sort(key=lambda par: par[0])
    return [r for _, r in dentro]


# ── Derivación del estado de asistencia ───────────────────────────────────────


@dataclass(frozen=True)
class EstadoDerivado:
    """Output de ``derivar_estado``: estado + minutos + horas formateadas.

    Los campos quedan listos para construir un ``Asistencia`` sin
    transformaciones adicionales en el servicio.

    Attributes:
        estado: Uno de ``EstadoAsistencia.*.value``.
        hora_entrada_real: ``"HH:MM:SS"`` o ``None``.
        hora_salida_real: ``"HH:MM:SS"`` o ``None``.
        minutos_tarde: 0 cuando no aplica.
        minutos_salida_temprana: 0 cuando no aplica.
    """

    estado: str
    hora_entrada_real: Optional[str]
    hora_salida_real: Optional[str]
    minutos_tarde: int
    minutos_salida_temprana: int


def derivar_estado(marcadas: MarcadasDelDia, turno: Turno, fecha_iso: str) -> EstadoDerivado:
    """Calcula estado + minutos de desviación a partir de marcadas y turno.

    Reglas (aplicadas en orden):

        1. Si no hay entrada y no hay salida → AUSENTE.
        2. Si falta una de las dos → INCOMPLETO (con la que sí está).
        3. Si hay ambas:
              - Si entrada ≤ hora_oficial_entrada + tolerancia_entrada y
                salida ≥ hora_oficial_salida - tolerancia_salida → PRESENTE.
              - tarde = True si entrada > hora_oficial_entrada + tolerancia_entrada.
              - salida_temprana = True si salida < hora_oficial_salida - tolerancia_salida.
              - Combinar flags para TARDE, SALIDA_TEMPRANA o TARDE_Y_SALIDA_TEMPRANA.

    Los minutos reportados son los de desviación ESTRICTA (sin tolerancia):

        minutos_tarde = max(0, entrada - hora_oficial_entrada) en minutos.
        minutos_salida_temprana = max(0, hora_oficial_salida - salida).

    Se reportan incluso cuando están dentro de tolerancia (útil para
    reportes ejecutivos); la tolerancia solo afecta el valor de ``estado``.

    Args:
        marcadas: Entrada y salida del día (puede tener ``None`` en uno
            o ambos campos).
        turno: Turno vigente ese día.
        fecha_iso: Día de ENTRADA del turno, ``"YYYY-MM-DD"``. Se usa
            para decidir a qué fecha se refiere cada hora oficial.

    Returns:
        ``EstadoDerivado`` con el estado final + campos derivados.

    Raises:
        ValueError: Si las horas del turno son inválidas.
    """
    entrada = marcadas.entrada
    salida = marcadas.salida

    if entrada is None and salida is None:
        return EstadoDerivado(
            estado=EstadoAsistencia.AUSENTE.value,
            hora_entrada_real=None,
            hora_salida_real=None,
            minutos_tarde=0,
            minutos_salida_temprana=0,
        )

    if entrada is None or salida is None:
        return EstadoDerivado(
            estado=EstadoAsistencia.INCOMPLETO.value,
            hora_entrada_real=_fmt_hora_real(entrada),
            hora_salida_real=_fmt_hora_real(salida),
            minutos_tarde=0,
            minutos_salida_temprana=0,
        )

    entrada_oficial, salida_oficial = _horas_oficiales(turno, fecha_iso)
    minutos_tarde = _minutos_positivos(entrada - entrada_oficial)
    minutos_temprana = _minutos_positivos(salida_oficial - salida)
    es_tarde = minutos_tarde > turno.minutos_tolerancia_entrada
    es_temprana = minutos_temprana > turno.minutos_tolerancia_salida

    estado = _combinar_flags(es_tarde, es_temprana)
    return EstadoDerivado(
        estado=estado,
        hora_entrada_real=_fmt_hora_real(entrada),
        hora_salida_real=_fmt_hora_real(salida),
        minutos_tarde=minutos_tarde,
        minutos_salida_temprana=minutos_temprana,
    )


def _horas_oficiales(turno: Turno, fecha_iso: str) -> Tuple[datetime, datetime]:
    """Construye los datetimes oficiales de entrada y salida del turno ese día."""
    dia = _parse_fecha(fecha_iso, campo="fecha_iso")
    entrada_time = _parse_hora(turno.hora_entrada, campo="hora_entrada")
    salida_time = _parse_hora(turno.hora_salida, campo="hora_salida")
    entrada_oficial = datetime.combine(dia, entrada_time)
    dia_salida = dia + timedelta(days=1) if turno.cruza_medianoche else dia
    salida_oficial = datetime.combine(dia_salida, salida_time)
    return entrada_oficial, salida_oficial


def _combinar_flags(es_tarde: bool, es_temprana: bool) -> str:
    """Devuelve el valor de ``EstadoAsistencia`` según los flags."""
    if es_tarde and es_temprana:
        return EstadoAsistencia.TARDE_Y_SALIDA_TEMPRANA.value
    if es_tarde:
        return EstadoAsistencia.TARDE.value
    if es_temprana:
        return EstadoAsistencia.SALIDA_TEMPRANA.value
    return EstadoAsistencia.PRESENTE.value


def _minutos_positivos(delta: timedelta) -> int:
    """Devuelve los minutos del delta, truncado a 0 si es negativo.

    Usamos ``int(delta.total_seconds() // 60)`` para que un delta de
    89 segundos cuente como 1 minuto (no 0) — más estricto en frontera.
    """
    if delta.total_seconds() <= 0:
        return 0
    return int(delta.total_seconds() // 60)


def _fmt_hora_real(ts: Optional[datetime]) -> Optional[str]:
    """Formatea ``datetime`` a ``"HH:MM:SS"`` o devuelve ``None``."""
    if ts is None:
        return None
    return ts.strftime("%H:%M:%S")


# ── Cálculo del estado cuando no corresponde trabajar ─────────────────────────


def estado_no_trabaja(turno: Optional[Turno], fecha_iso: str, es_feriado: bool) -> Optional[str]:
    """Devuelve el estado si el empleado NO debía trabajar ese día, o ``None``.

    Se aplica en este orden:

        - Si ``es_feriado`` → ``FERIADO``. Gana sobre SIN_TURNO porque la
          intención del feriado es más fuerte que el calendario semanal
          del turno.
        - Si ``turno is None`` (no hay asignación vigente) → ``SIN_TURNO``.
        - Si el turno existe pero el día no aplica (bitmask
          ``dias_semana``) → ``SIN_TURNO``.
        - Si el turno aplica → ``None`` (el caller continúa con el flujo
          normal de marcadas).

    Args:
        turno: Turno vigente del empleado, o ``None``.
        fecha_iso: Día de consulta ``"YYYY-MM-DD"``.
        es_feriado: True si la fecha está en la tabla ``feriados``.

    Returns:
        El estado correspondiente o ``None`` si debe procesarse normalmente.
    """
    if es_feriado:
        return EstadoAsistencia.FERIADO.value
    if turno is None:
        return EstadoAsistencia.SIN_TURNO.value
    fecha = _parse_fecha(fecha_iso, campo="fecha_iso")
    if not turno.aplica_en(fecha.weekday()):
        return EstadoAsistencia.SIN_TURNO.value
    return None


# ── Helpers de parsing ────────────────────────────────────────────────────────


def _parse_fecha(valor: str, campo: str) -> date:
    """Parsea ``"YYYY-MM-DD"`` a ``date`` o lanza ``ValueError`` descriptivo."""
    try:
        return datetime.strptime(valor, _FMT_FECHA).date()
    except ValueError as exc:
        raise ValueError(f"Fecha inválida en '{campo}': '{valor}' (esperado YYYY-MM-DD)") from exc


def _parse_hora(valor: str, campo: str) -> time:
    """Parsea ``"HH:MM"`` 24h a ``time`` o lanza ``ValueError`` descriptivo."""
    try:
        return datetime.strptime(valor, _FMT_HORA).time()
    except ValueError as exc:
        raise ValueError(f"Hora inválida en '{campo}': '{valor}' (esperado HH:MM)") from exc


def _parse_timestamp(valor: str) -> datetime:
    """Parsea timestamp raw ``"YYYY-MM-DDTHH:MM:SS"`` a ``datetime`` naive."""
    try:
        return datetime.strptime(valor, _FMT_TIMESTAMP)
    except ValueError as exc:
        raise ValueError(f"Timestamp inválido: '{valor}' (esperado YYYY-MM-DDTHH:MM:SS)") from exc
