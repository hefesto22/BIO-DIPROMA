"""Implementación SQLite del repositorio de Departamento."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from core.models.departamento import Departamento
from core.repositories.departamento_repository import (
    IDepartamentoReadRepository,
    IDepartamentoWriteRepository,
)
from infrastructure.database.connection import Database


def _row_to_departamento(row: sqlite3.Row) -> Departamento:
    """Mapea una fila de ``departamentos`` al dataclass ``Departamento``."""
    return Departamento(
        id=row["id"],
        nombre=row["nombre"],
        is_active=bool(row["is_active"]),
    )


class DepartamentoRepositorySQLite(IDepartamentoReadRepository, IDepartamentoWriteRepository):
    """Implementación SQLite — Opción A: una conexión por operación.

    Esta clase implementa ambas interfaces (read + write). Los consumidores
    que solo necesiten una deben tipar el parámetro del constructor con
    la interfaz más estrecha (ISP).
    """

    _SELECT_COLS = "id, nombre, is_active"

    def __init__(self, database: Database) -> None:
        """Inicializa el repo con el adaptador de BD inyectado."""
        self._db = database

    # ── Read ──────────────────────────────────────────────────────────────

    def get_by_id(self, departamento_id: int) -> Optional[Departamento]:
        with self._db.transaction() as conn:
            row: Optional[sqlite3.Row] = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM departamentos WHERE id = ?",
                (departamento_id,),
            ).fetchone()
        return _row_to_departamento(row) if row is not None else None

    def get_by_nombre(self, nombre: str) -> Optional[Departamento]:
        with self._db.transaction() as conn:
            row: Optional[sqlite3.Row] = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM departamentos WHERE nombre = ?",
                (nombre,),
            ).fetchone()
        return _row_to_departamento(row) if row is not None else None

    def list_all(self) -> List[Departamento]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM departamentos " "ORDER BY nombre ASC"
            ).fetchall()
        return [_row_to_departamento(r) for r in rows]

    def list_active(self) -> List[Departamento]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM departamentos "
                "WHERE is_active = 1 ORDER BY nombre ASC"
            ).fetchall()
        return [_row_to_departamento(r) for r in rows]

    # ── Write ─────────────────────────────────────────────────────────────

    def create(self, departamento: Departamento) -> Departamento:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO departamentos (nombre, is_active) VALUES (?, ?)",
                (departamento.nombre, 1 if departamento.is_active else 0),
            )
            new_id = cursor.lastrowid
        return Departamento(
            id=new_id,
            nombre=departamento.nombre,
            is_active=departamento.is_active,
        )

    def rename(self, departamento_id: int, nuevo_nombre: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE departamentos SET nombre = ? WHERE id = ?",
                (nuevo_nombre, departamento_id),
            )

    def archive(self, departamento_id: int) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE departamentos SET is_active = 0 WHERE id = ?",
                (departamento_id,),
            )

    def unarchive(self, departamento_id: int) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE departamentos SET is_active = 1 WHERE id = ?",
                (departamento_id,),
            )

    def archive_moviendo(self, origen_id: int, destino_id: int) -> Tuple[int, int]:
        ahora = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._db.transaction() as conn:
            cargos = conn.execute(
                "UPDATE cargos SET departamento_id = ? WHERE departamento_id = ?",
                (destino_id, origen_id),
            ).rowcount
            empleados = conn.execute(
                "UPDATE empleados SET departamento_id = ?, updated_at = ? "
                "WHERE departamento_id = ? AND is_active = 1",
                (destino_id, ahora, origen_id),
            ).rowcount
            conn.execute(
                "UPDATE departamentos SET is_active = 0 WHERE id = ?",
                (origen_id,),
            )
        return empleados, cargos
