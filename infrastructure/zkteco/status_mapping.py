"""Mapeo entero → ``TipoMarcada`` para el campo ``Attendance.status`` de pyzk.

pyzk expone en ``Attendance.status`` un entero que indica el tipo de
punch: 0=entrada, 1=salida, 4=entrada extra, 5=salida extra. Algunos
firmwares devuelven códigos no documentados (2, 3, 255, ...); en esos
casos NO descartamos el registro — lo etiquetamos ``UNKNOWN`` para que
la consolidación pueda procesarlo heurísticamente y el operador lo revise.

Este módulo es la ÚNICA fuente de verdad del mapeo. Si pyzk cambia de
convención en el futuro, acá se ajusta y nada más.
"""

from __future__ import annotations

from typing import Final, Mapping

from core.models.registro_raw import TipoMarcada

# Mapeo canónico firmware → enum. Los valores son los documentados por
# pyzk (https://github.com/fananimi/pyzk) y coinciden con los modelos
# K-series / SpeedFace / ProFace que usa la municipalidad.
_STATUS_TO_TIPO: Final[Mapping[int, TipoMarcada]] = {
    0: TipoMarcada.CHECK_IN,
    1: TipoMarcada.CHECK_OUT,
    4: TipoMarcada.OVERTIME_IN,
    5: TipoMarcada.OVERTIME_OUT,
}


def map_status_to_tipo_marcada(status: int) -> TipoMarcada:
    """Traduce el entero ``Attendance.status`` al enum de dominio.

    Args:
        status: Entero devuelto por pyzk en ``Attendance.status``.

    Returns:
        El ``TipoMarcada`` correspondiente, o ``TipoMarcada.UNKNOWN`` si
        el código no está en el catálogo. Nunca lanza — los callers
        pueden confiar en un retorno válido.
    """
    return _STATUS_TO_TIPO.get(status, TipoMarcada.UNKNOWN)
