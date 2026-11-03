"""Implementación SQLite del repositorio de Rol."""

from __future__ import annotations

import json
import sqlite3
from typing import List, Optional

from core.models.rol import Rol
from core.repositories.rol_repository import IRolReadRepository
from infrastructure.database.connection import Database


def _row_to_rol(row: sqlite3.Row) -> Rol:
    """Mapea una fila de ``roles`` al dataclass ``Rol``.

    Deserializa ``permissions_json`` y lo envuelve en ``frozenset`` para
    preservar la inmutabilidad del dominio.
    """
    permisos = json.loads(row["permissions_json"])
    return Rol(
        id=row["id"],
        code=row["code"],
        name=row["name"],
        description=row["description"],
        permissions=frozenset(permisos),
    )


class RolRepositorySQLite(IRolReadRepository):
    """Implementación SQLite — Opción A: una conexión por operación."""

    _SELECT_COLS = "id, code, name, description, permissions_json"

    def __init__(self, database: Database) -> None:
        """Inicializa el repo con el adaptador de BD inyectado."""
        self._db = database

    def get_by_id(self, role_id: int) -> Optional[Rol]:
        with self._db.transaction() as conn:
            row: Optional[sqlite3.Row] = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM roles WHERE id = ?",
                (role_id,),
            ).fetchone()
        return _row_to_rol(row) if row is not None else None

    def get_by_code(self, code: str) -> Optional[Rol]:
        with self._db.transaction() as conn:
            row: Optional[sqlite3.Row] = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM roles WHERE code = ?",
                (code,),
            ).fetchone()
        return _row_to_rol(row) if row is not None else None

    def list_all(self) -> List[Rol]:
        with self._db.transaction() as conn:
            rows = conn.execute(f"SELECT {self._SELECT_COLS} FROM roles ORDER BY id ASC").fetchall()
        return [_row_to_rol(r) for r in rows]
