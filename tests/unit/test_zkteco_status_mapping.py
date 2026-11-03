"""Tests de ``map_status_to_tipo_marcada``.

Verifica el mapeo de los 4 códigos documentados + fallback UNKNOWN para
cualquier código no reconocido. Este mapeo es crítico porque es la única
fuente de verdad entre el firmware del reloj y el dominio — un bug aquí
se propagaría a toda la consolidación de Fase 3.3.
"""

from __future__ import annotations

import pytest

from core.models.registro_raw import TipoMarcada
from infrastructure.zkteco.status_mapping import map_status_to_tipo_marcada


@pytest.mark.parametrize(
    "status, esperado",
    [
        (0, TipoMarcada.CHECK_IN),
        (1, TipoMarcada.CHECK_OUT),
        (4, TipoMarcada.OVERTIME_IN),
        (5, TipoMarcada.OVERTIME_OUT),
    ],
)
def test_codigos_documentados_mapean_al_tipo_correcto(status: int, esperado: TipoMarcada) -> None:
    """Los 4 códigos que pyzk documenta deben traducir al enum correspondiente."""
    assert map_status_to_tipo_marcada(status) == esperado


@pytest.mark.parametrize("codigo_desconocido", [2, 3, 6, 7, 42, 99, 255, -1])
def test_codigos_no_documentados_caen_a_unknown(codigo_desconocido: int) -> None:
    """Cualquier código fuera del catálogo debe mapearse a UNKNOWN — nunca
    descartamos el registro, lo marcamos para revisión manual."""
    assert map_status_to_tipo_marcada(codigo_desconocido) == TipoMarcada.UNKNOWN


def test_retorna_tipomarcada_no_string() -> None:
    """El mapping retorna el enum, no el ``.value``. El caller decide
    cuándo extraer el string para persistir."""
    resultado = map_status_to_tipo_marcada(0)
    assert isinstance(resultado, TipoMarcada)
    # Y hereda de str, así que la comparación string sigue funcionando:
    assert resultado == "CHECK_IN"
