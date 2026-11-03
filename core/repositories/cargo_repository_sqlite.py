"""Implementación SQLite del repositorio de Cargo."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import List, Optional

from core.models.cargo import Cargo
from core.repositories.cargo_repository import (
    ICargoReadRepository,
    ICargoWriteRepository,
)
from infrastructure.database.connection import Database


def _row_to_cargo(row: sqlite3.Row) -> Cargo:
    """Mapea una fila de ``cargos`` al dataclass ``Cargo``."""
    return Cargo(
        id=row["id"],
        nombre=row["nombre"],
        is_active=bool(row["is_active"]),
        departamento_id=row["departamento_id"],
    )


class CargoRepositorySQLite(ICargoReadRepository, ICargoWriteRepository):
    """Implementación SQLite — Opción A: una conexión por operación.

    Sub-3.2.A: el campo ``departamento_id`` es nullable. ``None`` =
    cargo global (aparece en cualquier departamento del formulario de
    empleados); un valor restringe el cargo a ese departamento.
    """

    _SELECT_COLS = "id, nombre, is_active, departamento_id"

    def __init__(self, database: Database) -> None:
        """Inicializa el repo con el adaptador de BD inyectado."""
        self._db = database

    # ── Read ──────────────────────────────────────────────────────────────

    def get_by_id(self, cargo_id: int) -> Optional[Cargo]:
        with self._db.transaction() as conn:
            row: Optional[sqlite3.Row] = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM cargos WHERE id = ?",
                (cargo_id,),
            ).fetchone()
        return _row_to_cargo(row) if row is not None else None

    def get_by_nombre(self, nombre: str) -> Optional[Cargo]:
        with self._db.transaction() as conn:
            row: Optional[sqlite3.Row] = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM cargos WHERE nombre = ?",
                (nombre,),
            ).fetchone()
        return _row_to_cargo(row) if row is not None else None

    def list_all(self) -> List[Cargo]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM cargos ORDER BY nombre ASC"
            ).fetchall()
        return [_row_to_cargo(r) for r in rows]

    def list_active(self) -> List[Cargo]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM cargos " "WHERE is_active = 1 ORDER BY nombre ASC"
            ).fetchall()
        return [_row_to_cargo(r) for r in rows]

    def list_active_para_departamento(self, departamento_id: int) -> List[Cargo]:
        """Sub-3.2.A: cargos globales + específicos del departamento."""
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM cargos "
                "WHERE is_active = 1 "
                "  AND (departamento_id IS NULL OR departamento_id = ?) "
                "ORDER BY nombre ASC",
                (departamento_id,),
            ).fetchall()
        return [_row_to_cargo(r) for r in rows]

    # ── Write ─────────────────────────────────────────────────────────────

    def create(self, cargo: Cargo) -> Cargo:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO cargos (nombre, is_active, departamento_id) " "VALUES (?, ?, ?)",
                (cargo.nombre, 1 if cargo.is_active else 0, cargo.departamento_id),
            )
            new_id = cursor.lastrowid
        return Cargo(
            id=new_id,
            nombre=cargo.nombre,
            is_active=cargo.is_active,
            departamento_id=cargo.departamento_id,
        )

    def rename(self, cargo_id: int, nuevo_nombre: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE cargos SET nombre = ? WHERE id = ?",
                (nuevo_nombre, cargo_id),
            )

    def update_departamento(self, cargo_id: int, departamento_id: Optional[int]) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE cargos SET departamento_id = ? WHERE id = ?",
                (departamento_id, cargo_id),
            )

    def archive(self, cargo_id: int) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE cargos SET is_active = 0 WHERE id = ?",
                (cargo_id,),
            )

    def unarchive(self, cargo_id: int) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE cargos SET is_active = 1 WHERE id = ?",
                (cargo_id,),
            )

    def archive_moviendo(self, origen_id: int, destino_id: int) -> int:
        ahora = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._db.transaction() as conn:
            empleados = conn.execute(
                "UPDATE empleados SET cargo_id = ?, updated_at = ? "
                "WHERE cargo_id = ? AND is_active = 1",
                (destino_id, ahora, origen_id),
            ).rowcount
            conn.execute("UPDATE cargos SET is_active = 0 WHERE id = ?", (origen_id,))
        return int(empleados)
