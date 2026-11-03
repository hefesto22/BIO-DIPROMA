"""Modelo de dominio: RegistroRaw.

Marcada individual entregada por un dispositivo ZKTeco, sin procesar.
"Raw" porque guarda lo que vino del device tal cual — el ``zkteco_user_id``
es el ID del reloj (no el ``empleado_id`` de nuestra BD), y el servicio
de consolidación (Fase 3.3) es quien resuelve el mapeo contra la tabla
``empleados`` al construir las filas consolidadas de ``asistencias``.

Diseño:
    - Una fila por marcada. No hay "pareja entrada/salida" aquí; eso es
      responsabilidad de la consolidación.
    - UNIQUE (dispositivo_id, zkteco_user_id, timestamp): dedupe defensivo
      — si alguien re-sincroniza el mismo rango, no se duplican filas.
    - ``tipo_marcada`` refleja lo que pyzk expone en ``Attendance.status``.
      Mapeamos los 4 tipos conocidos y un ``UNKNOWN`` de fallback para
      firmwares/modelos que devuelvan códigos no documentados — evita
      perder el registro.

Incluye el enum ``TipoMarcada`` (catálogo cerrado) acoplado a este modelo.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final, FrozenSet, Optional


class TipoMarcada(str, Enum):
    """Catálogo cerrado del tipo de marcada reportado por el ZKTeco.

    Se hereda de ``str`` para comparación natural contra el TEXT persistido
    en BD:

        >>> TipoMarcada.CHECK_IN == "CHECK_IN"
        True

    Valores según ``pyzk`` (campo ``Attendance.status``):
        CHECK_IN (0): Entrada normal del turno.
        CHECK_OUT (1): Salida normal del turno.
        OVERTIME_IN (4): Entrada extra (horas extras, doble turno).
        OVERTIME_OUT (5): Salida extra.
        UNKNOWN: Código no reconocido — se preserva el registro para no
            perder datos; el servicio de consolidación decide cómo tratarlo
            (típicamente como marcada simple entrada/salida según posición).
    """

    CHECK_IN = "CHECK_IN"
    CHECK_OUT = "CHECK_OUT"
    OVERTIME_IN = "OVERTIME_IN"
    OVERTIME_OUT = "OVERTIME_OUT"
    UNKNOWN = "UNKNOWN"


ALL_TIPOS_MARCADA: Final[FrozenSet[str]] = frozenset(tipo.value for tipo in TipoMarcada)


@dataclass
class RegistroRaw:
    """Marcada individual entregada por un dispositivo ZKTeco.

    ``RegistroRaw`` NO es ``frozen`` pero en la práctica es inmutable una
    vez insertado — no hay flujos de edición. El no-frozen facilita
    instanciación parcial en tests.

    Atributos:
        id: PK en la tabla ``registros_raw``. ``None`` si aún no fue
            persistido.
        dispositivo_id: FK al reloj que reportó la marcada.
        sincronizacion_id: FK a la sync que trajo este registro. Permite
            rastrear el origen y hacer rollback (``ON DELETE CASCADE``).
        zkteco_user_id: ID del usuario EN EL RELOJ (no el ``empleado_id``
            de nuestra BD). El mapeo a ``empleados.zkteco_id`` lo hace el
            servicio de consolidación.
        timestamp: Momento de la marcada, ISO-8601 con segundos
            (``YYYY-MM-DDTHH:MM:SS``). El reloj no reporta timezone; se
            asume que el reloj y el servidor viven en la misma TZ.
        tipo_marcada: Uno de ``TipoMarcada.*.value``. Default UNKNOWN cuando
            el código del reloj no se reconoce.
    """

    id: Optional[int]
    dispositivo_id: int
    sincronizacion_id: int
    zkteco_user_id: int
    timestamp: str
    tipo_marcada: str = TipoMarcada.UNKNOWN.value
