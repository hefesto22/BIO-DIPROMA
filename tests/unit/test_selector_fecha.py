"""Lectura y escritura de fechas del selector (dd/mm/aaaa), sin abrir ventanas."""

from __future__ import annotations

from datetime import date

import pytest

from ui.components.piezas import SelectorFecha


@pytest.mark.parametrize(
    "texto, esperado",
    [
        ("03/09/2026", date(2026, 9, 3)),
        ("3/9/2026", date(2026, 9, 3)),
        ("3-9-26", date(2026, 9, 3)),
        (" 31/12/2025 ", date(2025, 12, 31)),
        ("09/25/2026", date(2026, 9, 25)),  # mes > 12: se entiende día/mes al revés
    ],
)
def test_lee_fechas_escritas_a_mano(texto: str, esperado: date) -> None:
    assert SelectorFecha.parse_date(texto) == esperado


@pytest.mark.parametrize("texto", ["", "abc", "12/2026", "31/02/2026", "00/01/2026"])
def test_rechaza_fechas_incompletas_o_imposibles(texto: str) -> None:
    with pytest.raises((ValueError, IndexError)):
        SelectorFecha.parse_date(texto)


def test_escribe_con_ceros_y_anio_completo() -> None:
    assert SelectorFecha.format_date(date(2026, 1, 5)) == "05/01/2026"
    assert SelectorFecha.parse_date(SelectorFecha.format_date(date(2026, 1, 5))) == date(2026, 1, 5)
