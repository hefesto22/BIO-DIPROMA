"""Implementación SQLite del repositorio de DescargaReporte."""

from __future__ import annotations

import sqlite3
from typing import List, Optional

from core.models.descarga_reporte import DescargaReporte
from core.repositories.descarga_reporte_repository import (
    IDescargaReporteReadRepository,
    IDescargaReporteWriteRepository,
)
from infrastructure.database.connection import Database


def _row_to_descarga(row: sqlite3.Row) -> DescargaReporte:
    """Mapea una fila de ``descargas_reportes`` al dataclass."""
    return DescargaReporte(
        id=row["id"],
        user_id=row["user_id"],
        fecha_hora_utc=row["fecha_hora_utc"],
        tipo_reporte=row["tipo_reporte"],
        rango_desde=row["rango_desde"],
        rango_hasta=row["rango_hasta"],
        empleado_id_filtro=row["empleado_id_filtro"],
        ruta_archivo=row["ruta_archivo"],
        filas_exportadas=row["filas_exportadas"],
    )


class DescargaReporteRepositorySQLite(
    IDescargaReporteReadRepository,
    IDescargaReporteWriteRepository,
):
    """Implementación SQLite — una conexión por operación."""

    _SELECT_COLS = (
        "id, user_id, fecha_hora_utc, tipo_reporte, rango_desde, rango_hasta, "
        "empleado_id_filtro, ruta_archivo, filas_exportadas"
    )

    def __init__(self, database: Database) -> None:
        """Inicializa el repo con el adaptador de BD inyectado."""
        self._db = database

    # ── Read ──────────────────────────────────────────────────────────────

    def get_by_id(self, descarga_id: int) -> Optional[DescargaReporte]:
        with self._db.transaction() as conn:
            row: Optional[sqlite3.Row] = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM descargas_reportes WHERE id = ?",
                (descarga_id,),
            ).fetchone()
        return _row_to_descarga(row) if row is not None else None

    def list_recientes(self, limit: int = 50) -> List[DescargaReporte]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM descargas_reportes "
                "ORDER BY fecha_hora_utc DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_descarga(r) for r in rows]

    def list_by_user(self, user_id: int, limit: int = 50) -> List[DescargaReporte]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM descargas_reportes "
                "WHERE user_id = ? "
                "ORDER BY fecha_hora_utc DESC, id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [_row_to_descarga(r) for r in rows]

    # ── Write ─────────────────────────────────────────────────────────────

    def insert(self, descarga: DescargaReporte) -> DescargaReporte:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO descargas_reportes "
                "(user_id, fecha_hora_utc, tipo_reporte, rango_desde, rango_hasta, "
                " empleado_id_filtro, ruta_archivo, filas_exportadas) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    descarga.user_id,
                    descarga.fecha_hora_utc,
                    descarga.tipo_reporte,
                    descarga.rango_desde,
                    descarga.rango_hasta,
                    descarga.empleado_id_filtro,
                    descarga.ruta_archivo,
                    descarga.filas_exportadas,
                ),
            )
            new_id = cursor.lastrowid
        return DescargaReporte(
            id=new_id,
            user_id=descarga.user_id,
            fecha_hora_utc=descarga.fecha_hora_utc,
            tipo_reporte=descarga.tipo_reporte,
            rango_desde=descarga.rango_desde,
            rango_hasta=descarga.rango_hasta,
            empleado_id_filtro=descarga.empleado_id_filtro,
            ruta_archivo=descarga.ruta_archivo,
            filas_exportadas=descarga.filas_exportadas,
        )
