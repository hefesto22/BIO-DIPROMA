"""Adaptador de conexión SQLite.

Responsabilidad única (SRP): abrir conexiones al archivo SQLite aplicando los
PRAGMAs obligatorios del proyecto (WAL mode + foreign keys) y ofrecer un
context manager de transacción con commit/rollback automático.

Diseño:
    - Clase `Database` instanciable, NO singleton. Cada capa la recibe por
      inyección en constructor (regla SOLID-D del proyecto).
    - `connect()` devuelve una NUEVA conexión cada vez. SQLite por defecto
      prohíbe compartir una conexión entre hilos, así que en vez de cachear,
      damos una por consumidor. Con WAL, múltiples lectores + 1 escritor
      corren sin bloqueo entre conexiones distintas.
    - `transaction()` entrega la conexión lista para usarse; al salir del
      bloque hace commit (o rollback ante excepción) y cierra la conexión.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Union

# Tipo aceptado como ruta: str (incluye ":memory:") o Path.
DatabasePath = Union[str, Path]


class Database:
    """Adaptador SQLite con WAL + foreign keys habilitados por defecto."""

    def __init__(self, db_path: DatabasePath) -> None:
        """Inicializa el adaptador.

        Args:
            db_path: Ruta al archivo .db o ":memory:" para BD en memoria
                (usada en tests unitarios).
        """
        self._path: str = str(db_path)
        self._log = logging.getLogger(self.__class__.__name__)

    @property
    def path(self) -> str:
        """Ruta del archivo SQLite (o ':memory:')."""
        return self._path

    def connect(self) -> sqlite3.Connection:
        """Abre una nueva conexión con los PRAGMAs obligatorios aplicados.

        Returns:
            Conexión SQLite con row_factory=Row (acceso por nombre de columna).
        """
        self._log.debug("Abriendo conexión SQLite: %s", self._path)
        conn = sqlite3.connect(
            self._path,
            detect_types=sqlite3.PARSE_DECLTYPES,
            # check_same_thread=False intencionalmente NO se pasa: preferimos
            # que cada hilo abra su propia conexión vía connect() nuevamente.
        )
        conn.row_factory = sqlite3.Row
        self._apply_pragmas(conn)
        return conn

    @staticmethod
    def _apply_pragmas(conn: sqlite3.Connection) -> None:
        """Aplica PRAGMAs obligatorios: WAL mode + foreign keys + synchronous."""
        # WAL: escrituras y lecturas concurrentes sin bloquearse entre sí.
        # Nota: en BDs en memoria (":memory:") WAL no aplica y SQLite ignora
        # silenciosamente el cambio — no es un error.
        conn.execute("PRAGMA journal_mode=WAL")

        # Foreign keys: obligatorias en el proyecto, deben activarse por
        # conexión (no son persistentes a nivel de archivo).
        conn.execute("PRAGMA foreign_keys=ON")

        # NORMAL es la recomendación oficial para WAL: balance perf/durabilidad.
        # FULL solo añade una sync extra que con WAL no aporta mucho y penaliza.
        conn.execute("PRAGMA synchronous=NORMAL")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Context manager con commit/rollback automático.

        Uso:
            with db.transaction() as conn:
                conn.execute("INSERT INTO ...", (...))

        Si el bloque lanza excepción, se hace rollback y se re-lanza.
        En ambos casos la conexión se cierra al salir.
        """
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
