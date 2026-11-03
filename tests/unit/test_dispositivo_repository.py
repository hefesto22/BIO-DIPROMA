"""Tests del DispositivoRepositorySQLite.

Cubre CRUD básico, búsquedas por id/nombre/ip+puerto, listados
(todos vs. activos), update, archive/unarchive, y los dos UNIQUE
que protegen la tabla (``nombre`` y ``(ip, puerto)``).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.models.dispositivo import Dispositivo
from core.repositories.dispositivo_repository_sqlite import (
    DispositivoRepositorySQLite,
)
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "infrastructure" / "database" / "migrations"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def repo(tmp_path: Path) -> DispositivoRepositorySQLite:
    db = Database(tmp_path / "test_dispositivo.db")
    MigrationsRunner(db, MIGRATIONS_DIR).run()
    return DispositivoRepositorySQLite(db)


def _nuevo(
    nombre: str = "Reloj Principal",
    ip: str = "192.168.1.100",
    puerto: int = 4370,
) -> Dispositivo:
    """Construye un Dispositivo listo para insertar."""
    return Dispositivo(id=None, nombre=nombre, ip=ip, puerto=puerto)


# ── Tests: create + read ──────────────────────────────────────────────────────


def test_create_asigna_id_y_persiste_todos_los_campos(
    repo: DispositivoRepositorySQLite,
) -> None:
    creado = repo.create(_nuevo("Entrada", "10.0.0.5", 4370))
    assert creado.id is not None and creado.id > 0
    assert creado.nombre == "Entrada"
    assert creado.ip == "10.0.0.5"
    assert creado.puerto == 4370
    assert creado.is_active is True


def test_create_puerto_default_es_4370(repo: DispositivoRepositorySQLite) -> None:
    """El dataclass default es 4370 — confirma que llega tal cual a BD."""
    creado = repo.create(Dispositivo(id=None, nombre="X", ip="192.168.1.1"))
    assert creado.puerto == 4370


def test_get_by_id_encuentra_creado(repo: DispositivoRepositorySQLite) -> None:
    creado = repo.create(_nuevo("Alcaldía"))
    assert creado.id is not None
    leido = repo.get_by_id(creado.id)
    assert leido is not None
    assert leido.nombre == "Alcaldía"


def test_get_by_id_inexistente_devuelve_none(
    repo: DispositivoRepositorySQLite,
) -> None:
    assert repo.get_by_id(9999) is None


def test_get_by_nombre_encuentra_y_es_case_sensitive(
    repo: DispositivoRepositorySQLite,
) -> None:
    repo.create(_nuevo("Alcaldía"))
    assert repo.get_by_nombre("Alcaldía") is not None
    assert repo.get_by_nombre("alcaldía") is None


def test_get_by_ip_puerto_encuentra(repo: DispositivoRepositorySQLite) -> None:
    repo.create(_nuevo("X", "10.0.0.1", 4370))
    encontrado = repo.get_by_ip_puerto("10.0.0.1", 4370)
    assert encontrado is not None
    assert encontrado.nombre == "X"


def test_get_by_ip_puerto_distingue_puerto(
    repo: DispositivoRepositorySQLite,
) -> None:
    """Mismo IP, distinto puerto → son dos dispositivos distintos."""
    repo.create(_nuevo("A", "10.0.0.1", 4370))
    repo.create(_nuevo("B", "10.0.0.1", 4371))
    assert repo.get_by_ip_puerto("10.0.0.1", 4370).nombre == "A"  # type: ignore[union-attr]
    assert repo.get_by_ip_puerto("10.0.0.1", 4371).nombre == "B"  # type: ignore[union-attr]
    assert repo.get_by_ip_puerto("10.0.0.1", 9999) is None


# ── Tests: UNIQUEs ────────────────────────────────────────────────────────────


def test_create_duplicate_nombre_falla(repo: DispositivoRepositorySQLite) -> None:
    repo.create(_nuevo("Dup", "10.0.0.1", 4370))
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(_nuevo("Dup", "10.0.0.2", 4370))


def test_create_duplicate_ip_puerto_falla(
    repo: DispositivoRepositorySQLite,
) -> None:
    repo.create(_nuevo("A", "10.0.0.1", 4370))
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(_nuevo("B", "10.0.0.1", 4370))


def test_create_puerto_fuera_de_rango_falla(
    repo: DispositivoRepositorySQLite,
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(_nuevo("X", "10.0.0.1", 0))
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(_nuevo("Y", "10.0.0.2", 70000))


# ── Tests: listados ───────────────────────────────────────────────────────────


def test_list_all_vacio(repo: DispositivoRepositorySQLite) -> None:
    assert repo.list_all() == []


def test_list_all_ordenado_por_nombre(repo: DispositivoRepositorySQLite) -> None:
    repo.create(_nuevo("Zona 3", "10.0.0.3", 4370))
    repo.create(_nuevo("Alcaldía", "10.0.0.1", 4370))
    repo.create(_nuevo("Mantenimiento", "10.0.0.2", 4370))
    nombres = [d.nombre for d in repo.list_all()]
    assert nombres == ["Alcaldía", "Mantenimiento", "Zona 3"]


def test_list_active_excluye_archivados(
    repo: DispositivoRepositorySQLite,
) -> None:
    repo.create(_nuevo("Activo", "10.0.0.1", 4370))
    arch = repo.create(_nuevo("Archivado", "10.0.0.2", 4370))
    assert arch.id is not None
    repo.archive(arch.id)
    activos = repo.list_active()
    assert [d.nombre for d in activos] == ["Activo"]


def test_list_all_incluye_archivados(
    repo: DispositivoRepositorySQLite,
) -> None:
    repo.create(_nuevo("Activo", "10.0.0.1", 4370))
    arch = repo.create(_nuevo("Archivado", "10.0.0.2", 4370))
    assert arch.id is not None
    repo.archive(arch.id)
    todos = repo.list_all()
    assert len(todos) == 2
    assert {d.is_active for d in todos} == {True, False}


# ── Tests: update / archive / unarchive ───────────────────────────────────────


def test_update_cambia_nombre_ip_puerto(
    repo: DispositivoRepositorySQLite,
) -> None:
    creado = repo.create(_nuevo("Viejo", "10.0.0.1", 4370))
    assert creado.id is not None
    creado.nombre = "Nuevo"
    creado.ip = "10.0.0.99"
    creado.puerto = 4371
    repo.update(creado)
    leido = repo.get_by_id(creado.id)
    assert leido is not None
    assert leido.nombre == "Nuevo"
    assert leido.ip == "10.0.0.99"
    assert leido.puerto == 4371


def test_update_sin_id_lanza_value_error(
    repo: DispositivoRepositorySQLite,
) -> None:
    with pytest.raises(ValueError, match="id"):
        repo.update(_nuevo("sin id"))


def test_update_no_toca_is_active(repo: DispositivoRepositorySQLite) -> None:
    """``update`` solo mueve nombre/ip/puerto — ``is_active`` queda intacto."""
    creado = repo.create(_nuevo("X", "10.0.0.1", 4370))
    assert creado.id is not None
    repo.archive(creado.id)
    creado.nombre = "Y"
    creado.is_active = True  # intenta reactivar por la puerta trasera
    repo.update(creado)
    leido = repo.get_by_id(creado.id)
    assert leido is not None
    assert leido.is_active is False  # sigue archivado
    assert leido.nombre == "Y"  # nombre sí cambió


def test_update_colision_nombre_falla(repo: DispositivoRepositorySQLite) -> None:
    repo.create(_nuevo("A", "10.0.0.1", 4370))
    b = repo.create(_nuevo("B", "10.0.0.2", 4370))
    assert b.id is not None
    b.nombre = "A"
    with pytest.raises(sqlite3.IntegrityError):
        repo.update(b)


def test_archive_y_unarchive_cambian_estado(
    repo: DispositivoRepositorySQLite,
) -> None:
    creado = repo.create(_nuevo("Ping", "10.0.0.1", 4370))
    assert creado.id is not None
    repo.archive(creado.id)
    assert repo.get_by_id(creado.id).is_active is False  # type: ignore[union-attr]
    repo.unarchive(creado.id)
    assert repo.get_by_id(creado.id).is_active is True  # type: ignore[union-attr]


def test_archive_id_inexistente_no_falla(
    repo: DispositivoRepositorySQLite,
) -> None:
    """UPDATE de 0 filas no es error en SQL."""
    repo.archive(9999)
