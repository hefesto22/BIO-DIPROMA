"""Tests unitarios para ``infrastructure.paths``.

Cubren los dos modos de ejecución:
    - dev (sin congelar) — ``app_dir()`` apunta a la raíz del repo y
      ``resource_path()`` también.
    - frozen (PyInstaller) — ``app_dir()`` apunta al directorio del
      ejecutable y ``resource_path()`` a ``sys._MEIPASS``.

Estrategia: monkeypatch sobre ``sys.frozen``, ``sys.executable`` y
``sys._MEIPASS`` para simular cada escenario sin necesidad de un
build real.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from infrastructure import paths


# ── Modo dev (sin congelar) ───────────────────────────────────────────────────


def test_is_frozen_returns_false_in_dev() -> None:
    """En dev no hay sys.frozen, así que is_frozen() debe ser False."""
    # No monkeypatch — corremos contra el intérprete real, que NO está
    # congelado. Esto verifica el camino "feliz" del entorno de tests.
    assert paths.is_frozen() is False


def test_app_dir_in_dev_is_repo_root() -> None:
    """En dev, ``app_dir()`` apunta a la raíz del repo (donde vive main.py)."""
    result = paths.app_dir()
    # El archivo main.py debe existir en esa carpeta — la raíz del repo.
    assert (result / "main.py").is_file()
    # Y la carpeta infrastructure/ también debe existir allí.
    assert (result / "infrastructure").is_dir()


def test_resource_path_in_dev_is_relative_to_repo_root() -> None:
    """En dev, ``resource_path()`` resuelve relativo a la raíz del repo."""
    migrations = paths.resource_path("infrastructure/database/migrations")
    assert migrations.is_dir(), f"esperaba carpeta de migraciones en {migrations}"
    # Sanity: hay al menos un archivo SQL adentro.
    assert any(p.suffix == ".sql" for p in migrations.iterdir())


def test_db_path_default() -> None:
    """``db_path()`` = ``app_dir() / 'data' / 'zkteco_app.db'``."""
    expected = paths.app_dir() / "data" / "zkteco_app.db"
    assert paths.db_path() == expected


def test_logs_dir_default() -> None:
    """``logs_dir()`` = ``app_dir() / 'data' / 'logs'``."""
    expected = paths.app_dir() / "data" / "logs"
    assert paths.logs_dir() == expected


def test_exports_dir_default() -> None:
    """``exports_dir()`` = ``app_dir() / 'data' / 'exports'``."""
    expected = paths.app_dir() / "data" / "exports"
    assert paths.exports_dir() == expected


def test_migrations_dir_points_to_real_folder() -> None:
    """``migrations_dir()`` apunta a la carpeta con scripts NNN_*.sql."""
    folder = paths.migrations_dir()
    assert folder.is_dir()
    # Hay al menos una migración real (001_init.sql existe desde Fase 1).
    assert any(p.name.startswith("001_") for p in folder.iterdir())


# ── Modo frozen (simulado) ────────────────────────────────────────────────────


def test_is_frozen_returns_true_when_sys_frozen_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si ``sys.frozen=True``, ``is_frozen()`` debe reportar True."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert paths.is_frozen() is True


def test_app_dir_in_frozen_mode_uses_executable_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Bajo frozen, ``app_dir()`` = carpeta del ejecutable (Path(sys.executable).parent)."""
    fake_exe = tmp_path / "zkteco.exe"
    fake_exe.write_text("not really an exe")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))

    assert paths.app_dir() == tmp_path


def test_resource_path_in_frozen_mode_uses_meipass(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Bajo PyInstaller --onefile, los recursos se leen desde sys._MEIPASS."""
    bundle = tmp_path / "_MEIxxxxxx"
    bundle.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)

    result = paths.resource_path("infrastructure/database/migrations")
    assert result == bundle / "infrastructure/database/migrations"


def test_db_path_in_frozen_mode_uses_executable_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Bajo frozen, la BD vive en ``<exe-dir>/data/zkteco_app.db`` (modo portable)."""
    fake_exe = tmp_path / "zkteco.exe"
    fake_exe.write_text("not really an exe")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))

    assert paths.db_path() == tmp_path / "data" / "zkteco_app.db"


# ── ensure_runtime_dirs ───────────────────────────────────────────────────────


def test_ensure_runtime_dirs_creates_subdirs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``ensure_runtime_dirs()`` crea data/, data/logs/ y data/exports/."""
    fake_exe = tmp_path / "zkteco.exe"
    fake_exe.write_text("not really an exe")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))

    # Ninguna carpeta data/ existe todavía.
    assert not (tmp_path / "data").exists()

    paths.ensure_runtime_dirs()

    assert (tmp_path / "data").is_dir()
    assert (tmp_path / "data" / "logs").is_dir()
    assert (tmp_path / "data" / "exports").is_dir()


def test_ensure_runtime_dirs_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Llamar ``ensure_runtime_dirs()`` dos veces no falla ni borra archivos."""
    fake_exe = tmp_path / "zkteco.exe"
    fake_exe.write_text("not really an exe")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))

    paths.ensure_runtime_dirs()
    # Sembramos un archivo dentro de logs/ — no debe ser tocado en la 2ª llamada.
    canary = tmp_path / "data" / "logs" / "canary.log"
    canary.write_text("hello")

    paths.ensure_runtime_dirs()  # No debe lanzar.

    assert canary.read_text() == "hello"
