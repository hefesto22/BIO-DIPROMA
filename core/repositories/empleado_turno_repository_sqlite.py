"""Implementación SQLite del repositorio de EmpleadoTurno."""

from __future__ import annotations

import sqlite3
from typing import List, Optional

from core.models.empleado_turno import EmpleadoTurno
from core.repositories.empleado_turno_repository import (
    IEmpleadoTurnoReadRepository,
    IEmpleadoTurnoWriteRepository,
)
from infrastructure.database.connection import Database


def _row_to_empleado_turno(row: sqlite3.Row) -> EmpleadoTurno:
    """Mapea una fila de ``empleado_turnos`` al dataclass ``EmpleadoTurno``."""
    return EmpleadoTurno(
        id=row["id"],
        empleado_id=row["empleado_id"],
        turno_id=row["turno_id"],
        fecha_inicio=row["fecha_inicio"],
        fecha_fin=row["fecha_fin"],
    )


class EmpleadoTurnoRepositorySQLite(IEmpleadoTurnoReadRepository, IEmpleadoTurnoWriteRepository):
    """Implementación SQLite — Opción A: una conexión por operación.

    Nota sobre atomicidad:
        ``cerrar_vigente_y_asignar`` corre UPDATE + INSERT dentro de una
        sola transacción (``Database.transaction()`` hace commit al final
        del bloque y rollback ante excepción). Si el INSERT falla (por
        ejemplo FK inválida), el UPDATE se revierte y el estado anterior
        queda intacto.
    """

    _SELECT_COLS = "id, empleado_id, turno_id, fecha_inicio, fecha_fin"

    def __init__(self, database: Database) -> None:
        """Inicializa el repo con el adaptador de BD inyectado."""
        self._db = database

    # ── Read ──────────────────────────────────────────────────────────────

    def get_vigente(self, empleado_id: int) -> Optional[EmpleadoTurno]:
        # Sub-3.2.B: ahora puede haber varias vigentes (días disjuntos).
        # Devolvemos la primera por fecha_inicio ASC (estable y predecible).
        with self._db.transaction() as conn:
            row: Optional[sqlite3.Row] = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM empleado_turnos "
                "WHERE empleado_id = ? AND fecha_fin IS NULL "
                "ORDER BY fecha_inicio ASC, id ASC LIMIT 1",
                (empleado_id,),
            ).fetchone()
        return _row_to_empleado_turno(row) if row is not None else None

    def list_vigentes(self, empleado_id: int) -> List[EmpleadoTurno]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM empleado_turnos "
                "WHERE empleado_id = ? AND fecha_fin IS NULL "
                "ORDER BY fecha_inicio ASC, id ASC",
                (empleado_id,),
            ).fetchall()
        return [_row_to_empleado_turno(r) for r in rows]

    def list_historial(self, empleado_id: int) -> List[EmpleadoTurno]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM empleado_turnos "
                "WHERE empleado_id = ? "
                "ORDER BY fecha_inicio DESC, id DESC",
                (empleado_id,),
            ).fetchall()
        return [_row_to_empleado_turno(r) for r in rows]

    def list_by_turno(self, turno_id: int) -> List[EmpleadoTurno]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM empleado_turnos "
                "WHERE turno_id = ? "
                "ORDER BY fecha_inicio DESC, id DESC",
                (turno_id,),
            ).fetchall()
        return [_row_to_empleado_turno(r) for r in rows]

    # ── Write ─────────────────────────────────────────────────────────────

    def asignar(self, empleado_id: int, turno_id: int, fecha_inicio: str) -> EmpleadoTurno:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO empleado_turnos "
                "(empleado_id, turno_id, fecha_inicio, fecha_fin) "
                "VALUES (?, ?, ?, NULL)",
                (empleado_id, turno_id, fecha_inicio),
            )
            new_id = cursor.lastrowid
        return EmpleadoTurno(
            id=new_id,
            empleado_id=empleado_id,
            turno_id=turno_id,
            fecha_inicio=fecha_inicio,
            fecha_fin=None,
        )

    def cerrar_asignacion_por_id(self, asignacion_id: int, fecha_fin: str) -> None:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE empleado_turnos SET fecha_fin = ? " "WHERE id = ? AND fecha_fin IS NULL",
                (fecha_fin, asignacion_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"La asignación {asignacion_id} no existe o ya está cerrada.")

    def cerrar_vigente(self, empleado_id: int, fecha_fin: str) -> None:
        # Un solo UPDATE con rowcount check. Si el empleado no tiene
        # vigente, rowcount == 0 → ValueError (semántica consistente con
        # cerrar_vigente_y_asignar). Si el CHECK de fechas de la tabla
        # falla (fecha_fin < fecha_inicio), SQLite levanta IntegrityError
        # y se propaga sin masking.
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE empleado_turnos SET fecha_fin = ? "
                "WHERE empleado_id = ? AND fecha_fin IS NULL",
                (fecha_fin, empleado_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(
                    f"El empleado {empleado_id} no tiene asignación vigente " "que cerrar."
                )

    def cerrar_vigente_y_asignar(
        self,
        empleado_id: int,
        turno_id_nuevo: int,
        fecha_fin_vigente: str,
        fecha_inicio_nueva: str,
    ) -> EmpleadoTurno:
        # Ambas ops dentro de la misma transacción. Si el INSERT falla,
        # el rollback revierte el UPDATE automáticamente.
        with self._db.transaction() as conn:
            cursor_update = conn.execute(
                "UPDATE empleado_turnos SET fecha_fin = ? "
                "WHERE empleado_id = ? AND fecha_fin IS NULL",
                (fecha_fin_vigente, empleado_id),
            )
            if cursor_update.rowcount == 0:
                # No había vigente — este método no aplica. Lanzar ValueError
                # para que el servicio pueda distinguir entre "no hay vigente"
                # y errores de integridad.
                raise ValueError(
                    f"El empleado {empleado_id} no tiene asignación vigente. "
                    "Usar asignar() directamente."
                )
            cursor_insert = conn.execute(
                "INSERT INTO empleado_turnos "
                "(empleado_id, turno_id, fecha_inicio, fecha_fin) "
                "VALUES (?, ?, ?, NULL)",
                (empleado_id, turno_id_nuevo, fecha_inicio_nueva),
            )
            new_id = cursor_insert.lastrowid
        return EmpleadoTurno(
            id=new_id,
            empleado_id=empleado_id,
            turno_id=turno_id_nuevo,
            fecha_inicio=fecha_inicio_nueva,
            fecha_fin=None,
        )
