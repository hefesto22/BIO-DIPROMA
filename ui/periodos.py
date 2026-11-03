"""Períodos rápidos para los filtros de fecha (sin Tk, testeable).

Las quincenas siguen la costumbre de planilla en Honduras: del 1 al 15 y
del 16 al último día del mes.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from typing import Dict, Final, Optional, Tuple

HOY: Final[str] = "Hoy"
AYER: Final[str] = "Ayer"
ESTA_SEMANA: Final[str] = "Esta semana"
ESTE_MES: Final[str] = "Este mes"
MES_PASADO: Final[str] = "Mes pasado"
QUINCENA_1: Final[str] = "1.ª quincena"
QUINCENA_2: Final[str] = "2.ª quincena"
# "Todo": desde el primer día con datos hasta hoy. Depende de la BD, así que
# ``rango`` no lo resuelve: lo arma la vista con ``rango_todo``.
TODO: Final[str] = "Todo"


def rango(nombre: str, hoy: date) -> Tuple[date, date]:
    """``(desde, hasta)`` del período con ese nombre, relativo a ``hoy``.

    Los períodos del mes en curso terminan hoy (no tiene sentido pedir
    días que todavía no pasan), salvo las quincenas, que son fijas.

    Raises:
        KeyError: nombre desconocido.
    """
    inicio_mes = hoy.replace(day=1)
    fin_mes = hoy.replace(day=monthrange(hoy.year, hoy.month)[1])
    fin_mes_pasado = inicio_mes - timedelta(days=1)
    tabla: Dict[str, Tuple[date, date]] = {
        HOY: (hoy, hoy),
        AYER: (hoy - timedelta(days=1), hoy - timedelta(days=1)),
        ESTA_SEMANA: (hoy - timedelta(days=hoy.weekday()), hoy),
        ESTE_MES: (inicio_mes, hoy),
        MES_PASADO: (fin_mes_pasado.replace(day=1), fin_mes_pasado),
        QUINCENA_1: (inicio_mes, hoy.replace(day=15)),
        QUINCENA_2: (hoy.replace(day=16), fin_mes),
    }
    return tabla[nombre]


def rango_todo(primera: Optional[date], hoy: date) -> Tuple[date, date]:
    """Del primer día con datos hasta hoy (solo hoy si todavía no hay datos)."""
    return (min(primera, hoy) if primera is not None else hoy), hoy


def descripcion(desde: date, hasta: date) -> str:
    """Texto corto del rango: ``"31/10/2026"``, ``"26/10 al 31/10/2026"``."""
    if desde == hasta:
        return desde.strftime("%d/%m/%Y")
    if desde.year == hasta.year:
        return f"{desde:%d/%m} al {hasta:%d/%m/%Y}"
    return f"{desde:%d/%m/%Y} al {hasta:%d/%m/%Y}"
