"""Implementación SQLite del repositorio de Usuario."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import List, Optional

from core.models.usuario import Usuario
from core.repositories.usuario_repository import (
    IUsuarioReadRepository,
    IUsuarioWriteRepository,
)
from infrastructure.database.connection import Database


def _row_to_usuario(row: sqlite3.Row) -> Usuario:
    """Mapea una fila de ``usuarios`` al dataclass ``Usuario``."""
    return Usuario(
        id=row["id"],
        username=row["username"],
        password_hash=row["password_hash"],
        full_name=row["full_name"],
        role_id=row["role_id"],
        is_active=bool(row["is_active"]),
        failed_attempts=row["failed_attempts"],
        locked_until=row["locked_until"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _utc_now_iso() -> str:
    """Timestamp UTC ISO-8601 con precisión de segundos."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class UsuarioRepositorySQLite(IUsuarioReadRepository, IUsuarioWriteRepository):
    """Implementación SQLite — Opción A: una conexión por operación.

    Esta clase implementa ambas interfaces (read + write) por comodidad.
    Los consumidores que solo necesiten una deben recibir el tipo más
    estrecho en su constructor (ISP — el ``.pyi`` o el type hint del
    servicio se encarga de "estrechar" al contrato correcto).
    """

    _SELECT_COLS = (
        "id, username, password_hash, full_name, role_id, "
        "is_active, failed_attempts, locked_until, created_at, updated_at"
    )

    def __init__(self, database: Database) -> None:
        """Inicializa el repo con el adaptador de BD inyectado."""
        self._db = database

    # ── Read ──────────────────────────────────────────────────────────────

    def get_by_id(self, user_id: int) -> Optional[Usuario]:
        with self._db.transaction() as conn:
            row: Optional[sqlite3.Row] = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM usuarios WHERE id = ?",
                (user_id,),
            ).fetchone()
        return _row_to_usuario(row) if row is not None else None

    def get_by_username(self, username: str) -> Optional[Usuario]:
        with self._db.transaction() as conn:
            row: Optional[sqlite3.Row] = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM usuarios WHERE username = ?",
                (username,),
            ).fetchone()
        return _row_to_usuario(row) if row is not None else None

    def list_all(self) -> List[Usuario]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM usuarios ORDER BY username ASC"
            ).fetchall()
        return [_row_to_usuario(r) for r in rows]

    def count_by_role(self, role_id: int) -> int:
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM usuarios WHERE role_id = ?",
                (role_id,),
            ).fetchone()
        # row siempre existe para COUNT(*) — el cast explícito satisface mypy.
        return int(row["c"])

    # ── Write ─────────────────────────────────────────────────────────────

    def create(self, usuario: Usuario) -> Usuario:
        now = _utc_now_iso()
        created_at = usuario.created_at or now
        updated_at = usuario.updated_at or now
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO usuarios "
                "(username, password_hash, full_name, role_id, is_active, "
                " failed_attempts, locked_until, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    usuario.username,
                    usuario.password_hash,
                    usuario.full_name,
                    usuario.role_id,
                    1 if usuario.is_active else 0,
                    usuario.failed_attempts,
                    usuario.locked_until,
                    created_at,
                    updated_at,
                ),
            )
            new_id = cursor.lastrowid
        # Instancia nueva (Usuario no es frozen pero devolvemos una copia
        # limpia para que el caller no retenga una referencia al original).
        return Usuario(
            id=new_id,
            username=usuario.username,
            password_hash=usuario.password_hash,
            full_name=usuario.full_name,
            role_id=usuario.role_id,
            is_active=usuario.is_active,
            failed_attempts=usuario.failed_attempts,
            locked_until=usuario.locked_until,
            created_at=created_at,
            updated_at=updated_at,
        )

    def update_login_state(
        self,
        user_id: int,
        failed_attempts: int,
        locked_until: Optional[str],
    ) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE usuarios SET "
                "failed_attempts = ?, locked_until = ?, updated_at = ? "
                "WHERE id = ?",
                (failed_attempts, locked_until, _utc_now_iso(), user_id),
            )

    def update_password_hash(self, user_id: int, new_hash: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE usuarios SET password_hash = ?, updated_at = ? WHERE id = ?",
                (new_hash, _utc_now_iso(), user_id),
            )

    def delete(self, user_id: int) -> None:
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM usuarios WHERE id = ?", (user_id,))

    def update_profile(
        self,
        user_id: int,
        full_name: str,
        role_id: int,
        is_active: bool,
    ) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE usuarios SET "
                "full_name = ?, role_id = ?, is_active = ?, updated_at = ? "
                "WHERE id = ?",
                (
                    full_name,
                    role_id,
                    1 if is_active else 0,
                    _utc_now_iso(),
                    user_id,
                ),
            )

    def unlock_account(self, user_id: int) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE usuarios SET "
                "failed_attempts = 0, locked_until = NULL, updated_at = ? "
                "WHERE id = ?",
                (_utc_now_iso(), user_id),
            )
