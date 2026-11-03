"""Implementación SQLite del repositorio de Feriado."""

from __future__ import annotations

import sqlite3
from typing import List, Optional

from core.models.feriado import Feriado
from core.repositories.feriado_repository import (
    IFeriadoReadRepository,
    IFeriadoWriteRepository,
)
from infrastructure.database.connection import Database


def _row_to_feriado(row: sqlite3.Row) -> Feriado:
    """Mapea una fila de ``feriados`` al dataclass ``Feriado``."""
    return Feriado(
        id=row["id"],
        fecha=row["fecha"],
        descripcion=row["descripcion"],
    )


class FeriadoRepositorySQLite(IFeriadoReadRepository, IFeriadoWriteRepository):
    """Implementación SQLite — Opción A: una conexión por operación."""

    _SELECT_COLS = "id, fecha, descripcion"
    _ORDER_BY = "ORDER BY fecha ASC"

    def __init__(self, database: Database) -> None:
        """Inicializa el repo con el adaptador de BD inyectado."""
        self._db = database

    # ── Read ──────────────────────────────────────────────────────────────

    def get_by_id(self, feriado_id: int) -> Optional[Feriado]:
        with self._db.transaction() as conn:
            row: Optional[sqlite3.Row] = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM feriados WHERE id = ?",
                (feriado_id,),
            ).fetchone()
        return _row_to_feriado(row) if row is not None else None

    def get_by_fecha(self, fecha: str) -> Optional[Feriado]:
        with self._db.transaction() as conn:
            row: Optional[sqlite3.Row] = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM feriados WHERE fecha = ?",
                (fecha,),
            ).fetchone()
        return _row_to_feriado(row) if row is not None else None

    def list_all(self) -> List[Feriado]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM feriados {self._ORDER_BY}"
            ).fetchall()
        return [_row_to_feriado(r) for r in rows]

    def list_by_rango(self, desde: str, hasta: str) -> List[Feriado]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM feriados "
                f"WHERE fecha >= ? AND fecha <= ? {self._ORDER_BY}",
                (desde, hasta),
            ).fetchall()
        return [_row_to_feriado(r) for r in rows]

    # ── Write ─────────────────────────────────────────────────────────────

    def create(self, feriado: Feriado) -> Feriado:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO feriados (fecha, descripcion) VALUES (?, ?)",
                (feriado.fecha, feriado.descripcion),
            )
            new_id = cursor.lastrowid
        return Feriado(
            id=new_id,
            fecha=feriado.fecha,
            descripcion=feriado.descripcion,
        )

    def update_descripcion(self, feriado_id: int, nueva_descripcion: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE feriados SET descripcion = ? WHERE id = ?",
                (nueva_descripcion, feriado_id),
            )

    def delete(self, feriado_id: int) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM feriados WHERE id = ?",
                (feriado_id,),
            )
