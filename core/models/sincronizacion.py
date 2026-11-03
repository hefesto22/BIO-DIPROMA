"""Modelo de dominio: Sincronizacion.

Cabecera de cada pull desde un dispositivo ZKTeco. Registra quién disparó
la sync, cuándo, qué rango de fechas pidió al reloj, y el resultado. Los
``registros_raw`` referencian su ``sincronizacion_id`` para que se pueda
rastrear de qué pull específico vino cada marcada — útil para auditoría
y para deshacer un sync fallido (``ON DELETE CASCADE`` en los registros).

Incluye el enum ``EstadoSincronizacion`` (catálogo cerrado de estados)
porque está acoplado semánticamente al ciclo de vida de la sync: no hay
otro caso de uso para él fuera de ``Sincronizacion``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final, FrozenSet, Optional


class EstadoSincronizacion(str, Enum):
    """Catálogo cerrado de estados de una sincronización.

    Se hereda de ``str`` para comparación natural contra el TEXT persistido
    en BD:

        >>> EstadoSincronizacion.OK == "OK"
        True

    Estados:
        EN_CURSO: sync iniciada pero aún no finalizada (``fin IS NULL``).
            Si el proceso muere a mitad, queda "colgada" — el servicio de
            Fase 3.3 debe poder marcar como FALLIDA las huérfanas antiguas.
        OK: sync completada sin errores, ``fin`` y ``registros_recibidos``
            poblados.
        FALLIDA: la sync terminó en error (red caída, device sin respuesta,
            SQL error al persistir). ``error_mensaje`` describe el fallo.
    """

    EN_CURSO = "EN_CURSO"
    OK = "OK"
    FALLIDA = "FALLIDA"


ALL_ESTADOS_SINCRONIZACION: Final[FrozenSet[str]] = frozenset(
    estado.value for estado in EstadoSincronizacion
)


@dataclass
class Sincronizacion:
    """Cabecera de un pull de datos desde un dispositivo ZKTeco.

    ``Sincronizacion`` NO es ``frozen`` — el servicio la muta para registrar
    el ``fin``, ``estado`` final y ``registros_recibidos`` al cerrar el pull.

    Invariantes (validadas por BD y/o servicio):
        - ``rango_hasta >= rango_desde`` (CHECK en BD).
        - ``estado in ALL_ESTADOS_SINCRONIZACION`` (CHECK en BD).
        - Si ``estado == OK`` → ``fin`` está poblado.
        - Si ``estado == FALLIDA`` → ``error_mensaje`` está poblado.

    Atributos:
        id: PK en la tabla ``sincronizaciones``. ``None`` si aún no fue
            persistido.
        dispositivo_id: FK al reloj desde el que se hizo el pull.
        iniciada_por_user_id: FK al usuario que disparó la sync. ``None``
            admitido porque el FK usa ``ON DELETE SET NULL`` — si el usuario
            se elimina, la historia de su sync no se pierde.
        inicio: Timestamp ISO-8601 UTC del arranque de la sync.
        fin: Timestamp ISO-8601 UTC del cierre. ``None`` mientras EN_CURSO.
        rango_desde: Fecha ISO ``YYYY-MM-DD`` — límite inferior del rango
            solicitado al device.
        rango_hasta: Fecha ISO ``YYYY-MM-DD`` — límite superior (inclusivo).
        registros_recibidos: Conteo de registros que el device entregó para
            este pull. Default 0 mientras EN_CURSO.
        estado: Uno de ``EstadoSincronizacion.*.value``. Default EN_CURSO.
        error_mensaje: Descripción libre del fallo si ``estado == FALLIDA``.
    """

    id: Optional[int]
    dispositivo_id: int
    iniciada_por_user_id: Optional[int]
    inicio: str
    rango_desde: str
    rango_hasta: str
    fin: Optional[str] = None
    registros_recibidos: int = 0
    estado: str = EstadoSincronizacion.EN_CURSO.value
    error_mensaje: Optional[str] = None
