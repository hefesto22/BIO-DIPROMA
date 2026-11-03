"""Implementación SQLite del repositorio de Sincronizacion."""

from __future__ import annotations

import sqlite3
from typing import List, Optional

from core.models.sincronizacion import EstadoSincronizacion, Sincronizacion
from core.repositories.sincronizacion_repository import (
    ISincronizacionReadRepository,
    ISincronizacionWriteRepository,
)
from infrastructure.database.connection import Database


def _row_to_sincronizacion(row: sqlite3.Row) -> Sincronizacion:
    """Mapea una fila de ``sincronizaciones`` al dataclass."""
    return Sincronizacion(
        id=row["id"],
        dispositivo_id=row["dispositivo_id"],
        iniciada_por_user_id=row["iniciada_por_user_id"],
        inicio=row["inicio"],
        rango_desde=row["rango_desde"],
        rango_hasta=row["rango_hasta"],
        fin=row["fin"],
        registros_recibidos=row["registros_recibidos"],
        estado=row["estado"],
        error_mensaje=row["error_mensaje"],
    )


class SincronizacionRepositorySQLite(ISincronizacionReadRepository, ISincronizacionWriteRepository):
    """Implementación SQLite — una conexión por operación.

    Los verbos ``marcar_ok`` / ``marcar_fallida`` verifican con
    ``cursor.rowcount`` que la fila existe; si no, lanzan ``ValueError``
    (no es IntegrityError: es un contrato violado por el caller).
    """

    _SELECT_COLS = (
        "id, dispositivo_id, iniciada_por_user_id, inicio, rango_desde, "
        "rango_hasta, fin, registros_recibidos, estado, error_mensaje"
    )

    def __init__(self, database: Database) -> None:
        """Inicializa el repo con el adaptador de BD inyectado."""
        self._db = database

    # ── Read ──────────────────────────────────────────────────────────────

    def get_by_id(self, sincronizacion_id: int) -> Optional[Sincronizacion]:
        with self._db.transaction() as conn:
            row: Optional[sqlite3.Row] = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM sincronizaciones WHERE id = ?",
                (sincronizacion_id,),
            ).fetchone()
        return _row_to_sincronizacion(row) if row is not None else None

    def list_recientes(self, limit: int = 50) -> List[Sincronizacion]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM sincronizaciones "
                "ORDER BY inicio DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_sincronizacion(r) for r in rows]

    def list_by_dispositivo(self, dispositivo_id: int, limit: int = 50) -> List[Sincronizacion]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM sincronizaciones "
                "WHERE dispositivo_id = ? "
                "ORDER BY inicio DESC, id DESC LIMIT ?",
                (dispositivo_id, limit),
            ).fetchall()
        return [_row_to_sincronizacion(r) for r in rows]

    def list_en_curso(self) -> List[Sincronizacion]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM sincronizaciones "
                "WHERE estado = ? ORDER BY inicio ASC, id ASC",
                (EstadoSincronizacion.EN_CURSO.value,),
            ).fetchall()
        return [_row_to_sincronizacion(r) for r in rows]

    # ── Write ─────────────────────────────────────────────────────────────

    def create(self, sincronizacion: Sincronizacion) -> Sincronizacion:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO sincronizaciones "
                "(dispositivo_id, iniciada_por_user_id, inicio, rango_desde, "
                " rango_hasta, fin, registros_recibidos, estado, error_mensaje) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    sincronizacion.dispositivo_id,
                    sincronizacion.iniciada_por_user_id,
                    sincronizacion.inicio,
                    sincronizacion.rango_desde,
                    sincronizacion.rango_hasta,
                    sincronizacion.fin,
                    sincronizacion.registros_recibidos,
                    sincronizacion.estado,
                    sincronizacion.error_mensaje,
                ),
            )
            new_id = cursor.lastrowid
        return Sincronizacion(
            id=new_id,
            dispositivo_id=sincronizacion.dispositivo_id,
            iniciada_por_user_id=sincronizacion.iniciada_por_user_id,
            inicio=sincronizacion.inicio,
            rango_desde=sincronizacion.rango_desde,
            rango_hasta=sincronizacion.rango_hasta,
            fin=sincronizacion.fin,
            registros_recibidos=sincronizacion.registros_recibidos,
            estado=sincronizacion.estado,
            error_mensaje=sincronizacion.error_mensaje,
        )

    def marcar_ok(self, sincronizacion_id: int, fin: str, registros_recibidos: int) -> None:
        if registros_recibidos < 0:
            raise ValueError(f"registros_recibidos debe ser >= 0, recibido: {registros_recibidos}")
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE sincronizaciones SET "
                "fin = ?, registros_recibidos = ?, estado = ?, error_mensaje = NULL "
                "WHERE id = ?",
                (fin, registros_recibidos, EstadoSincronizacion.OK.value, sincronizacion_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"No existe sincronización con id={sincronizacion_id}.")

    def marcar_fallida(self, sincronizacion_id: int, fin: str, error_mensaje: str) -> None:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE sincronizaciones SET "
                "fin = ?, estado = ?, error_mensaje = ? "
                "WHERE id = ?",
                (
                    fin,
                    EstadoSincronizacion.FALLIDA.value,
                    error_mensaje,
                    sincronizacion_id,
                ),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"No existe sincronización con id={sincronizacion_id}.")
