"""Implementación SQLite del repositorio de Turno."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from typing import Dict, Iterable, List, Optional

from core.models.turno import HorarioTurno, Turno
from core.repositories.turno_repository import (
    ITurnoReadRepository,
    ITurnoWriteRepository,
)
from infrastructure.database.connection import Database


def _row_to_horario(row: sqlite3.Row) -> HorarioTurno:
    """Mapea una fila de ``turno_horarios`` al dataclass ``HorarioTurno``."""
    return HorarioTurno(
        dias_semana=row["dias_semana"],
        hora_entrada=row["hora_entrada"],
        hora_salida=row["hora_salida"],
        minutos_descanso=row["minutos_descanso"],
        cruza_medianoche=bool(row["cruza_medianoche"]),
    )


def _cargar_horarios(
    conn: sqlite3.Connection, turno_ids: Iterable[int]
) -> Dict[int, List[HorarioTurno]]:
    """``{turno_id: [horarios alternos]}`` en una sola consulta.

    Ordenados por el día más temprano de la semana (bit más alto primero),
    así "Sábado" aparece antes que "Domingo".
    """
    ids = list(turno_ids)
    if not ids:
        return {}
    marcadores = ", ".join("?" for _ in ids)
    rows = conn.execute(
        "SELECT turno_id, dias_semana, hora_entrada, hora_salida, minutos_descanso, "
        "cruza_medianoche FROM turno_horarios "
        f"WHERE turno_id IN ({marcadores}) ORDER BY turno_id, dias_semana DESC, id",
        ids,
    ).fetchall()
    resultado: Dict[int, List[HorarioTurno]] = {}
    for row in rows:
        resultado.setdefault(row["turno_id"], []).append(_row_to_horario(row))
    return resultado


def _con_horarios(conn: sqlite3.Connection, turnos: List[Turno]) -> List[Turno]:
    """Adjunta a cada turno sus horarios alternos."""
    horarios = _cargar_horarios(conn, (t.id for t in turnos if t.id is not None))
    for turno in turnos:
        if turno.id is not None:
            turno.horarios_extra = horarios.get(turno.id, [])
    return turnos


def _guardar_horarios(
    conn: sqlite3.Connection, turno_id: int, horarios: Iterable[HorarioTurno]
) -> None:
    """Reemplaza los horarios alternos del turno (borra y vuelve a insertar)."""
    conn.execute("DELETE FROM turno_horarios WHERE turno_id = ?", (turno_id,))
    for h in horarios:
        conn.execute(
            "INSERT INTO turno_horarios "
            "(turno_id, dias_semana, hora_entrada, hora_salida, minutos_descanso, "
            " cruza_medianoche) VALUES (?, ?, ?, ?, ?, ?)",
            (
                turno_id,
                h.dias_semana,
                h.hora_entrada,
                h.hora_salida,
                h.minutos_descanso,
                1 if h.cruza_medianoche else 0,
            ),
        )


def _row_to_turno(row: sqlite3.Row) -> Turno:
    """Mapea una fila de ``turnos`` al dataclass ``Turno``."""
    return Turno(
        id=row["id"],
        nombre=row["nombre"],
        hora_entrada=row["hora_entrada"],
        hora_salida=row["hora_salida"],
        minutos_descanso=row["minutos_descanso"],
        dias_semana=row["dias_semana"],
        cruza_medianoche=bool(row["cruza_medianoche"]),
        is_active=bool(row["is_active"]),
    )


class TurnoRepositorySQLite(ITurnoReadRepository, ITurnoWriteRepository):
    """Implementación SQLite — Opción A: una conexión por operación."""

    _SELECT_COLS = (
        "id, nombre, hora_entrada, hora_salida, minutos_descanso, "
        "dias_semana, cruza_medianoche, is_active"
    )

    def __init__(self, database: Database) -> None:
        """Inicializa el repo con el adaptador de BD inyectado."""
        self._db = database

    # ── Read ──────────────────────────────────────────────────────────────

    def get_by_id(self, turno_id: int) -> Optional[Turno]:
        with self._db.transaction() as conn:
            row: Optional[sqlite3.Row] = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM turnos WHERE id = ?",
                (turno_id,),
            ).fetchone()
            if row is None:
                return None
            return _con_horarios(conn, [_row_to_turno(row)])[0]

    def get_by_nombre(self, nombre: str) -> Optional[Turno]:
        with self._db.transaction() as conn:
            row: Optional[sqlite3.Row] = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM turnos WHERE nombre = ?",
                (nombre,),
            ).fetchone()
            if row is None:
                return None
            return _con_horarios(conn, [_row_to_turno(row)])[0]

    def list_all(self) -> List[Turno]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM turnos ORDER BY nombre ASC"
            ).fetchall()
            return _con_horarios(conn, [_row_to_turno(r) for r in rows])

    def list_active(self) -> List[Turno]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM turnos " "WHERE is_active = 1 ORDER BY nombre ASC"
            ).fetchall()
            return _con_horarios(conn, [_row_to_turno(r) for r in rows])

    # ── Write ─────────────────────────────────────────────────────────────

    def create(self, turno: Turno) -> Turno:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO turnos "
                "(nombre, hora_entrada, hora_salida, minutos_descanso, "
                " dias_semana, cruza_medianoche, is_active) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    turno.nombre,
                    turno.hora_entrada,
                    turno.hora_salida,
                    turno.minutos_descanso,
                    turno.dias_semana,
                    1 if turno.cruza_medianoche else 0,
                    1 if turno.is_active else 0,
                ),
            )
            new_id = cursor.lastrowid
            assert new_id is not None
            _guardar_horarios(conn, new_id, turno.horarios_extra)
        return replace(turno, id=new_id, horarios_extra=list(turno.horarios_extra))

    def update(self, turno: Turno) -> None:
        if turno.id is None:
            raise ValueError("No se puede actualizar un Turno sin id asignado.")
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE turnos SET "
                "nombre = ?, hora_entrada = ?, hora_salida = ?, "
                "minutos_descanso = ?, dias_semana = ?, "
                "cruza_medianoche = ?, is_active = ? "
                "WHERE id = ?",
                (
                    turno.nombre,
                    turno.hora_entrada,
                    turno.hora_salida,
                    turno.minutos_descanso,
                    turno.dias_semana,
                    1 if turno.cruza_medianoche else 0,
                    1 if turno.is_active else 0,
                    turno.id,
                ),
            )
            _guardar_horarios(conn, turno.id, turno.horarios_extra)

    def archive(self, turno_id: int) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE turnos SET is_active = 0 WHERE id = ?",
                (turno_id,),
            )

    def unarchive(self, turno_id: int) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE turnos SET is_active = 1 WHERE id = ?",
                (turno_id,),
            )
