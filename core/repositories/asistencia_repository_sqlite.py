"""Implementación SQLite del repositorio de Asistencia."""

from __future__ import annotations

import sqlite3
from typing import List, Optional

from core.models.asistencia import OBSERVACION_SALIDA_ASUMIDA, Asistencia
from core.repositories.asistencia_repository import (
    IAsistenciaReadRepository,
    IAsistenciaWriteRepository,
)
from infrastructure.database.connection import Database


def _row_to_asistencia(row: sqlite3.Row) -> Asistencia:
    """Mapea una fila de ``asistencias`` al dataclass ``Asistencia``."""
    return Asistencia(
        id=row["id"],
        empleado_id=row["empleado_id"],
        fecha=row["fecha"],
        turno_id_aplicado=row["turno_id_aplicado"],
        hora_entrada_real=row["hora_entrada_real"],
        hora_salida_real=row["hora_salida_real"],
        estado=row["estado"],
        minutos_tarde=row["minutos_tarde"],
        minutos_salida_temprana=row["minutos_salida_temprana"],
        observaciones=row["observaciones"],
        consolidada_en=row["consolidada_en"],
    )


class AsistenciaRepositorySQLite(IAsistenciaReadRepository, IAsistenciaWriteRepository):
    """Implementación SQLite — una conexión por operación.

    UPSERT con preservación de ``observaciones``:
        SQLite soporta ``INSERT ... ON CONFLICT(...) DO UPDATE SET ...``
        desde 3.24. En el DO UPDATE, NO incluimos ``observaciones`` en
        el SET — así las anotaciones manuales del operador sobreviven
        la re-consolidación.
    """

    _SELECT_COLS = (
        "id, empleado_id, fecha, turno_id_aplicado, hora_entrada_real, "
        "hora_salida_real, estado, minutos_tarde, minutos_salida_temprana, "
        "observaciones, consolidada_en"
    )

    def __init__(self, database: Database) -> None:
        """Inicializa el repo con el adaptador de BD inyectado."""
        self._db = database

    # ── Read ──────────────────────────────────────────────────────────────

    def get_by_empleado_y_fecha(self, empleado_id: int, fecha: str) -> Optional[Asistencia]:
        with self._db.transaction() as conn:
            row: Optional[sqlite3.Row] = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM asistencias "
                "WHERE empleado_id = ? AND fecha = ?",
                (empleado_id, fecha),
            ).fetchone()
        return _row_to_asistencia(row) if row is not None else None

    def list_by_empleado_y_rango(
        self,
        empleado_id: int,
        desde: str,
        hasta: str,
        limit: Optional[int] = None,
    ) -> List[Asistencia]:
        # LIMIT como parámetro de SQL (no interpolación) — SQLite acepta
        # placeholder en LIMIT. Si `limit` es None, se omite la cláusula.
        sql = (
            f"SELECT {self._SELECT_COLS} FROM asistencias "
            "WHERE empleado_id = ? AND fecha >= ? AND fecha <= ? "
            "ORDER BY fecha ASC"
        )
        params: tuple[object, ...] = (empleado_id, desde, hasta)
        if limit is not None:
            sql += " LIMIT ?"
            params = params + (limit,)
        with self._db.transaction() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_asistencia(r) for r in rows]

    def list_by_fecha(self, fecha: str) -> List[Asistencia]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM asistencias "
                "WHERE fecha = ? ORDER BY empleado_id ASC",
                (fecha,),
            ).fetchall()
        return [_row_to_asistencia(r) for r in rows]

    def list_by_rango(
        self,
        desde: str,
        hasta: str,
        limit: Optional[int] = None,
    ) -> List[Asistencia]:
        sql = (
            f"SELECT {self._SELECT_COLS} FROM asistencias "
            "WHERE fecha >= ? AND fecha <= ? "
            "ORDER BY fecha ASC, empleado_id ASC"
        )
        params: tuple[object, ...] = (desde, hasta)
        if limit is not None:
            sql += " LIMIT ?"
            params = params + (limit,)
        with self._db.transaction() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_asistencia(r) for r in rows]

    def primera_fecha(self, empleado_id: Optional[int] = None) -> Optional[str]:
        sql = "SELECT MIN(fecha) AS primera FROM asistencias"
        params: tuple[object, ...] = ()
        if empleado_id is not None:
            sql += " WHERE empleado_id = ?"
            params = (empleado_id,)
        with self._db.transaction() as conn:
            row: Optional[sqlite3.Row] = conn.execute(sql, params).fetchone()
        return str(row["primera"]) if row is not None and row["primera"] is not None else None

    def count_by_rango(
        self,
        desde: str,
        hasta: str,
        empleado_id: Optional[int] = None,
    ) -> int:
        sql = "SELECT COUNT(*) AS total FROM asistencias " "WHERE fecha >= ? AND fecha <= ?"
        params: tuple[object, ...] = (desde, hasta)
        if empleado_id is not None:
            sql += " AND empleado_id = ?"
            params = params + (empleado_id,)
        with self._db.transaction() as conn:
            row: Optional[sqlite3.Row] = conn.execute(sql, params).fetchone()
        # COUNT(*) siempre devuelve una fila — defensa por si SQLite cambia.
        return int(row["total"]) if row is not None else 0

    # ── Write ─────────────────────────────────────────────────────────────

    def upsert(self, asistencia: Asistencia) -> Asistencia:
        # INSERT ... ON CONFLICT(empleado_id, fecha) DO UPDATE. Los campos
        # calculados se sobrescriben. `observaciones`: la nota que escribió
        # el operador se preserva siempre; si no hay nota o la que hay es la
        # automática de "salida asumida", se toma la nueva (así la nota
        # aparece cuando el día cierra sin segunda marca y se va si después
        # llega la marca de salida).
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO asistencias "
                "(empleado_id, fecha, turno_id_aplicado, hora_entrada_real, "
                " hora_salida_real, estado, minutos_tarde, "
                " minutos_salida_temprana, observaciones, consolidada_en) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(empleado_id, fecha) DO UPDATE SET "
                "    turno_id_aplicado = excluded.turno_id_aplicado, "
                "    hora_entrada_real = excluded.hora_entrada_real, "
                "    hora_salida_real = excluded.hora_salida_real, "
                "    estado = excluded.estado, "
                "    minutos_tarde = excluded.minutos_tarde, "
                "    minutos_salida_temprana = excluded.minutos_salida_temprana, "
                "    observaciones = CASE "
                "        WHEN asistencias.observaciones IS NULL "
                "          OR asistencias.observaciones = ? "
                "        THEN excluded.observaciones "
                "        ELSE asistencias.observaciones END, "
                "    consolidada_en = excluded.consolidada_en",
                (
                    asistencia.empleado_id,
                    asistencia.fecha,
                    asistencia.turno_id_aplicado,
                    asistencia.hora_entrada_real,
                    asistencia.hora_salida_real,
                    asistencia.estado,
                    asistencia.minutos_tarde,
                    asistencia.minutos_salida_temprana,
                    asistencia.observaciones,
                    asistencia.consolidada_en,
                    OBSERVACION_SALIDA_ASUMIDA,
                ),
            )
            # Recuperamos la fila resultante — sea el nuevo INSERT o el
            # UPDATE sobre una existente. Garantiza que el caller obtenga
            # el id + las observaciones preservadas (no las pasadas).
            row: Optional[sqlite3.Row] = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM asistencias "
                "WHERE empleado_id = ? AND fecha = ?",
                (asistencia.empleado_id, asistencia.fecha),
            ).fetchone()
        if row is None:
            # Defensivo: el INSERT OR CONFLICT acaba de correr, debería
            # existir. Si no, algo muy raro pasó.
            raise RuntimeError(
                "Asistencia insertada desapareció de inmediato — "
                "inconsistencia inesperada de BD."
            )
        return _row_to_asistencia(row)

    def update_observaciones(self, asistencia_id: int, observaciones: Optional[str]) -> None:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE asistencias SET observaciones = ? WHERE id = ?",
                (observaciones, asistencia_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"No existe asistencia con id={asistencia_id}.")
