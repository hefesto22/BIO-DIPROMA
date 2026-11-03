"""Tests del DescargaReporteRepositorySQLite.

Cubre:
    - ``insert`` persiste y devuelve la fila con ``id`` asignado.
    - ``get_by_id`` resuelve la fila o ``None``.
    - ``list_recientes`` ordena por fecha desc.
    - ``list_by_user`` filtra y ordena por fecha desc.
    - ``user_id`` SET NULL al borrar el usuario (FK declarada en la
      migración).
"""

from __future__ import annotations

from pathlib import Path
from typing import Tuple

import pytest

from core.models.descarga_reporte import DescargaReporte, TipoReporte
from core.repositories.descarga_reporte_repository_sqlite import (
    DescargaReporteRepositorySQLite,
)
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "infrastructure" / "database" / "migrations"


def _seed_user(db: Database, user_id: int, username: str) -> None:
    """Inserta un usuario dummy ligado al rol ADMIN (id=2 según seed)."""
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO usuarios "
            "(id, username, password_hash, full_name, role_id, created_at, updated_at) "
            "VALUES (?, ?, 'hash', 'Dummy', 2, "
            "'2024-01-01T00:00:00', '2024-01-01T00:00:00')",
            (user_id, username),
        )


@pytest.fixture
def setup(tmp_path: Path) -> Tuple[DescargaReporteRepositorySQLite, Database]:
    db = Database(tmp_path / "test_descarga.db")
    MigrationsRunner(db, MIGRATIONS_DIR).run()
    _seed_user(db, 1, "alice")
    _seed_user(db, 2, "bob")
    return DescargaReporteRepositorySQLite(db), db


def _factory(
    user_id: int = 1,
    fecha: str = "2026-04-24T10:00:00",
    rango_desde: str = "2026-04-01",
    rango_hasta: str = "2026-04-30",
    filas: int = 100,
    ruta: str = "/tmp/reporte.xlsx",
) -> DescargaReporte:
    return DescargaReporte(
        id=None,
        user_id=user_id,
        fecha_hora_utc=fecha,
        tipo_reporte=TipoReporte.ASISTENCIA.value,
        rango_desde=rango_desde,
        rango_hasta=rango_hasta,
        ruta_archivo=ruta,
        filas_exportadas=filas,
    )


# ── insert + get_by_id ────────────────────────────────────────────────────────


def test_insert_devuelve_id_asignado(
    setup: Tuple[DescargaReporteRepositorySQLite, Database],
) -> None:
    repo, _ = setup
    creado = repo.insert(_factory())
    assert creado.id is not None and creado.id > 0
    assert creado.tipo_reporte == "ASISTENCIA"


def test_get_by_id_resuelve_fila(
    setup: Tuple[DescargaReporteRepositorySQLite, Database],
) -> None:
    repo, _ = setup
    creado = repo.insert(_factory())
    assert creado.id is not None
    leido = repo.get_by_id(creado.id)
    assert leido is not None
    assert leido.user_id == 1
    assert leido.filas_exportadas == 100


def test_get_by_id_inexistente_devuelve_none(
    setup: Tuple[DescargaReporteRepositorySQLite, Database],
) -> None:
    repo, _ = setup
    assert repo.get_by_id(99999) is None


# ── list_recientes ────────────────────────────────────────────────────────────


def test_list_recientes_ordena_por_fecha_desc(
    setup: Tuple[DescargaReporteRepositorySQLite, Database],
) -> None:
    repo, _ = setup
    repo.insert(_factory(fecha="2026-04-01T08:00:00"))
    repo.insert(_factory(fecha="2026-04-22T12:00:00"))
    repo.insert(_factory(fecha="2026-04-10T09:00:00"))
    listadas = repo.list_recientes()
    assert [d.fecha_hora_utc for d in listadas] == [
        "2026-04-22T12:00:00",
        "2026-04-10T09:00:00",
        "2026-04-01T08:00:00",
    ]


def test_list_recientes_respeta_limit(
    setup: Tuple[DescargaReporteRepositorySQLite, Database],
) -> None:
    repo, _ = setup
    for i in range(5):
        repo.insert(_factory(fecha=f"2026-04-{i + 1:02d}T08:00:00"))
    listadas = repo.list_recientes(limit=2)
    assert len(listadas) == 2


# ── list_by_user ──────────────────────────────────────────────────────────────


def test_list_by_user_filtra_y_ordena(
    setup: Tuple[DescargaReporteRepositorySQLite, Database],
) -> None:
    repo, _ = setup
    repo.insert(_factory(user_id=1, fecha="2026-04-01T08:00:00"))
    repo.insert(_factory(user_id=2, fecha="2026-04-02T08:00:00"))
    repo.insert(_factory(user_id=1, fecha="2026-04-03T08:00:00"))
    listadas = repo.list_by_user(user_id=1)
    assert len(listadas) == 2
    assert all(d.user_id == 1 for d in listadas)
    assert listadas[0].fecha_hora_utc == "2026-04-03T08:00:00"


# ── FK ON DELETE SET NULL ─────────────────────────────────────────────────────


def test_borrar_usuario_setea_user_id_null(
    setup: Tuple[DescargaReporteRepositorySQLite, Database],
) -> None:
    """ON DELETE SET NULL preserva el historial cuando se borra un usuario."""
    repo, db = setup
    creado = repo.insert(_factory(user_id=2))
    assert creado.id is not None
    with db.transaction() as conn:
        conn.execute("DELETE FROM usuarios WHERE id = 2")
    leido = repo.get_by_id(creado.id)
    assert leido is not None
    assert leido.user_id is None
