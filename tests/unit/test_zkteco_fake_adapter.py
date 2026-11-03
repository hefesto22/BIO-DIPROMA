"""Tests de ``FakeZKTecoAdapter``.

El fake replica el contrato de ``IZKTecoAdapter`` sin tocar red. Estos
tests lo ejercitan como caja negra (vía el método público
``pull_attendance``) porque es el mismo camino que usará el servicio de
consolidación en Fase 3.3 — si el fake se comporta como el real desde
afuera, los tests de consolidación serán fiables.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from core.models.dispositivo import Dispositivo
from core.models.registro_raw import TipoMarcada
from infrastructure.zkteco.exceptions import (
    ZKAdapterError,
    ZKConnectionError,
    ZKProtocolError,
    ZKTimeoutError,
)
from infrastructure.zkteco.fake_adapter import FakeZKTecoAdapter


def _dispositivo(id_: int = 1) -> Dispositivo:
    """Factory de dispositivo persistido para los tests."""
    return Dispositivo(id=id_, nombre=f"Reloj {id_}", ip="192.168.1.100")


# ── Casos base ────────────────────────────────────────────────────────


def test_pull_sin_preload_retorna_lista_vacia() -> None:
    """Dispositivo sin marcadas cargadas devuelve [] — no None ni error."""
    adapter = FakeZKTecoAdapter()
    resultado = adapter.pull_attendance(
        _dispositivo(),
        sincronizacion_id=10,
        desde=date(2026, 4, 1),
        hasta=date(2026, 4, 30),
    )
    assert resultado == []


def test_marcada_preloaded_se_devuelve_con_mapeo_correcto() -> None:
    """Una marcada con status=0 debe regresar como CHECK_IN."""
    adapter = FakeZKTecoAdapter()
    adapter.preload_attendance(
        dispositivo_id=1,
        zkteco_user_id=100,
        timestamp=datetime(2026, 4, 15, 8, 0, 0),
        status=0,
    )
    resultado = adapter.pull_attendance(
        _dispositivo(),
        sincronizacion_id=10,
        desde=date(2026, 4, 1),
        hasta=date(2026, 4, 30),
    )
    assert len(resultado) == 1
    r = resultado[0]
    assert r.dispositivo_id == 1
    assert r.sincronizacion_id == 10
    assert r.zkteco_user_id == 100
    assert r.timestamp == "2026-04-15T08:00:00"
    assert r.tipo_marcada == TipoMarcada.CHECK_IN.value
    assert r.id is None


def test_status_desconocido_mapea_a_unknown_sin_descartar_el_registro() -> None:
    """Status=99 (firmware raro) debe marcarse UNKNOWN, no perderse."""
    adapter = FakeZKTecoAdapter()
    adapter.preload_attendance(
        dispositivo_id=1,
        zkteco_user_id=100,
        timestamp=datetime(2026, 4, 15, 8, 0, 0),
        status=99,
    )
    resultado = adapter.pull_attendance(
        _dispositivo(),
        sincronizacion_id=10,
        desde=date(2026, 4, 15),
        hasta=date(2026, 4, 15),
    )
    assert len(resultado) == 1
    assert resultado[0].tipo_marcada == TipoMarcada.UNKNOWN.value


# ── Filtrado ──────────────────────────────────────────────────────────


def test_filtra_por_dispositivo_id() -> None:
    """Marcadas de otro reloj NO deben aparecer en el pull."""
    adapter = FakeZKTecoAdapter()
    adapter.preload_attendance(
        dispositivo_id=1,
        zkteco_user_id=100,
        timestamp=datetime(2026, 4, 15, 8, 0, 0),
        status=0,
    )
    adapter.preload_attendance(
        dispositivo_id=2,  # otro reloj
        zkteco_user_id=200,
        timestamp=datetime(2026, 4, 15, 8, 0, 0),
        status=0,
    )
    resultado = adapter.pull_attendance(
        _dispositivo(1),
        sincronizacion_id=10,
        desde=date(2026, 4, 1),
        hasta=date(2026, 4, 30),
    )
    assert len(resultado) == 1
    assert resultado[0].dispositivo_id == 1
    assert resultado[0].zkteco_user_id == 100


def test_filtra_por_rango_de_fecha_inclusive() -> None:
    """Rango inclusive en ambos extremos; descarta las fuera de rango."""
    adapter = FakeZKTecoAdapter()
    # Fuera (antes)
    adapter.preload_attendance(
        dispositivo_id=1,
        zkteco_user_id=100,
        timestamp=datetime(2026, 3, 31, 23, 59, 59),
        status=0,
    )
    # Dentro (borde inferior)
    adapter.preload_attendance(
        dispositivo_id=1,
        zkteco_user_id=100,
        timestamp=datetime(2026, 4, 1, 0, 0, 0),
        status=0,
    )
    # Dentro (medio)
    adapter.preload_attendance(
        dispositivo_id=1,
        zkteco_user_id=100,
        timestamp=datetime(2026, 4, 15, 12, 0, 0),
        status=1,
    )
    # Dentro (borde superior)
    adapter.preload_attendance(
        dispositivo_id=1,
        zkteco_user_id=100,
        timestamp=datetime(2026, 4, 30, 23, 59, 59),
        status=0,
    )
    # Fuera (después)
    adapter.preload_attendance(
        dispositivo_id=1,
        zkteco_user_id=100,
        timestamp=datetime(2026, 5, 1, 0, 0, 0),
        status=0,
    )
    resultado = adapter.pull_attendance(
        _dispositivo(),
        sincronizacion_id=10,
        desde=date(2026, 4, 1),
        hasta=date(2026, 4, 30),
    )
    assert len(resultado) == 3


# ── Errores simulados ─────────────────────────────────────────────────


def test_set_next_error_lanza_y_se_auto_resetea() -> None:
    """El error se consume en el próximo pull; la siguiente llamada
    retorna normal (lista vacía)."""
    adapter = FakeZKTecoAdapter()
    adapter.set_next_error(ZKConnectionError("red caída"))

    with pytest.raises(ZKConnectionError):
        adapter.pull_attendance(
            _dispositivo(),
            sincronizacion_id=10,
            desde=date(2026, 4, 1),
            hasta=date(2026, 4, 30),
        )

    # Siguiente llamada: ya no lanza.
    resultado = adapter.pull_attendance(
        _dispositivo(),
        sincronizacion_id=11,
        desde=date(2026, 4, 1),
        hasta=date(2026, 4, 30),
    )
    assert resultado == []


@pytest.mark.parametrize(
    "error",
    [
        ZKConnectionError("no hay red"),
        ZKTimeoutError("reloj sordo"),
        ZKProtocolError("firmware raro"),
    ],
)
def test_cualquier_subclase_de_zkadaptererror_puede_ser_simulada(
    error: ZKAdapterError,
) -> None:
    """El fake acepta toda la jerarquía, no solo la clase base."""
    adapter = FakeZKTecoAdapter()
    adapter.set_next_error(error)

    with pytest.raises(type(error)):
        adapter.pull_attendance(
            _dispositivo(),
            sincronizacion_id=10,
            desde=date(2026, 4, 1),
            hasta=date(2026, 4, 30),
        )


# ── Validaciones del contrato ─────────────────────────────────────────


def test_dispositivo_sin_id_es_rechazado() -> None:
    """Contrato: el dispositivo debe estar persistido antes del pull."""
    adapter = FakeZKTecoAdapter()
    disp_sin_id = Dispositivo(id=None, nombre="nuevo", ip="192.168.1.100")

    with pytest.raises(ValueError, match="persistido"):
        adapter.pull_attendance(
            disp_sin_id,
            sincronizacion_id=10,
            desde=date(2026, 4, 1),
            hasta=date(2026, 4, 30),
        )


def test_rango_invertido_es_rechazado() -> None:
    """``desde > hasta`` es un error del caller — no silenciar."""
    adapter = FakeZKTecoAdapter()

    with pytest.raises(ValueError, match="invertido"):
        adapter.pull_attendance(
            _dispositivo(),
            sincronizacion_id=10,
            desde=date(2026, 4, 30),
            hasta=date(2026, 4, 1),
        )


# ── Auditoría (pulls) ─────────────────────────────────────────────────


def test_pulls_registra_cada_llamada_en_orden() -> None:
    """El historial permite al test verificar qué pidió el servicio."""
    adapter = FakeZKTecoAdapter()
    adapter.pull_attendance(
        _dispositivo(1),
        sincronizacion_id=10,
        desde=date(2026, 4, 1),
        hasta=date(2026, 4, 15),
    )
    adapter.pull_attendance(
        _dispositivo(2),
        sincronizacion_id=11,
        desde=date(2026, 4, 16),
        hasta=date(2026, 4, 30),
    )

    pulls = adapter.pulls
    assert len(pulls) == 2
    assert pulls[0].dispositivo_id == 1
    assert pulls[0].sincronizacion_id == 10
    assert pulls[0].desde == date(2026, 4, 1)
    assert pulls[0].hasta == date(2026, 4, 15)
    assert pulls[1].dispositivo_id == 2
    assert pulls[1].sincronizacion_id == 11


def test_pulls_tambien_registra_llamadas_fallidas() -> None:
    """Aunque el pull lance, la llamada ya quedó registrada — útil para
    tests del servicio que cuenten reintentos."""
    adapter = FakeZKTecoAdapter()
    adapter.set_next_error(ZKTimeoutError("timeout"))

    with pytest.raises(ZKTimeoutError):
        adapter.pull_attendance(
            _dispositivo(),
            sincronizacion_id=10,
            desde=date(2026, 4, 1),
            hasta=date(2026, 4, 30),
        )

    assert len(adapter.pulls) == 1


def test_clear_resetea_estado_completo() -> None:
    """``clear`` debe vaciar buffer, error pendiente e historial."""
    adapter = FakeZKTecoAdapter()
    adapter.preload_attendance(
        dispositivo_id=1,
        zkteco_user_id=100,
        timestamp=datetime(2026, 4, 15, 8, 0, 0),
        status=0,
    )
    adapter.set_next_error(ZKConnectionError("fallo"))

    adapter.clear()

    # Post-clear: pulls vacío.
    assert adapter.pulls == []

    # Post-clear: no hay marcadas cargadas ni error pendiente.
    resultado = adapter.pull_attendance(
        _dispositivo(),
        sincronizacion_id=99,
        desde=date(2026, 4, 1),
        hasta=date(2026, 4, 30),
    )
    assert resultado == []
    # La llamada post-clear sí queda registrada (el reset solo limpió el estado previo).
    assert len(adapter.pulls) == 1
