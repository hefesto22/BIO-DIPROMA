"""Tests del RegistroRawRepositorySQLite.

Cubre:
    - ``create_bulk`` con secuencia vacía → no-op (retorna 0).
    - ``create_bulk`` con registros únicos → retorna len(registros).
    - ``create_bulk`` con duplicados (UNIQUE dispositivo+zkteco_user+ts)
      → dedupe silencioso vía ``INSERT OR IGNORE``, rowcount correcto.
    - Listados: por sincronización (orden timestamp ASC) y por zkteco_user
      + rango (filtrado por dispositivo y rango inclusive).
    - ``count_by_sincronizacion`` con casos borde.
    - FK: dispositivo_id / sincronizacion_id inexistentes → IntegrityError.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Tuple

import pytest

from core.models.dispositivo import Dispositivo
from core.models.registro_raw import RegistroRaw, TipoMarcada
from core.models.sincronizacion import Sincronizacion
from core.repositories.dispositivo_repository_sqlite import (
    DispositivoRepositorySQLite,
)
from core.repositories.registro_raw_repository_sqlite import (
    RegistroRawRepositorySQLite,
)
from core.repositories.sincronizacion_repository_sqlite import (
    SincronizacionRepositorySQLite,
)
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "infrastructure" / "database" / "migrations"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def setup(
    tmp_path: Path,
) -> Tuple[RegistroRawRepositorySQLite, int, int, Database]:
    """DB + 1 dispositivo + 1 sincronización seedeados.

    Returns:
        (repo, dispositivo_id, sincronizacion_id, db).
    """
    db = Database(tmp_path / "test_registro_raw.db")
    MigrationsRunner(db, MIGRATIONS_DIR).run()
    disp = DispositivoRepositorySQLite(db).create(
        Dispositivo(id=None, nombre="Reloj Test", ip="10.0.0.1", puerto=4370)
    )
    assert disp.id is not None
    sync = SincronizacionRepositorySQLite(db).create(
        Sincronizacion(
            id=None,
            dispositivo_id=disp.id,
            iniciada_por_user_id=None,
            inicio="2026-04-24T10:00:00",
            rango_desde="2026-04-01",
            rango_hasta="2026-04-30",
        )
    )
    assert sync.id is not None
    return RegistroRawRepositorySQLite(db), disp.id, sync.id, db


def _nuevo_registro(
    dispositivo_id: int,
    sincronizacion_id: int,
    zkteco_user_id: int = 101,
    timestamp: str = "2026-04-15T08:00:00",
    tipo: str = TipoMarcada.CHECK_IN.value,
) -> RegistroRaw:
    return RegistroRaw(
        id=None,
        dispositivo_id=dispositivo_id,
        sincronizacion_id=sincronizacion_id,
        zkteco_user_id=zkteco_user_id,
        timestamp=timestamp,
        tipo_marcada=tipo,
    )


# ── Tests: create_bulk ────────────────────────────────────────────────────────


def test_create_bulk_vacio_retorna_cero(
    setup: Tuple[RegistroRawRepositorySQLite, int, int, Database],
) -> None:
    """Secuencia vacía es no-op: no abre transacción, retorna 0."""
    repo, _, _, _ = setup
    assert repo.create_bulk([]) == 0


def test_create_bulk_inserta_y_cuenta_todos(
    setup: Tuple[RegistroRawRepositorySQLite, int, int, Database],
) -> None:
    repo, disp_id, sync_id, _ = setup
    registros = [
        _nuevo_registro(disp_id, sync_id, 101, "2026-04-15T08:00:00"),
        _nuevo_registro(disp_id, sync_id, 101, "2026-04-15T17:00:00"),
        _nuevo_registro(disp_id, sync_id, 102, "2026-04-15T08:05:00"),
    ]
    insertados = repo.create_bulk(registros)
    assert insertados == 3
    assert repo.count_by_sincronizacion(sync_id) == 3


def test_create_bulk_dedupe_silencioso(
    setup: Tuple[RegistroRawRepositorySQLite, int, int, Database],
) -> None:
    """UNIQUE (dispositivo, zkteco_user, timestamp) + INSERT OR IGNORE:
    re-ejecutar con los mismos tuples retorna 0 insertados."""
    repo, disp_id, sync_id, _ = setup
    base = [
        _nuevo_registro(disp_id, sync_id, 101, "2026-04-15T08:00:00"),
        _nuevo_registro(disp_id, sync_id, 101, "2026-04-15T17:00:00"),
    ]
    assert repo.create_bulk(base) == 2
    # Segundo pase con los mismos tuples: todos ignorados.
    assert repo.create_bulk(base) == 0
    # La tabla sigue con sólo 2 filas.
    assert repo.count_by_sincronizacion(sync_id) == 2


def test_create_bulk_dedupe_parcial_cuenta_solo_nuevos(
    setup: Tuple[RegistroRawRepositorySQLite, int, int, Database],
) -> None:
    """Mezcla de existentes + nuevos: rowcount = sólo los nuevos."""
    repo, disp_id, sync_id, _ = setup
    existente = _nuevo_registro(disp_id, sync_id, 101, "2026-04-15T08:00:00")
    assert repo.create_bulk([existente]) == 1

    nuevo = _nuevo_registro(disp_id, sync_id, 101, "2026-04-15T17:00:00")
    insertados = repo.create_bulk([existente, nuevo])
    assert insertados == 1  # sólo el nuevo; el existente se ignoró


def test_create_bulk_con_dispositivo_inexistente_falla(
    setup: Tuple[RegistroRawRepositorySQLite, int, int, Database],
) -> None:
    repo, _, sync_id, _ = setup
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_bulk([_nuevo_registro(9999, sync_id)])


def test_create_bulk_con_sincronizacion_inexistente_falla(
    setup: Tuple[RegistroRawRepositorySQLite, int, int, Database],
) -> None:
    repo, disp_id, _, _ = setup
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_bulk([_nuevo_registro(disp_id, 9999)])


def test_create_bulk_tipo_marcada_invalido_es_silencioso(
    setup: Tuple[RegistroRawRepositorySQLite, int, int, Database],
) -> None:
    """``INSERT OR IGNORE`` suprime TODAS las violaciones (UNIQUE y CHECK).

    Una ``tipo_marcada`` fuera del catálogo NO levanta — se descarta
    silenciosamente, igual que un duplicado. Esto es consistente con la
    política defensiva del repo: preferimos perder la fila a reventar una
    sync entera. El mapeo a ``UNKNOWN`` para códigos desconocidos sucede
    aguas arriba (en el adapter pyzk) — si algo igual llega con un valor
    inválido, lo absorbemos.
    """
    repo, disp_id, sync_id, _ = setup
    valido = _nuevo_registro(
        disp_id, sync_id, 101, "2026-04-15T08:00:00", tipo=TipoMarcada.CHECK_IN.value
    )
    invalido = _nuevo_registro(disp_id, sync_id, 102, "2026-04-15T08:05:00", tipo="INVALIDO")
    insertados = repo.create_bulk([valido, invalido])
    assert insertados == 1  # sólo el válido
    assert repo.count_by_sincronizacion(sync_id) == 1


# ── Tests: get_by_id / list_by_sincronizacion ─────────────────────────────────


def test_get_by_id_inexistente_devuelve_none(
    setup: Tuple[RegistroRawRepositorySQLite, int, int, Database],
) -> None:
    repo, _, _, _ = setup
    assert repo.get_by_id(9999) is None


def test_get_by_id_recupera_campos(
    setup: Tuple[RegistroRawRepositorySQLite, int, int, Database],
) -> None:
    repo, disp_id, sync_id, _ = setup
    repo.create_bulk([_nuevo_registro(disp_id, sync_id, 101, "2026-04-15T08:00:00")])
    lista = repo.list_by_sincronizacion(sync_id)
    assert len(lista) == 1 and lista[0].id is not None
    leido = repo.get_by_id(lista[0].id)
    assert leido is not None
    assert leido.zkteco_user_id == 101
    assert leido.tipo_marcada == TipoMarcada.CHECK_IN.value
    assert leido.timestamp == "2026-04-15T08:00:00"


def test_list_by_sincronizacion_vacio(
    setup: Tuple[RegistroRawRepositorySQLite, int, int, Database],
) -> None:
    repo, _, sync_id, _ = setup
    assert repo.list_by_sincronizacion(sync_id) == []


def test_list_by_sincronizacion_ordena_por_timestamp_asc(
    setup: Tuple[RegistroRawRepositorySQLite, int, int, Database],
) -> None:
    repo, disp_id, sync_id, _ = setup
    # Se insertan fuera de orden cronológico a propósito.
    repo.create_bulk(
        [
            _nuevo_registro(disp_id, sync_id, 101, "2026-04-15T17:00:00"),
            _nuevo_registro(disp_id, sync_id, 101, "2026-04-15T08:00:00"),
            _nuevo_registro(disp_id, sync_id, 102, "2026-04-15T12:00:00"),
        ]
    )
    resultado = repo.list_by_sincronizacion(sync_id)
    assert [r.timestamp for r in resultado] == [
        "2026-04-15T08:00:00",
        "2026-04-15T12:00:00",
        "2026-04-15T17:00:00",
    ]


# ── Tests: list_by_zkteco_user_y_rango ────────────────────────────────────────


def test_list_by_zkteco_user_y_rango_filtra_usuario(
    setup: Tuple[RegistroRawRepositorySQLite, int, int, Database],
) -> None:
    repo, disp_id, sync_id, _ = setup
    repo.create_bulk(
        [
            _nuevo_registro(disp_id, sync_id, 101, "2026-04-15T08:00:00"),
            _nuevo_registro(disp_id, sync_id, 102, "2026-04-15T08:05:00"),
            _nuevo_registro(disp_id, sync_id, 101, "2026-04-15T17:00:00"),
        ]
    )
    del_101 = repo.list_by_zkteco_user_y_rango(
        101, disp_id, "2026-04-15T00:00:00", "2026-04-15T23:59:59"
    )
    assert len(del_101) == 2
    assert all(r.zkteco_user_id == 101 for r in del_101)


def test_list_by_zkteco_user_y_rango_filtra_dispositivo(
    setup: Tuple[RegistroRawRepositorySQLite, int, int, Database],
) -> None:
    """Dos relojes con el mismo ``zkteco_user_id`` no se mezclan."""
    repo, disp_id_1, sync_id, db = setup
    # Crear segundo dispositivo y segunda sincronización sobre él.
    disp_2 = DispositivoRepositorySQLite(db).create(
        Dispositivo(id=None, nombre="Otro", ip="10.0.0.2", puerto=4370)
    )
    assert disp_2.id is not None
    sync_2 = SincronizacionRepositorySQLite(db).create(
        Sincronizacion(
            id=None,
            dispositivo_id=disp_2.id,
            iniciada_por_user_id=None,
            inicio="2026-04-24T11:00:00",
            rango_desde="2026-04-01",
            rango_hasta="2026-04-30",
        )
    )
    assert sync_2.id is not None

    repo.create_bulk(
        [
            _nuevo_registro(disp_id_1, sync_id, 101, "2026-04-15T08:00:00"),
            _nuevo_registro(disp_2.id, sync_2.id, 101, "2026-04-15T08:00:01"),
        ]
    )
    solo_disp_1 = repo.list_by_zkteco_user_y_rango(
        101, disp_id_1, "2026-04-15T00:00:00", "2026-04-15T23:59:59"
    )
    assert len(solo_disp_1) == 1
    assert solo_disp_1[0].dispositivo_id == disp_id_1


def test_list_by_zkteco_user_y_rango_inclusivo_en_ambos_extremos(
    setup: Tuple[RegistroRawRepositorySQLite, int, int, Database],
) -> None:
    repo, disp_id, sync_id, _ = setup
    repo.create_bulk(
        [
            _nuevo_registro(disp_id, sync_id, 101, "2026-04-14T23:59:59"),
            _nuevo_registro(disp_id, sync_id, 101, "2026-04-15T00:00:00"),
            _nuevo_registro(disp_id, sync_id, 101, "2026-04-15T12:00:00"),
            _nuevo_registro(disp_id, sync_id, 101, "2026-04-15T23:59:59"),
            _nuevo_registro(disp_id, sync_id, 101, "2026-04-16T00:00:00"),
        ]
    )
    resultado = repo.list_by_zkteco_user_y_rango(
        101, disp_id, "2026-04-15T00:00:00", "2026-04-15T23:59:59"
    )
    ts = [r.timestamp for r in resultado]
    assert ts == [
        "2026-04-15T00:00:00",
        "2026-04-15T12:00:00",
        "2026-04-15T23:59:59",
    ]


def test_list_by_zkteco_user_y_rango_fuera_devuelve_vacio(
    setup: Tuple[RegistroRawRepositorySQLite, int, int, Database],
) -> None:
    repo, disp_id, sync_id, _ = setup
    repo.create_bulk([_nuevo_registro(disp_id, sync_id, 101, "2026-04-15T08:00:00")])
    assert (
        repo.list_by_zkteco_user_y_rango(101, disp_id, "2026-05-01T00:00:00", "2026-05-31T23:59:59")
        == []
    )


# ── Tests: list_by_rango ──────────────────────────────────────────────────────


def test_list_by_rango_incluye_todos_los_dispositivos(
    setup: Tuple[RegistroRawRepositorySQLite, int, int, Database],
) -> None:
    """``list_by_rango`` no filtra por dispositivo — un empleado puede marcar
    en dos relojes diferentes y ambas marcadas deben aparecer."""
    repo, disp_id_1, sync_id, db = setup
    disp_2 = DispositivoRepositorySQLite(db).create(
        Dispositivo(id=None, nombre="Otro", ip="10.0.0.2", puerto=4370)
    )
    assert disp_2.id is not None
    sync_2 = SincronizacionRepositorySQLite(db).create(
        Sincronizacion(
            id=None,
            dispositivo_id=disp_2.id,
            iniciada_por_user_id=None,
            inicio="2026-04-24T11:00:00",
            rango_desde="2026-04-01",
            rango_hasta="2026-04-30",
        )
    )
    assert sync_2.id is not None

    repo.create_bulk(
        [
            _nuevo_registro(disp_id_1, sync_id, 101, "2026-04-15T08:00:00"),
            _nuevo_registro(disp_2.id, sync_2.id, 101, "2026-04-15T17:00:00"),
        ]
    )
    resultado = repo.list_by_rango("2026-04-15T00:00:00", "2026-04-15T23:59:59")
    assert len(resultado) == 2
    dispositivos = {r.dispositivo_id for r in resultado}
    assert dispositivos == {disp_id_1, disp_2.id}


def test_list_by_rango_ordena_por_zkteco_user_y_timestamp(
    setup: Tuple[RegistroRawRepositorySQLite, int, int, Database],
) -> None:
    """Orden: zkteco_user_id ASC, timestamp ASC, id ASC (para groupby lineal)."""
    repo, disp_id, sync_id, _ = setup
    repo.create_bulk(
        [
            _nuevo_registro(disp_id, sync_id, 102, "2026-04-15T08:00:00"),
            _nuevo_registro(disp_id, sync_id, 101, "2026-04-15T17:00:00"),
            _nuevo_registro(disp_id, sync_id, 101, "2026-04-15T08:00:00"),
            _nuevo_registro(disp_id, sync_id, 102, "2026-04-15T17:00:00"),
        ]
    )
    resultado = repo.list_by_rango("2026-04-15T00:00:00", "2026-04-15T23:59:59")
    pares = [(r.zkteco_user_id, r.timestamp) for r in resultado]
    assert pares == [
        (101, "2026-04-15T08:00:00"),
        (101, "2026-04-15T17:00:00"),
        (102, "2026-04-15T08:00:00"),
        (102, "2026-04-15T17:00:00"),
    ]


def test_list_by_rango_inclusivo_en_ambos_extremos(
    setup: Tuple[RegistroRawRepositorySQLite, int, int, Database],
) -> None:
    repo, disp_id, sync_id, _ = setup
    repo.create_bulk(
        [
            _nuevo_registro(disp_id, sync_id, 101, "2026-04-14T23:59:59"),
            _nuevo_registro(disp_id, sync_id, 101, "2026-04-15T00:00:00"),
            _nuevo_registro(disp_id, sync_id, 101, "2026-04-15T23:59:59"),
            _nuevo_registro(disp_id, sync_id, 101, "2026-04-16T00:00:00"),
        ]
    )
    resultado = repo.list_by_rango("2026-04-15T00:00:00", "2026-04-15T23:59:59")
    assert [r.timestamp for r in resultado] == [
        "2026-04-15T00:00:00",
        "2026-04-15T23:59:59",
    ]


def test_list_by_rango_vacio_cuando_no_hay_registros(
    setup: Tuple[RegistroRawRepositorySQLite, int, int, Database],
) -> None:
    repo, _, _, _ = setup
    assert repo.list_by_rango("2026-04-01T00:00:00", "2026-04-30T23:59:59") == []


# ── Tests: count_by_sincronizacion ────────────────────────────────────────────


def test_count_by_sincronizacion_vacio(
    setup: Tuple[RegistroRawRepositorySQLite, int, int, Database],
) -> None:
    repo, _, sync_id, _ = setup
    assert repo.count_by_sincronizacion(sync_id) == 0


def test_count_by_sincronizacion_id_inexistente_es_cero(
    setup: Tuple[RegistroRawRepositorySQLite, int, int, Database],
) -> None:
    """COUNT sobre filtro que no matchea nada devuelve 0, no levanta."""
    repo, _, _, _ = setup
    assert repo.count_by_sincronizacion(9999) == 0
