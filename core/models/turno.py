"""Modelo de dominio: Turno.

Incluye las constantes de bitmask de días de la semana que usan el
servicio y la UI para manipular ``Turno.dias_semana``.

Convención del bitmask (7 bits):

    bit 6  bit 5  bit 4  bit 3  bit 2  bit 1  bit 0
    LUN    MAR    MIE    JUE    VIE    SAB    DOM

Ejemplos:

    Lunes a viernes:  0b1111100 = 124 = DIAS_LABORALES
    Sábado y domingo: 0b0000011 = 3   = DIAS_FIN_DE_SEMANA
    Todos los días:   0b1111111 = 127 = DIAS_TODA_LA_SEMANA

El orden elegido (Lunes en el bit más alto) espeja la convención visual
de un calendario latinoamericano — la semana arranca en lunes. La
función ``dia_aplica`` abstrae al llamador de cualquier decisión de bit
order: recibe un día según ``datetime.weekday()`` (0 = Lunes, 6 = Domingo)
y devuelve si el turno aplica ese día.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Final, List, Optional

# ── Constantes de días de la semana (bitmask) ─────────────────────────────

LUNES: Final[int] = 1 << 6
MARTES: Final[int] = 1 << 5
MIERCOLES: Final[int] = 1 << 4
JUEVES: Final[int] = 1 << 3
VIERNES: Final[int] = 1 << 2
SABADO: Final[int] = 1 << 1
DOMINGO: Final[int] = 1 << 0

DIAS_LABORALES: Final[int] = LUNES | MARTES | MIERCOLES | JUEVES | VIERNES
DIAS_FIN_DE_SEMANA: Final[int] = SABADO | DOMINGO
DIAS_TODA_LA_SEMANA: Final[int] = DIAS_LABORALES | DIAS_FIN_DE_SEMANA

# Tabla de traducción weekday() → bit. Índice = datetime.weekday() (0..6).
# Conviene tenerla precomputada para evitar branches en el hot path.
_BIT_POR_WEEKDAY: Final[tuple[int, int, int, int, int, int, int]] = (
    LUNES,
    MARTES,
    MIERCOLES,
    JUEVES,
    VIERNES,
    SABADO,
    DOMINGO,
)


# Nombre de cada día por bit (para mensajes de error legibles).
NOMBRE_DIA_POR_BIT: Final[dict[int, str]] = {
    LUNES: "lunes",
    MARTES: "martes",
    MIERCOLES: "miércoles",
    JUEVES: "jueves",
    VIERNES: "viernes",
    SABADO: "sábado",
    DOMINGO: "domingo",
}


def nombres_dias(bitmask: int) -> List[str]:
    """Nombres (en minúscula) de los días encendidos, de lunes a domingo."""
    return [nombre for bit, nombre in NOMBRE_DIA_POR_BIT.items() if bitmask & bit]


def dia_aplica(dias_semana: int, weekday: int) -> bool:
    """Indica si el turno aplica en el día de la semana dado.

    Args:
        dias_semana: Bitmask tal como está persistido en ``Turno.dias_semana``.
        weekday: Día de la semana según ``datetime.date.weekday()`` —
            entero 0..6 donde 0 = Lunes y 6 = Domingo.

    Returns:
        ``True`` si el turno incluye ese día, ``False`` en caso contrario.

    Raises:
        ValueError: Si ``weekday`` está fuera del rango 0..6.
    """
    if not 0 <= weekday <= 6:
        raise ValueError(f"weekday debe estar en 0..6; se recibió {weekday}")
    return bool(dias_semana & _BIT_POR_WEEKDAY[weekday])


@dataclass(frozen=True)
class HorarioTurno:
    """Horario distinto dentro de un mismo turno para ciertos días.

    Ejemplo: el turno "Administrativo" trabaja de lunes a viernes de
    09:00 a 17:00 (horario principal, en las columnas de ``turnos``) y el
    sábado de 09:00 a 12:00 (un ``HorarioTurno`` en ``turno_horarios``).

    Los días de todos los horarios de un turno son disjuntos: cada día de
    la semana tiene a lo sumo un horario. Las tolerancias de entrada y
    salida son las del turno.
    """

    dias_semana: int
    hora_entrada: str
    hora_salida: str
    minutos_descanso: int = 0
    cruza_medianoche: bool = False


@dataclass
class Turno:
    """Turno de trabajo — horario principal más horarios alternos por día.

    Diseño (Decisión 4 del PRD): un turno describe cuándo debe estar
    trabajando un empleado. Se compara contra las marcas del reloj ZKTeco
    para calcular atrasos, salidas tempranas y horas extra (Fase 3).

    ``Turno`` NO es ``frozen`` para permitir la edición inline desde la UI.

    Atributos:
        id: PK en la tabla ``turnos``. ``None`` si aún no fue persistido.
        nombre: Nombre único visible al usuario. Ejemplos:
            "Administrativo 8-5", "Vigilancia Nocturna".
        hora_entrada: Formato "HH:MM" 24h. Ejemplo: ``"08:00"``.
        hora_salida: Formato "HH:MM" 24h. Si es menor que ``hora_entrada``
            el turno cruza medianoche (vigilancia nocturna).
        minutos_descanso: Minutos de almuerzo/pausa internos al bloque. Se
            restan al calcular horas trabajadas. Default 0.
        dias_semana: Bitmask 7-bit de días aplicables (ver constantes).
            Default: lunes a viernes (``DIAS_LABORALES``).
        cruza_medianoche: Flag derivado de ``hora_salida < hora_entrada``.
            Se persiste explícitamente para que los queries no tengan que
            recalcularlo; el servicio lo setea al crear/editar.
        minutos_tolerancia_entrada: Minutos de gracia a partir de la hora
            oficial de entrada antes de marcar TARDE. Default 10. Ejemplo:
            entrada 08:00 + tolerancia 10 → llegadas hasta 08:10 son PRESENTE.
            Colocado por turno (no global) porque distintos perfiles requieren
            distinta estrictez (ejecutivos vs. vigilancia).
        minutos_tolerancia_salida: Minutos de gracia ANTES de la hora oficial
            de salida (salida temprana permitida). Default 0. Ejemplo: salida
            17:00 + tolerancia 5 → salidas desde 16:55 son PRESENTE.
        is_active: ``False`` archiva el turno — deja de aparecer en
            dropdowns de asignación pero preserva las asignaciones
            históricas en ``empleado_turnos``.
        horarios_extra: Horarios alternos para otros días (ver
            ``HorarioTurno``). Vacío en un turno de horario único. Las
            horas de arriba son el horario principal y aplican solo a
            ``dias_semana``.
    """

    id: Optional[int]
    nombre: str
    hora_entrada: str
    hora_salida: str
    minutos_descanso: int = 0
    dias_semana: int = DIAS_LABORALES
    cruza_medianoche: bool = False
    minutos_tolerancia_entrada: int = 10
    minutos_tolerancia_salida: int = 0
    is_active: bool = True
    horarios_extra: List[HorarioTurno] = field(default_factory=list)

    @property
    def dias_totales(self) -> int:
        """Bitmask de TODOS los días que cubre el turno (todos sus horarios)."""
        total = self.dias_semana
        for horario in self.horarios_extra:
            total |= horario.dias_semana
        return total

    def aplica_en(self, weekday: int) -> bool:
        """``True`` si algún horario del turno cubre ese día (0 = lunes)."""
        return dia_aplica(self.dias_totales, weekday)

    def bloques(self) -> List[HorarioTurno]:
        """Horario principal seguido de los alternos, como ``HorarioTurno``."""
        principal = HorarioTurno(
            dias_semana=self.dias_semana,
            hora_entrada=self.hora_entrada,
            hora_salida=self.hora_salida,
            minutos_descanso=self.minutos_descanso,
            cruza_medianoche=self.cruza_medianoche,
        )
        return [principal, *self.horarios_extra]

    def horario_para(self, weekday: int) -> Optional["Turno"]:
        """El turno "visto" desde un día: sus horas son las de ese día.

        Devuelve ``self`` si el día es del horario principal, una copia con
        las horas del horario alterno si es de uno de ellos, o ``None`` si
        el turno no cubre ese día. La consolidación trabaja siempre con un
        turno de horario único, así que esta proyección es la que le pasa.
        """
        if dia_aplica(self.dias_semana, weekday):
            return self
        for horario in self.horarios_extra:
            if dia_aplica(horario.dias_semana, weekday):
                return replace(
                    self,
                    hora_entrada=horario.hora_entrada,
                    hora_salida=horario.hora_salida,
                    minutos_descanso=horario.minutos_descanso,
                    dias_semana=horario.dias_semana,
                    cruza_medianoche=horario.cruza_medianoche,
                    horarios_extra=[],
                )
        return None
