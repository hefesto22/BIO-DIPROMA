"""Tests del script ``bin.recover_superadmin`` (Sub-3.1).

Cubre:
    - Resetea la password del SUPERADMIN y destraba lockout.
    - Aborta si no hay SUPERADMIN en la BD.
    - Aborta si el operador no confirma con el username correcto.
    - Rechaza passwords débiles según ``PasswordPolicy``.
    - Audita la operación.
    - ``--db-path`` apunta a otra BD distinta de la default.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Iterator, Optional

import pytest

from bin import recover_superadmin as recovery
from core.models.usuario import Usuario
from core.repositories.audit_log_repository_sqlite import AuditLogRepositorySQLite
from core.repositories.usuario_repository_sqlite import UsuarioRepositorySQLite
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner
from infrastructure.security.bcrypt_hasher import BcryptHasher

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "infrastructure" / "database" / "migrations"

ROLE_ID_SUPERADMIN = 1


# ── Fakes / helpers ──────────────────────────────────────────────────────────


class _FakeHasher(BcryptHasher):
    """Hasher determinístico — evita pagar 12 rounds en cada test."""

    def __init__(self) -> None:
        self._cost_factor = 12
        self._log = logging.getLogger(self.__class__.__name__)

    def hash(self, password: str) -> str:
        return f"fake-hash-of-{password}"

    def verify(self, password: str, password_hash: str) -> bool:
        return password_hash == f"fake-hash-of-{password}"


def _crear_db_con_superadmin(
    tmp_path: Path,
    *,
    failed_attempts: int = 0,
    locked_until: Optional[str] = None,
) -> Path:
    """Crea una BD con un SUPERADMIN sembrado y devuelve su ruta."""
    db_path = tmp_path / "zkteco_app.db"
    database = Database(db_path)
    MigrationsRunner(database, MIGRATIONS_DIR).run()

    repo = UsuarioRepositorySQLite(database)
    repo.create(
        Usuario(
            id=None,
            username="admin",
            password_hash="fake-hash-of-Original123",
            full_name="Administrador",
            role_id=ROLE_ID_SUPERADMIN,
            is_active=True,
            failed_attempts=failed_attempts,
            locked_until=locked_until,
        )
    )
    return db_path


def _crear_db_sin_superadmin(tmp_path: Path) -> Path:
    """Crea una BD vacía (sin usuarios) — para validar el caso de error."""
    db_path = tmp_path / "zkteco_app.db"
    database = Database(db_path)
    MigrationsRunner(database, MIGRATIONS_DIR).run()
    return db_path


def _stub_inputs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    confirmacion: str,
    passwords: Iterable[str],
) -> None:
    """Cablea ``input``, ``getpass.getpass`` y el hasher real → fake."""
    pwd_iter: Iterator[str] = iter(passwords)
    monkeypatch.setattr("builtins.input", lambda _prompt="": confirmacion)
    monkeypatch.setattr(
        "bin.recover_superadmin.getpass.getpass",
        lambda _prompt="": next(pwd_iter),
    )
    monkeypatch.setattr(recovery, "BcryptHasher", lambda _cost: _FakeHasher())


# ── Casos felices ────────────────────────────────────────────────────────────


def test_recovery_actualiza_password_y_destraba_cuenta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sub-3.1: el SUPERADMIN bloqueado por intentos fallidos se resetea
    y queda con una nueva password verificable."""
    db_path = _crear_db_con_superadmin(
        tmp_path, failed_attempts=5, locked_until="2099-01-01T00:00:00"
    )
    _stub_inputs(
        monkeypatch,
        confirmacion="admin",
        passwords=["NuevaPass123", "NuevaPass123"],
    )

    exit_code = recovery.main(["--db-path", str(db_path)])

    assert exit_code == 0
    repo = UsuarioRepositorySQLite(Database(db_path))
    usuario = repo.get_by_username("admin")
    assert usuario is not None
    hasher = _FakeHasher()
    assert hasher.verify("NuevaPass123", usuario.password_hash)
    assert usuario.failed_attempts == 0
    assert usuario.locked_until is None


def test_recovery_audita_la_operacion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = _crear_db_con_superadmin(tmp_path)
    _stub_inputs(
        monkeypatch,
        confirmacion="admin",
        passwords=["NuevaPass123", "NuevaPass123"],
    )

    recovery.main(["--db-path", str(db_path)])

    audit = AuditLogRepositorySQLite(Database(db_path))
    entradas = audit.list_recent(limit=20)
    actions = [e.action for e in entradas]
    assert "superadmin_recovered" in actions


# ── Casos de error ───────────────────────────────────────────────────────────


def test_recovery_aborta_si_no_hay_superadmin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = _crear_db_sin_superadmin(tmp_path)
    _stub_inputs(monkeypatch, confirmacion="cualquiera", passwords=["x", "x"])

    exit_code = recovery.main(["--db-path", str(db_path)])

    assert exit_code == 3


def test_recovery_aborta_si_operador_no_confirma_username(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Si el operador escribe otro username, el script cancela sin tocar la BD."""
    db_path = _crear_db_con_superadmin(tmp_path)
    _stub_inputs(
        monkeypatch,
        confirmacion="otro_usuario",
        passwords=["NuevaPass123", "NuevaPass123"],
    )

    exit_code = recovery.main(["--db-path", str(db_path)])

    assert exit_code == 0  # cancelación voluntaria
    repo = UsuarioRepositorySQLite(Database(db_path))
    usuario = repo.get_by_username("admin")
    assert usuario is not None
    # El hash no cambió.
    assert usuario.password_hash == "fake-hash-of-Original123"


def test_recovery_rechaza_password_debil(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Una password sin dígitos (default policy require_digit=True) se rechaza."""
    db_path = _crear_db_con_superadmin(tmp_path)
    _stub_inputs(
        monkeypatch,
        confirmacion="admin",
        passwords=["sololetras", "sololetras"],
    )

    exit_code = recovery.main(["--db-path", str(db_path)])

    assert exit_code == 4
    # La password no cambió.
    repo = UsuarioRepositorySQLite(Database(db_path))
    usuario = repo.get_by_username("admin")
    assert usuario is not None
    assert usuario.password_hash == "fake-hash-of-Original123"


# ── Argument parsing ─────────────────────────────────────────────────────────


def test_parse_args_db_path_opcional() -> None:
    ns_con = recovery._parse_args(["--db-path", "foo.db"])
    assert ns_con.db_path == "foo.db"
    ns_sin = recovery._parse_args([])
    assert ns_sin.db_path is None


def test_resolver_db_path_devuelve_config_default_si_es_none() -> None:
    import config

    resuelto = recovery._resolver_db_path(None)
    assert resuelto == config.DATABASE_PATH


def test_resolver_db_path_resuelve_path_explicito() -> None:
    resuelto = recovery._resolver_db_path("dist/zkteco/data/zkteco_app.db")
    assert resuelto.is_absolute()
    assert resuelto.name == "zkteco_app.db"
