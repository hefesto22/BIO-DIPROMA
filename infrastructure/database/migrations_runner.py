"""Runner de migraciones SQL versionadas.

Responsabilidad única (SRP): descubrir archivos NNN_nombre.sql en el
directorio de migraciones, identificar cuáles no se han aplicado (según
la tabla schema_migrations) y aplicarlos en orden.

Diseño:
    - Recibe un `Database` por inyección (regla SOLID-D).
    - Cada archivo se aplica con `conn.executescript()` (soporta múltiples
      statements y hace commit implícito).
    - Tras aplicar el script, se inserta el registro en `schema_migrations`.
    - Política obligatoria para quienes escriban migraciones: usar SIEMPRE
      `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` para que
      re-ejecuciones accidentales sean idempotentes.

Formato de nombre de archivo: `NNN_descripcion.sql` con NNN de 3+ dígitos.
Cualquier archivo que no cumpla se ignora (incluyendo .gitkeep).
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, NamedTuple, Set

from infrastructure.database.connection import Database

# Regex para detectar archivos de migración válidos.
_MIGRATION_FILENAME_RE = re.compile(r"^(?P<version>\d{3,})_(?P<name>[a-z0-9_]+)\.sql$")


class Migration(NamedTuple):
    """Representa una migración descubierta en disco."""

    version: str
    name: str
    path: Path


class MigrationsRunner:
    """Aplica migraciones SQL pendientes en orden."""

    def __init__(self, database: Database, migrations_dir: Path) -> None:
        """Inicializa el runner.

        Args:
            database: Adaptador SQLite a usar (inyección de dependencia).
            migrations_dir: Carpeta con archivos NNN_nombre.sql.
        """
        self._db = database
        self._dir = migrations_dir
        self._log = logging.getLogger(self.__class__.__name__)

    def run(self) -> List[str]:
        """Aplica todas las migraciones pendientes.

        Returns:
            Lista de versiones recién aplicadas (vacía si todo estaba al día).
        """
        if not self._dir.is_dir():
            raise FileNotFoundError(f"Directorio de migraciones no existe: {self._dir}")

        migrations = self._discover_migrations()
        if not migrations:
            self._log.warning("No se encontraron migraciones en %s", self._dir)
            return []

        self._log.info("Descubiertas %d migraciones en disco.", len(migrations))

        conn = self._db.connect()
        try:
            applied: Set[str] = self._load_applied_versions(conn)
            newly_applied: List[str] = []
            for migration in migrations:
                if migration.version in applied:
                    self._log.debug("Migración %s ya aplicada, saltando.", migration.version)
                    continue
                self._apply_one(conn, migration)
                newly_applied.append(migration.version)

            if newly_applied:
                self._log.info(
                    "Aplicadas %d migraciones nuevas: %s",
                    len(newly_applied),
                    ", ".join(newly_applied),
                )
            else:
                self._log.info("Base de datos ya está al día — nada que aplicar.")
            return newly_applied
        finally:
            conn.close()

    def _discover_migrations(self) -> List[Migration]:
        """Lista archivos NNN_nombre.sql ordenados por versión."""
        migrations: List[Migration] = []
        for path in sorted(self._dir.iterdir()):
            if not path.is_file():
                continue
            match = _MIGRATION_FILENAME_RE.match(path.name)
            if not match:
                # Ignora .gitkeep, README.md, backups, etc.
                continue
            migrations.append(
                Migration(
                    version=match.group("version"),
                    name=match.group("name"),
                    path=path,
                )
            )
        return migrations

    def _load_applied_versions(self, conn: sqlite3.Connection) -> Set[str]:
        """Devuelve el set de versiones ya registradas en schema_migrations.

        Si la tabla aún no existe (primer arranque), retorna un set vacío.
        """
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        if row is None:
            return set()
        cursor = conn.execute("SELECT version FROM schema_migrations")
        return {row["version"] for row in cursor.fetchall()}

    def _apply_one(self, conn: sqlite3.Connection, migration: Migration) -> None:
        """Aplica una migración y la registra en schema_migrations.

        Política: si falla el script SQL, se hace rollback y se re-lanza.
        Si el script pasó pero el INSERT de tracking falla, también se
        re-lanza; la próxima ejecución reintentará (seguro gracias a
        CREATE ... IF NOT EXISTS en los scripts).
        """
        self._log.info("Aplicando migración %s_%s...", migration.version, migration.name)
        sql = migration.path.read_text(encoding="utf-8")
        try:
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (
                    migration.version,
                    migration.name,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            self._log.exception("Falló la migración %s — revisar SQL.", migration.version)
            raise
        self._log.info("Migración %s aplicada correctamente.", migration.version)
