"""Implementación SQLite del repositorio de Ausencia."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from typing import List, Optional

from core.models.ausencia import Ausencia
from core.repositories.ausencia_repository import (
    IAusenciaReadRepository,
    IAusenciaWriteRepository,
)
from infrastructure.database.connection import Database


def _row_to_ausencia(row: sqlite3.Row) -> Ausencia:
    """Mapea una fila de ``ausencias`` al dataclass ``Ausencia``."""
    return Ausencia(
        id=row["id"],
        empleado_id=row["empleado_id"],
        tipo=row["tipo"],
        fecha_desde=row["fecha_desde"],
        fecha_hasta=row["fecha_hasta"],
        nota=row["nota"],
        creado_por_user_id=row["creado_por_user_id"],
        creado_en=row["creado_en"],
    )


class AusenciaRepositorySQLite(IAusenciaReadRepository, IAusenciaWriteRepository):
    """Implementación SQLite — una conexión por operación."""

    _SELECT_COLS = (
        "id, empleado_id, tipo, fecha_desde, fecha_hasta, nota, creado_por_user_id, creado_en"
    )
    _ORDER_BY = "ORDER BY fecha_desde DESC, id DESC"

    def __init__(self, database: Database) -> None:
        """Inicializa el repo con el adaptador de BD inyectado."""
        self._db = database

    # ── Read ──────────────────────────────────────────────────────────────

    def get_by_id(self, ausencia_id: int) -> Optional[Ausencia]:
        with self._db.transaction() as conn:
            row: Optional[sqlite3.Row] = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM ausencias WHERE id = ?",
                (ausencia_id,),
            ).fetchone()
        return _row_to_ausencia(row) if row is not None else None

    def list_all(self) -> List[Ausencia]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM ausencias {self._ORDER_BY}"
            ).fetchall()
        return [_row_to_ausencia(r) for r in rows]

    def list_by_empleado(self, empleado_id: int) -> List[Ausencia]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM ausencias WHERE empleado_id = ? "
                f"{self._ORDER_BY}",
                (empleado_id,),
            ).fetchall()
        return [_row_to_ausencia(r) for r in rows]

    def list_en_rango(self, desde: str, hasta: str) -> List[Ausencia]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM ausencias "
                f"WHERE fecha_desde <= ? AND fecha_hasta >= ? {self._ORDER_BY}",
                (hasta, desde),
            ).fetchall()
        return [_row_to_ausencia(r) for r in rows]

    # ── Write ─────────────────────────────────────────────────────────────

    def create(self, ausencia: Ausencia) -> Ausencia:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO ausencias "
                "(empleado_id, tipo, fecha_desde, fecha_hasta, nota, creado_por_user_id, "
                " creado_en) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    ausencia.empleado_id,
                    ausencia.tipo,
                    ausencia.fecha_desde,
                    ausencia.fecha_hasta,
                    ausencia.nota,
                    ausencia.creado_por_user_id,
                    ausencia.creado_en,
                ),
            )
            nuevo_id = cursor.lastrowid
        return replace(ausencia, id=nuevo_id)

    def update(self, ausencia: Ausencia) -> None:
        if ausencia.id is None:
            raise ValueError("No se puede actualizar una Ausencia sin id asignado.")
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE ausencias SET tipo = ?, fecha_desde = ?, fecha_hasta = ?, nota = ? "
                "WHERE id = ?",
                (
                    ausencia.tipo,
                    ausencia.fecha_desde,
                    ausencia.fecha_hasta,
                    ausencia.nota,
                    ausencia.id,
                ),
            )

    def delete(self, ausencia_id: int) -> None:
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM ausencias WHERE id = ?", (ausencia_id,))
