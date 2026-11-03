"""Balance de tiempo: minutos de atraso contra tiempo extra (funciones puras).

Cómo se cuenta (todo en minutos, desde la hora EXACTA del turno; la
tolerancia solo decide si el día se marca como "Tarde", no los minutos):

    - Atraso: entra a las 07:05 y su entrada es 07:00 → 5 minutos.
    - Salida temprana: sale a las 16:50 y su salida es 17:00 → 10 minutos.
    - Tiempo extra: sale a las 17:05 y su salida es 17:00 → 5 minutos.
    - Trabajo en día libre o feriado: si ese día no le tocaba y marcó al
      menos dos veces, el tiempo entre la primera y la última marca.

    Balance = tiempo extra − atraso − salida temprana.

    Un balance positivo es tiempo trabajado de más (a favor del empleado);
    uno negativo, tiempo que debe. Así el que llega 5 minutos tarde y se
    queda 5 minutos más queda en cero: se compensa.

    Si no hubo marca de salida y el día cerró, la consolidación toma la
    hora de salida del turno ("salida asumida"): ese día no suma extra ni
    salida temprana.

Los minutos se truncan igual que en la consolidación (89 segundos = 1 min).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Final, FrozenSet, Iterable, List, Optional, Sequence, Tuple

from core.models.asistencia import Asistencia, EstadoAsistencia

E = EstadoAsistencia

TRABAJADOS: Final[FrozenSet[str]] = frozenset(
    {
        E.PRESENTE.value,
        E.TARDE.value,
        E.SALIDA_TEMPRANA.value,
        E.TARDE_Y_SALIDA_TEMPRANA.value,
        E.INCOMPLETO.value,
    }
)
JUSTIFICADOS: Final[FrozenSet[str]] = frozenset(
    {E.VACACIONES.value, E.DIA_LIBRE.value, E.PERMISO.value, E.INCAPACIDAD.value}
)
DESCANSO: Final[FrozenSet[str]] = frozenset({E.SIN_TURNO.value, E.FERIADO.value})

_DIA_SEG = 24 * 3600


@dataclass(frozen=True)
class HorarioDia:
    """Horario que le tocaba al empleado un día (``"HH:MM"`` 24 h)."""

    entrada: str
    salida: str
    cruza_medianoche: bool = False

    @property
    def texto(self) -> str:
        return f"{self.entrada}–{self.salida}"

    @property
    def minutos(self) -> int:
        """Duración de la jornada (de la entrada a la salida, con almuerzo)."""
        inicio, fin = _segundos(self.entrada), _segundos(self.salida)
        if self.cruza_medianoche or fin <= inicio:
            fin += _DIA_SEG
        return (fin - inicio) // 60


@dataclass(frozen=True)
class TiempoDia:
    """Minutos de un empleado en un día.

    Atributos:
        programado: Minutos que debía estar (0 si no le tocaba, si el día
            está justificado o si todavía falta la marca de salida).
        trabajado: Minutos de la entrada a la salida (``None`` si falta
            alguna de las dos).
        tarde / salida_temprana / extra: Ver el docstring del módulo.
        en_descanso: Minutos trabajados en un día libre o feriado.
    """

    programado: int = 0
    trabajado: Optional[int] = None
    tarde: int = 0
    salida_temprana: int = 0
    extra: int = 0
    en_descanso: int = 0

    @property
    def extra_total(self) -> int:
        return self.extra + self.en_descanso

    @property
    def balance(self) -> int:
        return self.extra_total - self.tarde - self.salida_temprana


@dataclass(frozen=True)
class TiempoEmpleado:
    """Suma de los ``TiempoDia`` de un empleado en el período.

    Atributos (minutos, salvo los conteos de días):
        programado / trabajado: Para el cumplimiento de jornada (solo días
            con entrada y salida, más las faltas con su jornada completa).
        tarde / salida_temprana / extra / en_descanso: Acumulados.
        dias_con_atraso: Días con al menos un minuto de atraso.
        dias_con_salida_temprana / dias_con_extra: Ídem.
        dias_descanso_trabajados: Días libres o feriados en que trabajó.
        entrada_promedio: Hora promedio de entrada (``"07:58"``) o ``None``.
    """

    programado: int = 0
    trabajado: int = 0
    tarde: int = 0
    salida_temprana: int = 0
    extra: int = 0
    en_descanso: int = 0
    dias_con_atraso: int = 0
    dias_con_salida_temprana: int = 0
    dias_con_extra: int = 0
    dias_descanso_trabajados: int = 0
    entrada_promedio: Optional[str] = None

    @property
    def extra_total(self) -> int:
        return self.extra + self.en_descanso

    @property
    def descuentos(self) -> int:
        return self.tarde + self.salida_temprana

    @property
    def balance(self) -> int:
        return self.extra_total - self.descuentos

    @property
    def cumplimiento(self) -> Optional[float]:
        """Trabajado / programado (``None`` si no había nada programado)."""
        return self.trabajado / self.programado if self.programado else None


@dataclass
class _Acumulador:
    total: Dict[str, int] = field(default_factory=dict)
    entradas: List[int] = field(default_factory=list)


def tiempo_del_dia(
    asistencia: Asistencia,
    horario: Optional[HorarioDia],
    marcas_del_dia: Sequence[str] = (),
    salida_del_dia_anterior: Optional[str] = None,
) -> TiempoDia:
    """Minutos de un día.

    Args:
        asistencia: El renglón consolidado del día.
        horario: Lo que le tocaba ese día según su turno (``None`` si no
            tenía turno o el turno ya no existe).
        marcas_del_dia: Horas ``"HH:MM:SS"`` de sus marcas ese día
            calendario (para medir el trabajo en días libres o feriados).
        salida_del_dia_anterior: Hora de salida del día anterior; en un
            turno nocturno esa marca cae en este día y no es trabajo extra.
    """
    a = asistencia
    if a.estado in DESCANSO:
        horas = sorted(
            h[:8] for h in marcas_del_dia if h[:8] != (salida_del_dia_anterior or "")[:8]
        )
        if len(horas) < 2:
            return TiempoDia()
        return TiempoDia(en_descanso=(_segundos(horas[-1]) - _segundos(horas[0])) // 60)
    if horario is None or a.estado in JUSTIFICADOS:
        return TiempoDia()
    if a.estado == E.AUSENTE.value:
        return TiempoDia(programado=horario.minutos, trabajado=0)
    if a.estado not in TRABAJADOS:
        return TiempoDia()

    entrada_oficial = _segundos(horario.entrada)
    salida_oficial = _segundos(horario.salida)
    if horario.cruza_medianoche or salida_oficial <= entrada_oficial:
        salida_oficial += _DIA_SEG
    entrada = _segundos(a.hora_entrada_real) if a.hora_entrada_real else None
    salida = _segundos(a.hora_salida_real) if a.hora_salida_real else None

    if entrada is not None and salida is not None:
        if salida < entrada:
            salida += _DIA_SEG
        return TiempoDia(
            programado=horario.minutos,
            trabajado=(salida - entrada) // 60,
            tarde=a.minutos_tarde,
            salida_temprana=a.minutos_salida_temprana,
            extra=max(0, salida - salida_oficial) // 60,
        )
    # Falta una marca (el día todavía no cerró): el atraso ya cuenta.
    tarde = max(0, entrada - entrada_oficial) // 60 if entrada is not None else 0
    return TiempoDia(tarde=tarde)


def sumar_dias(dias: Iterable[Tuple[TiempoDia, Optional[str]]]) -> TiempoEmpleado:
    """Acumula ``(TiempoDia, hora de entrada real)`` de un empleado."""
    acc = _Acumulador()
    claves = ("programado", "trabajado", "tarde", "salida_temprana", "extra", "en_descanso")
    conteos = ("dias_con_atraso", "dias_con_salida_temprana", "dias_con_extra", "dias_descanso")
    for clave in (*claves, *conteos):
        acc.total[clave] = 0
    for dia, entrada in dias:
        if dia.trabajado is not None and dia.programado:
            acc.total["programado"] += dia.programado
            acc.total["trabajado"] += dia.trabajado
        for clave in ("tarde", "salida_temprana", "extra", "en_descanso"):
            acc.total[clave] += getattr(dia, clave)
        acc.total["dias_con_atraso"] += dia.tarde > 0
        acc.total["dias_con_salida_temprana"] += dia.salida_temprana > 0
        acc.total["dias_con_extra"] += dia.extra > 0
        acc.total["dias_descanso"] += dia.en_descanso > 0
        if entrada and dia.programado:
            acc.entradas.append(_segundos(entrada))
    promedio = None
    if acc.entradas:
        seg = sum(acc.entradas) // len(acc.entradas)
        promedio = f"{seg // 3600:02d}:{seg % 3600 // 60:02d}"
    t = acc.total
    return TiempoEmpleado(
        programado=t["programado"],
        trabajado=t["trabajado"],
        tarde=t["tarde"],
        salida_temprana=t["salida_temprana"],
        extra=t["extra"],
        en_descanso=t["en_descanso"],
        dias_con_atraso=t["dias_con_atraso"],
        dias_con_salida_temprana=t["dias_con_salida_temprana"],
        dias_con_extra=t["dias_con_extra"],
        dias_descanso_trabajados=t["dias_descanso"],
        entrada_promedio=promedio,
    )


def texto_duracion(minutos: int, con_signo: bool = False) -> str:
    """``125`` → ``"2:05"``; con signo: ``"+2:05"``, ``"−0:45"``, ``"0:00"``."""
    signo = ""
    if con_signo and minutos > 0:
        signo = "+"
    elif minutos < 0:
        signo = "−"
    m = abs(minutos)
    return f"{signo}{m // 60}:{m % 60:02d}"


def _segundos(hora: str) -> int:
    partes = [int(p) for p in hora.strip()[:8].split(":")]
    while len(partes) < 3:
        partes.append(0)
    return partes[0] * 3600 + partes[1] * 60 + partes[2]
