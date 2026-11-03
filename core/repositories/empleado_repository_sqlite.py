"""Implementación SQLite del repositorio de Empleado."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import List, Optional

from core.models.empleado import Empleado
from core.repositories.empleado_repository import (
    IEmpleadoReadRepository,
    IEmpleadoWriteRepository,
)
from infrastructure.database.connection import Database


def _row_to_empleado(row: sqlite3.Row) -> Empleado:
    """Mapea una fila de ``empleados`` al dataclass ``Empleado``."""
    return Empleado(
        id=row["id"],
        dni=row["dni"],
        nombres=row["nombres"],
        apellidos=row["apellidos"],
        departamento_id=row["departamento_id"],
        cargo_id=row["cargo_id"],
        fecha_ingreso=row["fecha_ingreso"],
        telefono=row["telefono"],
        email=row["email"],
        zkteco_id=row["zkteco_id"],
        is_active=bool(row["is_active"]),
        fecha_baja=row["fecha_baja"],
        motivo_baja=row["motivo_baja"],
        nota_baja=row["nota_baja"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _utc_now_iso() -> str:
    """Timestamp UTC ISO-8601 con precisión de segundos."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class EmpleadoRepositorySQLite(IEmpleadoReadRepository, IEmpleadoWriteRepository):
    """Implementación SQLite — Opción A: una conexión por operación."""

    _SELECT_COLS = (
        "id, dni, nombres, apellidos, departamento_id, cargo_id, "
        "fecha_ingreso, telefono, email, zkteco_id, is_active, "
        "fecha_baja, motivo_baja, nota_baja, created_at, updated_at"
    )

    # Orden canónico de listados — el servicio puede re-ordenar en memoria
    # si necesita otra vista, pero la BD siempre devuelve esto por default.
    _ORDER_BY = "ORDER BY apellidos ASC, nombres ASC"

    def __init__(self, database: Database) -> None:
        """Inicializa el repo con el adaptador de BD inyectado."""
        self._db = database

    # ── Read ──────────────────────────────────────────────────────────────

    def get_by_id(self, empleado_id: int) -> Optional[Empleado]:
        with self._db.transaction() as conn:
            row: Optional[sqlite3.Row] = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM empleados WHERE id = ?",
                (empleado_id,),
            ).fetchone()
        return _row_to_empleado(row) if row is not None else None

    def get_by_dni(self, dni: str) -> Optional[Empleado]:
        with self._db.transaction() as conn:
            row: Optional[sqlite3.Row] = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM empleados WHERE dni = ?",
                (dni,),
            ).fetchone()
        return _row_to_empleado(row) if row is not None else None

    def get_by_zkteco_id(self, zkteco_id: int) -> Optional[Empleado]:
        with self._db.transaction() as conn:
            row: Optional[sqlite3.Row] = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM empleados WHERE zkteco_id = ?",
                (zkteco_id,),
            ).fetchone()
        return _row_to_empleado(row) if row is not None else None

    def list_all(self) -> List[Empleado]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM empleados {self._ORDER_BY}"
            ).fetchall()
        return [_row_to_empleado(r) for r in rows]

    def list_active(self) -> List[Empleado]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM empleados "
                f"WHERE is_active = 1 {self._ORDER_BY}"
            ).fetchall()
        return [_row_to_empleado(r) for r in rows]

    def list_by_departamento(
        self, departamento_id: int, solo_activos: bool = True
    ) -> List[Empleado]:
        where = "WHERE departamento_id = ?"
        params: tuple[object, ...] = (departamento_id,)
        if solo_activos:
            where += " AND is_active = 1"
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM empleados " f"{where} {self._ORDER_BY}",
                params,
            ).fetchall()
        return [_row_to_empleado(r) for r in rows]

    def list_by_cargo(self, cargo_id: int, solo_activos: bool = True) -> List[Empleado]:
        where = "WHERE cargo_id = ?"
        params: tuple[object, ...] = (cargo_id,)
        if solo_activos:
            where += " AND is_active = 1"
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM empleados " f"{where} {self._ORDER_BY}",
                params,
            ).fetchall()
        return [_row_to_empleado(r) for r in rows]

    # ── Write ─────────────────────────────────────────────────────────────

    def create(self, empleado: Empleado) -> Empleado:
        now = _utc_now_iso()
        created_at = empleado.created_at or now
        updated_at = empleado.updated_at or now
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO empleados "
                "(dni, nombres, apellidos, departamento_id, cargo_id, "
                " fecha_ingreso, telefono, email, zkteco_id, is_active, "
                " fecha_baja, motivo_baja, nota_baja, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    empleado.dni,
                    empleado.nombres,
                    empleado.apellidos,
                    empleado.departamento_id,
                    empleado.cargo_id,
                    empleado.fecha_ingreso,
                    empleado.telefono,
                    empleado.email,
                    empleado.zkteco_id,
                    1 if empleado.is_active else 0,
                    empleado.fecha_baja,
                    empleado.motivo_baja,
                    empleado.nota_baja,
                    created_at,
                    updated_at,
                ),
            )
            new_id = cursor.lastrowid
        return Empleado(
            id=new_id,
            dni=empleado.dni,
            nombres=empleado.nombres,
            apellidos=empleado.apellidos,
            departamento_id=empleado.departamento_id,
            cargo_id=empleado.cargo_id,
            fecha_ingreso=empleado.fecha_ingreso,
            telefono=empleado.telefono,
            email=empleado.email,
            zkteco_id=empleado.zkteco_id,
            is_active=empleado.is_active,
            fecha_baja=empleado.fecha_baja,
            motivo_baja=empleado.motivo_baja,
            nota_baja=empleado.nota_baja,
            created_at=created_at,
            updated_at=updated_at,
        )

    def update(self, empleado: Empleado) -> None:
        if empleado.id is None:
            raise ValueError("No se puede actualizar un Empleado sin id asignado.")
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE empleados SET "
                "dni = ?, nombres = ?, apellidos = ?, "
                "departamento_id = ?, cargo_id = ?, "
                "fecha_ingreso = ?, telefono = ?, email = ?, "
                "zkteco_id = ?, updated_at = ? "
                "WHERE id = ?",
                (
                    empleado.dni,
                    empleado.nombres,
                    empleado.apellidos,
                    empleado.departamento_id,
                    empleado.cargo_id,
                    empleado.fecha_ingreso,
                    empleado.telefono,
                    empleado.email,
                    empleado.zkteco_id,
                    _utc_now_iso(),
                    empleado.id,
                ),
            )

    def deactivate(
        self,
        empleado_id: int,
        fecha_baja: str,
        motivo_baja: str,
        nota_baja: Optional[str],
    ) -> None:
        # Los cinco campos (is_active + trío de baja + updated_at) se actualizan
        # en UNA sola sentencia para que el CHECK de coherencia vea el estado
        # final consistente, nunca un paso intermedio.
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE empleados SET "
                "is_active = 0, fecha_baja = ?, motivo_baja = ?, "
                "nota_baja = ?, updated_at = ? "
                "WHERE id = ?",
                (
                    fecha_baja,
                    motivo_baja,
                    nota_baja,
                    _utc_now_iso(),
                    empleado_id,
                ),
            )

    def reactivate(self, empleado_id: int) -> None:
        # Limpia el trío de baja simultáneamente con is_active=1 para que
        # el CHECK vea un estado coherente.
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE empleados SET "
                "is_active = 1, fecha_baja = NULL, motivo_baja = NULL, "
                "nota_baja = NULL, updated_at = ? "
                "WHERE id = ?",
                (_utc_now_iso(), empleado_id),
            )
