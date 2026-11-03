"""Backup automático de la BD SQLite — Sub-3.1 (production-ready).

Esta función se invoca al arrancar la app: si todavía no hay backup
del día, copia ``zkteco_app.db`` a ``data/backups/zkteco_app_YYYY-MM-DD.db``
usando la API nativa de backup de SQLite (consistente incluso con WAL).
Después purga los backups con mtime mayor a ``max_age_days``.

Diseño:
    - Idempotente: si ya hay backup de hoy, no hace nada.
    - Atómico: usamos ``sqlite3.Connection.backup()`` (no shutil.copy)
      para garantizar que el archivo destino sea consistente aunque la
      app esté escribiendo en paralelo.
    - Tolerante a fallos: cualquier error se loguea pero NO frena el
      arranque. Perder un backup ocasional es preferible a impedir que
      el operador entre a la app por un disco lleno.
    - Sin estado global: todas las funciones reciben paths explícitos.

Convenciones:
    - Nombre del archivo de backup: ``zkteco_app_YYYY-MM-DD.db``.
    - Carpeta destino: ``<data_dir>/backups/`` (creada bajo demanda).
    - Retención: 30 días por default (configurable).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Glob para identificar archivos que el módulo gestiona. Solo se borran
# archivos que matchean este patrón — previene tocar archivos del
# operador que casualmente vivan en backups/.
_BACKUP_PREFIX: str = "zkteco_app_"
_BACKUP_SUFFIX: str = ".db"
_BACKUP_GLOB: str = f"{_BACKUP_PREFIX}*{_BACKUP_SUFFIX}"


def run_daily_backup(
    db_path: Path,
    backup_dir: Path,
    max_age_days: int = 30,
    today: Optional[date] = None,
) -> Optional[Path]:
    """Crea el backup del día si aún no existe; purga los expirados.

    Args:
        db_path: Ruta absoluta al archivo SQLite de producción.
        backup_dir: Carpeta donde se guardan los backups. Se crea si no
            existe.
        max_age_days: Días a conservar. Backups con mtime más viejo se
            borran. ``0`` deshabilita la purga (todos se conservan).
        today: Inyectable para tests. Si es ``None`` se usa
            ``date.today()``.

    Returns:
        El ``Path`` del backup creado, o ``None`` si:
            - El backup de hoy ya existe (no se hace nada).
            - El archivo origen no existe (primera ejecución sin BD).
            - La copia falló (el error se loguea, no se propaga).
    """
    if today is None:
        today = date.today()

    if not db_path.is_file():
        logger.debug("Backup omitido: %s no existe todavía.", db_path)
        return None

    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / _nombre_de_backup(today)

    if backup_path.exists():
        logger.debug("Backup de hoy ya existe: %s", backup_path)
        _purgar_expirados(backup_dir, max_age_days)
        return None

    try:
        _copiar_consistente(db_path, backup_path)
    except sqlite3.Error as exc:
        logger.warning("Backup falló: %s — la app continúa sin backup.", exc)
        # Si quedó un parcial, lo borramos para no engañar al próximo run.
        if backup_path.exists():
            backup_path.unlink(missing_ok=True)
        return None
    except OSError as exc:
        logger.warning("Backup falló por I/O: %s — la app continúa.", exc)
        if backup_path.exists():
            backup_path.unlink(missing_ok=True)
        return None

    logger.info("Backup creado: %s", backup_path)
    _purgar_expirados(backup_dir, max_age_days)
    return backup_path


# ── Helpers privados ─────────────────────────────────────────────────────────


def _nombre_de_backup(dia: date) -> str:
    """Devuelve el nombre canónico ``zkteco_app_YYYY-MM-DD.db``."""
    return f"{_BACKUP_PREFIX}{dia.isoformat()}{_BACKUP_SUFFIX}"


def _copiar_consistente(src: Path, dst: Path) -> None:
    """Copia ``src`` a ``dst`` usando la API nativa de backup de SQLite.

    Comparado con ``shutil.copy``, este método:
        - Toma un snapshot consistente incluso si la BD tiene un WAL
          activo o transacciones abiertas en otro proceso.
        - Asegura que el archivo destino sea una BD válida (no un
          archivo "a medio escribir").
    """
    src_conn = sqlite3.connect(str(src))
    dst_conn = sqlite3.connect(str(dst))
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()


def _purgar_expirados(backup_dir: Path, max_age_days: int) -> int:
    """Borra archivos de backup con mtime mayor a ``max_age_days``.

    Returns:
        Cantidad de archivos eliminados.
    """
    if max_age_days <= 0:
        return 0
    cutoff = datetime.now() - timedelta(days=max_age_days)
    eliminados = 0
    for archivo in backup_dir.glob(_BACKUP_GLOB):
        try:
            mtime = datetime.fromtimestamp(archivo.stat().st_mtime)
        except OSError:
            continue
        if mtime < cutoff:
            try:
                archivo.unlink()
                eliminados += 1
            except OSError as exc:
                logger.warning("No se pudo borrar backup viejo %s: %s", archivo, exc)
    if eliminados:
        logger.info("Purgados %d backup(s) viejos en %s", eliminados, backup_dir)
    return eliminados
