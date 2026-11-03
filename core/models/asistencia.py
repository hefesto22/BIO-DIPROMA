"""Modelo de dominio: Asistencia.

Fila consolidada de asistencia diaria por empleado — el output del servicio
de consolidación (Fase 3.3) que toma registros_raw + turno vigente +
feriados y emite UN ``Asistencia`` por empleado por fecha.

Convención de fecha para turnos que cruzan medianoche:
    Si el turno arranca el día D y termina el día D+1, la asistencia se
    registra con ``fecha = D`` (el día de entrada). Esto espeja la
    intuición del empleado ("mi turno del lunes") y facilita el cálculo
    de horarios semanales.

Diseño:
    - UNIQUE (empleado_id, fecha): una sola fila por empleado por día.
    - Re-consolidación idempotente: si el servicio re-procesa el mismo día,
      debe hacer UPSERT (preservando observaciones manuales si existen).
    - Cuando el estado es ``SIN_TURNO``, ``AUSENTE`` o ``FERIADO``, los
      campos ``hora_entrada_real``/``hora_salida_real`` pueden ser ``None``.
    - ``turno_id_aplicado`` es ``None`` para días SIN_TURNO y FERIADO; en
      AUSENTE es el turno que habría aplicado (la expectativa incumplida).

Incluye el enum ``EstadoAsistencia`` acoplado a este modelo.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final, FrozenSet, Optional


class EstadoAsistencia(str, Enum):
    """Catálogo cerrado de estados diarios de asistencia.

    Se hereda de ``str`` para comparación natural contra el TEXT persistido
    en BD:

        >>> EstadoAsistencia.PRESENTE == "PRESENTE"
        True

    Estados:
        PRESENTE: Entrada dentro de tolerancia + salida dentro de tolerancia.
        TARDE: Entrada fuera de tolerancia; salida ok.
        SALIDA_TEMPRANA: Entrada ok; salida anticipada fuera de tolerancia.
        TARDE_Y_SALIDA_TEMPRANA: Ambos problemas el mismo día.
        AUSENTE: El empleado debía trabajar ese día (turno aplica) y no hay
            marcadas.
        SIN_TURNO: El día no aplica al turno vigente del empleado (p.ej.
            domingo en turno lun-sáb). No es AUSENTE.
        FERIADO: Fecha registrada en ``feriados``. El empleado no debía
            trabajar aunque su turno aplicara ese día de la semana.
        INCOMPLETO: Solo 1 marcada (sola entrada o sola salida). Marca para
            revisión manual — el operador puede anotar en observaciones y
            reclasificar manualmente en Fase 4.
        VACACIONES / DIA_LIBRE / PERMISO / INCAPACIDAD: El día está dentro de
            una ``Ausencia`` registrada de ese tipo. No es AUSENTE. Pierden
            frente a FERIADO y SIN_TURNO (esos días no consumen vacaciones).
    """

    PRESENTE = "PRESENTE"
    TARDE = "TARDE"
    SALIDA_TEMPRANA = "SALIDA_TEMPRANA"
    TARDE_Y_SALIDA_TEMPRANA = "TARDE_Y_SALIDA_TEMPRANA"
    AUSENTE = "AUSENTE"
    SIN_TURNO = "SIN_TURNO"
    FERIADO = "FERIADO"
    INCOMPLETO = "INCOMPLETO"
    VACACIONES = "VACACIONES"
    DIA_LIBRE = "DIA_LIBRE"
    PERMISO = "PERMISO"
    INCAPACIDAD = "INCAPACIDAD"


ALL_ESTADOS_ASISTENCIA: Final[FrozenSet[str]] = frozenset(
    estado.value for estado in EstadoAsistencia
)

# Nota que deja la consolidación cuando el empleado marcó la entrada pero no
# la salida y el día ya cerró (se toma la hora de salida del turno). Es la
# única observación automática: las demás las escribe el operador.
OBSERVACION_SALIDA_ASUMIDA: Final[str] = "Salida asumida según turno: sin segunda marcada."


@dataclass
class Asistencia:
    """Fila consolidada de asistencia diaria por empleado.

    ``Asistencia`` NO es ``frozen`` — la UI de Fase 4 permitirá editar
    observaciones y reclasificar estado manualmente (justificaciones).

    Atributos:
        id: PK en la tabla ``asistencias``. ``None`` si aún no fue persistida.
        empleado_id: FK al empleado.
        fecha: Fecha ISO ``YYYY-MM-DD``. Para turnos que cruzan medianoche,
            es la fecha de entrada (ver convención en el docstring del
            módulo).
        turno_id_aplicado: FK al turno vigente ese día. ``None`` para
            estados SIN_TURNO y FERIADO. En AUSENTE es el turno incumplido.
        hora_entrada_real: Hora efectiva de entrada, ``HH:MM:SS``. ``None``
            para AUSENTE, SIN_TURNO, FERIADO, y para INCOMPLETO si solo hay
            salida.
        hora_salida_real: Hora efectiva de salida, ``HH:MM:SS``. Análogo a
            entrada.
        estado: Uno de ``EstadoAsistencia.*.value``.
        minutos_tarde: Minutos de diferencia entre hora oficial y real de
            entrada, si la entrada fue tardía. 0 en cualquier otro caso.
        minutos_salida_temprana: Minutos de diferencia si la salida fue
            anticipada. 0 en cualquier otro caso.
        observaciones: Texto libre para anotaciones manuales (Fase 4).
        consolidada_en: Timestamp ISO-8601 UTC del último pase de
            consolidación. Permite saber qué filas quedaron obsoletas
            después de una modificación de turnos.
    """

    id: Optional[int]
    empleado_id: int
    fecha: str
    estado: str
    turno_id_aplicado: Optional[int] = None
    hora_entrada_real: Optional[str] = None
    hora_salida_real: Optional[str] = None
    minutos_tarde: int = 0
    minutos_salida_temprana: int = 0
    observaciones: Optional[str] = None
    consolidada_en: Optional[str] = None
