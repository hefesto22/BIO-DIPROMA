"""Implementación SQLite del repositorio de Dispositivo."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from typing import List, Optional

from core.models.dispositivo import Dispositivo
from core.repositories.dispositivo_repository import (
    IDispositivoReadRepository,
    IDispositivoWriteRepository,
)
from infrastructure.database.connection import Database


def _row_to_dispositivo(row: sqlite3.Row) -> Dispositivo:
    """Mapea una fila de ``dispositivos`` al dataclass ``Dispositivo``."""
    return Dispositivo(
        id=row["id"],
        nombre=row["nombre"],
        ip=row["ip"],
        puerto=row["puerto"],
        is_active=bool(row["is_active"]),
        modo=row["modo"],
        clave_comunicacion=row["clave_comunicacion"],
        usar_udp=bool(row["usar_udp"]),
        numero_serie=row["numero_serie"],
        push_stamp=row["push_stamp"],
        ultimo_contacto=row["ultimo_contacto"],
    )


class DispositivoRepositorySQLite(IDispositivoReadRepository, IDispositivoWriteRepository):
    """Implementación SQLite — Opción A: una conexión por operación.

    Esta clase implementa ambas interfaces (read + write). Los consumidores
    que solo necesiten una deben tipar el parámetro del constructor con
    la interfaz más estrecha (ISP).
    """

    _SELECT_COLS = (
        "id, nombre, ip, puerto, is_active, modo, clave_comunicacion, usar_udp, "
        "numero_serie, push_stamp, ultimo_contacto"
    )
    _ORDER_BY = "ORDER BY nombre ASC"

    def __init__(self, database: Database) -> None:
        """Inicializa el repo con el adaptador de BD inyectado."""
        self._db = database

    # ── Read ──────────────────────────────────────────────────────────────

    def get_by_id(self, dispositivo_id: int) -> Optional[Dispositivo]:
        with self._db.transaction() as conn:
            row: Optional[sqlite3.Row] = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM dispositivos WHERE id = ?",
                (dispositivo_id,),
            ).fetchone()
        return _row_to_dispositivo(row) if row is not None else None

    def get_by_nombre(self, nombre: str) -> Optional[Dispositivo]:
        with self._db.transaction() as conn:
            row: Optional[sqlite3.Row] = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM dispositivos WHERE nombre = ?",
                (nombre,),
            ).fetchone()
        return _row_to_dispositivo(row) if row is not None else None

    def get_by_ip_puerto(self, ip: str, puerto: int) -> Optional[Dispositivo]:
        with self._db.transaction() as conn:
            row: Optional[sqlite3.Row] = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM dispositivos " "WHERE ip = ? AND puerto = ?",
                (ip, puerto),
            ).fetchone()
        return _row_to_dispositivo(row) if row is not None else None

    def get_by_numero_serie(self, numero_serie: str) -> Optional[Dispositivo]:
        with self._db.transaction() as conn:
            row: Optional[sqlite3.Row] = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM dispositivos WHERE numero_serie = ?",
                (numero_serie,),
            ).fetchone()
        return _row_to_dispositivo(row) if row is not None else None

    def list_all(self) -> List[Dispositivo]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM dispositivos {self._ORDER_BY}"
            ).fetchall()
        return [_row_to_dispositivo(r) for r in rows]

    def list_active(self) -> List[Dispositivo]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM dispositivos "
                f"WHERE is_active = 1 {self._ORDER_BY}"
            ).fetchall()
        return [_row_to_dispositivo(r) for r in rows]

    # ── Write ─────────────────────────────────────────────────────────────

    def create(self, dispositivo: Dispositivo) -> Dispositivo:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO dispositivos (nombre, ip, puerto, is_active, modo, "
                "clave_comunicacion, usar_udp, numero_serie) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    dispositivo.nombre,
                    dispositivo.ip,
                    dispositivo.puerto,
                    1 if dispositivo.is_active else 0,
                    dispositivo.modo,
                    dispositivo.clave_comunicacion,
                    1 if dispositivo.usar_udp else 0,
                    dispositivo.numero_serie,
                ),
            )
            new_id = cursor.lastrowid
        return replace(dispositivo, id=new_id)

    def update(self, dispositivo: Dispositivo) -> None:
        if dispositivo.id is None:
            raise ValueError("No se puede actualizar un Dispositivo sin id asignado.")
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE dispositivos SET nombre = ?, ip = ?, puerto = ?, modo = ?, "
                "clave_comunicacion = ?, usar_udp = ?, numero_serie = ? WHERE id = ?",
                (
                    dispositivo.nombre,
                    dispositivo.ip,
                    dispositivo.puerto,
                    dispositivo.modo,
                    dispositivo.clave_comunicacion,
                    1 if dispositivo.usar_udp else 0,
                    dispositivo.numero_serie,
                    dispositivo.id,
                ),
            )

    def registrar_contacto(
        self, dispositivo_id: int, cuando: str, push_stamp: Optional[str] = None
    ) -> None:
        with self._db.transaction() as conn:
            if push_stamp is None:
                conn.execute(
                    "UPDATE dispositivos SET ultimo_contacto = ? WHERE id = ?",
                    (cuando, dispositivo_id),
                )
            else:
                conn.execute(
                    "UPDATE dispositivos SET ultimo_contacto = ?, push_stamp = ? WHERE id = ?",
                    (cuando, push_stamp, dispositivo_id),
                )

    def archive(self, dispositivo_id: int) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE dispositivos SET is_active = 0 WHERE id = ?",
                (dispositivo_id,),
            )

    def unarchive(self, dispositivo_id: int) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE dispositivos SET is_active = 1 WHERE id = ?",
                (dispositivo_id,),
            )
