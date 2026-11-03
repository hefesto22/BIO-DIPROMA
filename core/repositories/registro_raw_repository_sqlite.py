"""Implementación SQLite del repositorio de RegistroRaw."""

from __future__ import annotations

import sqlite3
from typing import List, Optional, Sequence

from core.models.registro_raw import RegistroRaw
from core.repositories.registro_raw_repository import (
    IRegistroRawReadRepository,
    IRegistroRawWriteRepository,
)
from infrastructure.database.connection import Database


def _row_to_registro_raw(row: sqlite3.Row) -> RegistroRaw:
    """Mapea una fila de ``registros_raw`` al dataclass ``RegistroRaw``."""
    return RegistroRaw(
        id=row["id"],
        dispositivo_id=row["dispositivo_id"],
        sincronizacion_id=row["sincronizacion_id"],
        zkteco_user_id=row["zkteco_user_id"],
        timestamp=row["timestamp"],
        tipo_marcada=row["tipo_marcada"],
    )


class RegistroRawRepositorySQLite(IRegistroRawReadRepository, IRegistroRawWriteRepository):
    """Implementación SQLite — una conexión por operación.

    Nota de performance:
        ``create_bulk`` usa ``executemany`` con ``INSERT OR IGNORE`` en
        una sola transacción. Para una sync típica (~5000 marcadas/mes)
        esto es del orden de decenas de ms — no justifica chunks.
    """

    _SELECT_COLS = (
        "id, dispositivo_id, sincronizacion_id, zkteco_user_id, " "timestamp, tipo_marcada"
    )

    def __init__(self, database: Database) -> None:
        """Inicializa el repo con el adaptador de BD inyectado."""
        self._db = database

    # ── Read ──────────────────────────────────────────────────────────────

    def get_by_id(self, registro_id: int) -> Optional[RegistroRaw]:
        with self._db.transaction() as conn:
            row: Optional[sqlite3.Row] = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM registros_raw WHERE id = ?",
                (registro_id,),
            ).fetchone()
        return _row_to_registro_raw(row) if row is not None else None

    def list_by_sincronizacion(self, sincronizacion_id: int) -> List[RegistroRaw]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM registros_raw "
                "WHERE sincronizacion_id = ? "
                "ORDER BY timestamp ASC, id ASC",
                (sincronizacion_id,),
            ).fetchall()
        return [_row_to_registro_raw(r) for r in rows]

    def list_by_zkteco_user_y_rango(
        self,
        zkteco_user_id: int,
        dispositivo_id: int,
        desde: str,
        hasta: str,
    ) -> List[RegistroRaw]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM registros_raw "
                "WHERE zkteco_user_id = ? AND dispositivo_id = ? "
                "AND timestamp >= ? AND timestamp <= ? "
                "ORDER BY timestamp ASC, id ASC",
                (zkteco_user_id, dispositivo_id, desde, hasta),
            ).fetchall()
        return [_row_to_registro_raw(r) for r in rows]

    def list_by_rango(self, desde: str, hasta: str) -> List[RegistroRaw]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM registros_raw "
                "WHERE timestamp >= ? AND timestamp <= ? "
                "ORDER BY zkteco_user_id ASC, timestamp ASC, id ASC",
                (desde, hasta),
            ).fetchall()
        return [_row_to_registro_raw(r) for r in rows]

    def count_by_sincronizacion(self, sincronizacion_id: int) -> int:
        with self._db.transaction() as conn:
            row: sqlite3.Row = conn.execute(
                "SELECT COUNT(*) AS total FROM registros_raw " "WHERE sincronizacion_id = ?",
                (sincronizacion_id,),
            ).fetchone()
        return int(row["total"])

    # ── Write ─────────────────────────────────────────────────────────────

    def create_bulk(self, registros: Sequence[RegistroRaw]) -> int:
        if not registros:
            return 0
        params = [
            (
                r.dispositivo_id,
                r.sincronizacion_id,
                r.zkteco_user_id,
                r.timestamp,
                r.tipo_marcada,
            )
            for r in registros
        ]
        with self._db.transaction() as conn:
            cursor = conn.executemany(
                "INSERT OR IGNORE INTO registros_raw "
                "(dispositivo_id, sincronizacion_id, zkteco_user_id, "
                " timestamp, tipo_marcada) "
                "VALUES (?, ?, ?, ?, ?)",
                params,
            )
            # rowcount refleja los INSERT realmente ejecutados; los
            # rechazados por IGNORE NO cuentan. Este es exactamente el
            # contador post-dedupe que queremos exponer.
            return cursor.rowcount
