"""Tests del SincronizacionRepositorySQLite.

Cubre:
    - Creación con estado default ``EN_CURSO``.
    - Ciclo de vida: ``marcar_ok`` y ``marcar_fallida``.
    - ``list_en_curso`` para recovery de huérfanas.
    - Ordenamiento canónico (más reciente primero).
    - CHECK ``rango_hasta >= rango_desde`` del schema.
    - FK a ``dispositivos`` (RESTRICT).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Tuple

import pytest

from core.models.dispositivo import Dispositivo
from core.models.sincronizacion import EstadoSincronizacion, Sincronizacion
from core.repositories.dispositivo_repository_sqlite import (
    DispositivoRepositorySQLite,
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
def setup(tmp_path: Path) -> Tuple[SincronizacionRepositorySQLite, int, Database]:
    """DB + 1 dispositivo seedeado. Retorna (repo, dispositivo_id, db)."""
    db = Database(tmp_path / "test_sincronizacion.db")
    MigrationsRunner(db, MIGRATIONS_DIR).run()
    disp = DispositivoRepositorySQLite(db).create(
        Dispositivo(id=None, nombre="Reloj Test", ip="10.0.0.1", puerto=4370)
    )
    assert disp.id is not None
    return SincronizacionRepositorySQLite(db), disp.id, db


def _nueva_sync(dispositivo_id: int, inicio: str = "2026-04-24T10:00:00") -> Sincronizacion:
    return Sincronizacion(
        id=None,
        dispositivo_id=dispositivo_id,
        iniciada_por_user_id=None,
        inicio=inicio,
        rango_desde="2026-04-01",
        rango_hasta="2026-04-30",
    )


# ── Tests: create ─────────────────────────────────────────────────────────────


def test_create_asigna_id_y_defaults(
    setup: Tuple[SincronizacionRepositorySQLite, int, Database],
) -> None:
    repo, disp_id, _ = setup
    creada = repo.create(_nueva_sync(disp_id))
    assert creada.id is not None and creada.id > 0
    assert creada.estado == EstadoSincronizacion.EN_CURSO.value
    assert creada.fin is None
    assert creada.registros_recibidos == 0
    assert creada.error_mensaje is None


def test_create_con_dispositivo_inexistente_falla(
    setup: Tuple[SincronizacionRepositorySQLite, int, Database],
) -> None:
    repo, _, _ = setup
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(_nueva_sync(dispositivo_id=9999))


def test_create_rango_invertido_falla_por_check(
    setup: Tuple[SincronizacionRepositorySQLite, int, Database],
) -> None:
    """CHECK(rango_hasta >= rango_desde) rechaza rangos invertidos."""
    repo, disp_id, _ = setup
    sync = _nueva_sync(disp_id)
    sync.rango_desde = "2026-04-30"
    sync.rango_hasta = "2026-04-01"
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(sync)


def test_create_rango_igual_es_valido(
    setup: Tuple[SincronizacionRepositorySQLite, int, Database],
) -> None:
    """``rango_desde == rango_hasta`` es sync de un solo día."""
    repo, disp_id, _ = setup
    sync = _nueva_sync(disp_id)
    sync.rango_desde = "2026-04-15"
    sync.rango_hasta = "2026-04-15"
    creada = repo.create(sync)
    assert creada.id is not None


# ── Tests: get / list ─────────────────────────────────────────────────────────


def test_get_by_id_inexistente_devuelve_none(
    setup: Tuple[SincronizacionRepositorySQLite, int, Database],
) -> None:
    repo, _, _ = setup
    assert repo.get_by_id(9999) is None


def test_list_recientes_ordena_descendente(
    setup: Tuple[SincronizacionRepositorySQLite, int, Database],
) -> None:
    repo, disp_id, _ = setup
    repo.create(_nueva_sync(disp_id, inicio="2026-04-20T10:00:00"))
    repo.create(_nueva_sync(disp_id, inicio="2026-04-24T10:00:00"))
    repo.create(_nueva_sync(disp_id, inicio="2026-04-22T10:00:00"))
    recientes = repo.list_recientes()
    assert [s.inicio for s in recientes] == [
        "2026-04-24T10:00:00",
        "2026-04-22T10:00:00",
        "2026-04-20T10:00:00",
    ]


def test_list_recientes_respeta_limit(
    setup: Tuple[SincronizacionRepositorySQLite, int, Database],
) -> None:
    repo, disp_id, _ = setup
    for i in range(5):
        repo.create(_nueva_sync(disp_id, inicio=f"2026-04-2{i}T10:00:00"))
    assert len(repo.list_recientes(limit=3)) == 3


def test_list_by_dispositivo_filtra(
    setup: Tuple[SincronizacionRepositorySQLite, int, Database],
) -> None:
    # Crear un segundo dispositivo (reutilizando la DB del fixture) para
    # verificar que el filtro por ``dispositivo_id`` funciona.
    repo, disp_id_1, db = setup
    disp_2 = DispositivoRepositorySQLite(db).create(
        Dispositivo(id=None, nombre="Otro", ip="10.0.0.2", puerto=4370)
    )
    assert disp_2.id is not None

    repo.create(_nueva_sync(disp_id_1))
    repo.create(_nueva_sync(disp_2.id))
    repo.create(_nueva_sync(disp_id_1))

    del_1 = repo.list_by_dispositivo(disp_id_1)
    del_2 = repo.list_by_dispositivo(disp_2.id)
    assert len(del_1) == 2
    assert len(del_2) == 1


def test_list_en_curso_solo_sin_cerrar(
    setup: Tuple[SincronizacionRepositorySQLite, int, Database],
) -> None:
    repo, disp_id, _ = setup
    s_ok = repo.create(_nueva_sync(disp_id, inicio="2026-04-20T10:00:00"))
    s_fail = repo.create(_nueva_sync(disp_id, inicio="2026-04-21T10:00:00"))
    s_en_curso = repo.create(_nueva_sync(disp_id, inicio="2026-04-22T10:00:00"))
    assert s_ok.id is not None and s_fail.id is not None

    repo.marcar_ok(s_ok.id, fin="2026-04-20T10:05:00", registros_recibidos=100)
    repo.marcar_fallida(s_fail.id, fin="2026-04-21T10:02:00", error_mensaje="red caída")

    en_curso = repo.list_en_curso()
    assert len(en_curso) == 1
    assert en_curso[0].id == s_en_curso.id


# ── Tests: marcar_ok ──────────────────────────────────────────────────────────


def test_marcar_ok_actualiza_estado_fin_y_contador(
    setup: Tuple[SincronizacionRepositorySQLite, int, Database],
) -> None:
    repo, disp_id, _ = setup
    s = repo.create(_nueva_sync(disp_id))
    assert s.id is not None

    repo.marcar_ok(s.id, fin="2026-04-24T10:05:00", registros_recibidos=42)
    leida = repo.get_by_id(s.id)
    assert leida is not None
    assert leida.estado == EstadoSincronizacion.OK.value
    assert leida.fin == "2026-04-24T10:05:00"
    assert leida.registros_recibidos == 42
    assert leida.error_mensaje is None


def test_marcar_ok_con_0_registros_es_valido(
    setup: Tuple[SincronizacionRepositorySQLite, int, Database],
) -> None:
    """Sync exitosa puede devolver 0 registros (rango sin marcadas)."""
    repo, disp_id, _ = setup
    s = repo.create(_nueva_sync(disp_id))
    assert s.id is not None
    repo.marcar_ok(s.id, fin="2026-04-24T10:05:00", registros_recibidos=0)
    leida = repo.get_by_id(s.id)
    assert leida is not None and leida.registros_recibidos == 0


def test_marcar_ok_negativo_lanza_value_error(
    setup: Tuple[SincronizacionRepositorySQLite, int, Database],
) -> None:
    repo, disp_id, _ = setup
    s = repo.create(_nueva_sync(disp_id))
    assert s.id is not None
    with pytest.raises(ValueError, match=">= 0"):
        repo.marcar_ok(s.id, fin="2026-04-24T10:05:00", registros_recibidos=-1)


def test_marcar_ok_id_inexistente_lanza_value_error(
    setup: Tuple[SincronizacionRepositorySQLite, int, Database],
) -> None:
    repo, _, _ = setup
    with pytest.raises(ValueError, match="No existe"):
        repo.marcar_ok(9999, fin="2026-04-24T10:05:00", registros_recibidos=0)


# ── Tests: marcar_fallida ─────────────────────────────────────────────────────


def test_marcar_fallida_actualiza_estado_y_error(
    setup: Tuple[SincronizacionRepositorySQLite, int, Database],
) -> None:
    repo, disp_id, _ = setup
    s = repo.create(_nueva_sync(disp_id))
    assert s.id is not None

    repo.marcar_fallida(s.id, fin="2026-04-24T10:02:00", error_mensaje="timeout al conectar")
    leida = repo.get_by_id(s.id)
    assert leida is not None
    assert leida.estado == EstadoSincronizacion.FALLIDA.value
    assert leida.fin == "2026-04-24T10:02:00"
    assert leida.error_mensaje == "timeout al conectar"


def test_marcar_fallida_preserva_registros_recibidos(
    setup: Tuple[SincronizacionRepositorySQLite, int, Database],
) -> None:
    """Si el fallo ocurre a mitad del pull, registros_recibidos puede
    haber quedado > 0 — el verbo fallida NO lo resetea."""
    repo, disp_id, _ = setup
    s = repo.create(_nueva_sync(disp_id))
    assert s.id is not None
    # Simular que un paso intermedio actualizó el contador directamente.
    # En producción esto lo hace el servicio antes de invocar marcar_fallida.
    # Acá lo probamos vía create con el valor preestablecido:
    # (re-creamos para simplicidad).
    s2 = Sincronizacion(
        id=None,
        dispositivo_id=disp_id,
        iniciada_por_user_id=None,
        inicio="2026-04-24T11:00:00",
        rango_desde="2026-04-01",
        rango_hasta="2026-04-30",
        registros_recibidos=15,
    )
    creada = repo.create(s2)
    assert creada.id is not None

    repo.marcar_fallida(creada.id, fin="2026-04-24T11:02:00", error_mensaje="X")
    leida = repo.get_by_id(creada.id)
    assert leida is not None
    assert leida.registros_recibidos == 15  # no se tocó


def test_marcar_fallida_id_inexistente_lanza_value_error(
    setup: Tuple[SincronizacionRepositorySQLite, int, Database],
) -> None:
    repo, _, _ = setup
    with pytest.raises(ValueError, match="No existe"):
        repo.marcar_fallida(9999, fin="X", error_mensaje="Y")
