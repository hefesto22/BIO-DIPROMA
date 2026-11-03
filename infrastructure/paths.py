"""Resolución de rutas de runtime — dev vs PyInstaller frozen.

Responsabilidad única (SRP): conocer dónde viven los archivos de la
aplicación según el modo de ejecución.

Modo dev (sin congelar)
-----------------------
- ``app_dir()``        → raíz del repo (donde está ``main.py``).
- ``resource_path()``  → relativo a la raíz del repo.

Modo congelado (PyInstaller, ``--onefile`` o ``--onedir``)
----------------------------------------------------------
- ``app_dir()``        → carpeta donde reside el ejecutable
                         (``Path(sys.executable).parent``). Es escribible
                         porque la app se distribuye en modo portable
                         (Opción B aprobada en Sub-6.1).
- ``resource_path()``  → ``sys._MEIPASS`` cuando existe (read-only,
                         contiene los datos empotrados con ``--add-data``).
                         Si no existe (build ``--onedir`` que empotra
                         recursos junto al ejecutable), cae a ``app_dir()``.

Subdirectorios bajo ``app_dir()``
---------------------------------
``data/``           — root para todo lo escribible.
``data/logs/``      — logs persistentes (RotatingFileHandler).
``data/exports/``   — sugerencia de carpeta para los Excel exportados.

La BD vive directamente en ``data/zkteco_app.db`` (un nivel arriba de
``logs/`` y ``exports/``) para que un backup simple sea "copiar
``data/``".

Diseño:
    - Funciones puras, sin estado global. Cada llamada calcula el
      valor en el momento — barato y compatible con monkeypatch en tests.
    - No depende de ``config`` para evitar ciclos: ``config`` puede
      delegar a este módulo, no al revés.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Nombre del archivo SQLite. Se mantiene el mismo que en dev pre-Fase 6
# para no requerir migración del archivo durante la transición.
_DB_FILENAME = "zkteco_app.db"

# Carpeta raíz para datos mutables, relativa a ``app_dir()``.
_DATA_SUBDIR = "data"
_LOGS_SUBDIR = "logs"
_EXPORTS_SUBDIR = "exports"
_BACKUPS_SUBDIR = "backups"

# Ruta a las migraciones SQL embebidas (read-only) — relativa al
# ``resource_path`` raíz.
_MIGRATIONS_REL = "infrastructure/database/migrations"

# Raíz del repo en modo dev (calculada una sola vez al importar).
# ``infrastructure/paths.py`` → ``infrastructure/`` → raíz del repo.
_REPO_ROOT: Path = Path(__file__).resolve().parent.parent


def is_frozen() -> bool:
    """Devuelve True si la app corre desde un bundle de PyInstaller.

    PyInstaller fija ``sys.frozen=True`` al congelar. En dev este
    atributo no existe.
    """
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """Carpeta raíz para datos mutables del usuario.

    En modo dev devuelve la raíz del repo. En modo congelado devuelve
    la carpeta donde reside el ejecutable — la app se distribuye como
    portable, así que esa carpeta es escribible.
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return _REPO_ROOT


def resource_path(rel: str) -> Path:
    """Resuelve una ruta de recurso read-only embebido en el bundle.

    Args:
        rel: Ruta relativa POSIX al recurso (p. ej.,
            ``"infrastructure/database/migrations"``).

    En modo congelado preferimos ``sys._MEIPASS`` (que PyInstaller
    expone solo bajo ``--onefile``). Si no existe — caso ``--onedir``
    o dev — caemos a la raíz del repo / del bundle.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is not None:
        return Path(meipass) / rel
    return _REPO_ROOT / rel


def data_dir() -> Path:
    """Carpeta ``<app_dir>/data`` con todos los archivos mutables."""
    return app_dir() / _DATA_SUBDIR


def db_path() -> Path:
    """Ruta absoluta del archivo SQLite de producción."""
    return data_dir() / _DB_FILENAME


def migrations_dir() -> Path:
    """Carpeta con los scripts de migración SQL (read-only)."""
    return resource_path(_MIGRATIONS_REL)


def logs_dir() -> Path:
    """Carpeta para logs persistentes."""
    return data_dir() / _LOGS_SUBDIR


def exports_dir() -> Path:
    """Carpeta sugerida como default del save dialog del export Excel."""
    return data_dir() / _EXPORTS_SUBDIR


def backups_dir() -> Path:
    """Carpeta donde el backup automático guarda los snapshots de la BD."""
    return data_dir() / _BACKUPS_SUBDIR


def ensure_runtime_dirs() -> None:
    """Crea ``data/``, ``data/logs/``, ``data/exports/`` y ``data/backups/``.

    Idempotente: si ya existen, no hace nada. Se llama una sola vez
    al arrancar (desde ``main.py``) antes de instalar el FileHandler
    o abrir la BD.
    """
    for path in (data_dir(), logs_dir(), exports_dir(), backups_dir()):
        path.mkdir(parents=True, exist_ok=True)
