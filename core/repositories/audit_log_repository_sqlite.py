"""Implementación SQLite del repositorio de AuditLog."""

from __future__ import annotations

import sqlite3
from typing import Dict, List

from core.models.audit_entry import AuditEntry
from core.repositories.audit_log_repository import (
    IAuditLogReadRepository,
    IAuditLogWriteRepository,
)
from infrastructure.database.connection import Database


def _row_to_audit_entry(row: sqlite3.Row) -> AuditEntry:
    """Mapea una fila de ``audit_log`` al dataclass ``AuditEntry``."""
    return AuditEntry(
        id=row["id"],
        user_id=row["user_id"],
        action=row["action"],
        machine_name=row["machine_name"],
        timestamp=row["timestamp"],
        details=row["details"],
    )


class AuditLogRepositorySQLite(IAuditLogReadRepository, IAuditLogWriteRepository):
    """Implementación SQLite — Opción A: una conexión por operación.

    Implementa ambos contratos (read + write) pero, por política del
    proyecto, el write solo expone ``insert`` (append-only).
    """

    _SELECT_COLS = "id, user_id, action, machine_name, timestamp, details"

    def __init__(self, database: Database) -> None:
        """Inicializa el repo con el adaptador de BD inyectado."""
        self._db = database

    # ── Read ──────────────────────────────────────────────────────────────

    def list_recent(self, limit: int) -> List[AuditEntry]:
        if limit <= 0:
            raise ValueError(f"limit debe ser > 0, recibido: {limit}")
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM audit_log "
                "ORDER BY timestamp DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_audit_entry(r) for r in rows]

    def list_by_user(self, user_id: int) -> List[AuditEntry]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM audit_log "
                "WHERE user_id = ? ORDER BY timestamp DESC, id DESC",
                (user_id,),
            ).fetchall()
        return [_row_to_audit_entry(r) for r in rows]

    def last_timestamp_by_user(self, action: str) -> Dict[int, str]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                "SELECT user_id, MAX(timestamp) AS ultimo FROM audit_log "
                "WHERE action = ? AND user_id IS NOT NULL GROUP BY user_id",
                (action,),
            ).fetchall()
        return {int(r["user_id"]): str(r["ultimo"]) for r in rows}

    # ── Write ─────────────────────────────────────────────────────────────

    def insert(self, entry: AuditEntry) -> AuditEntry:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO audit_log "
                "(user_id, action, machine_name, timestamp, details) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    entry.user_id,
                    entry.action,
                    entry.machine_name,
                    entry.timestamp,
                    entry.details,
                ),
            )
            new_id = cursor.lastrowid
        # Devolvemos una instancia nueva con el id asignado (frozen dataclass
        # no permite mutar el original).
        return AuditEntry(
            id=new_id,
            user_id=entry.user_id,
            action=entry.action,
            machine_name=entry.machine_name,
            timestamp=entry.timestamp,
            details=entry.details,
        )
