"""Tests del TurnoService.

Integración con repos SQLite reales + AuditLogger real. Cubre CRUD de
turnos, detección automática de ``cruza_medianoche``, validaciones de
bitmask/horas/descanso, archivar y reactivar.
"""

from __future__ import annotations

from pathlib import Path
from typing import Tuple

import pytest

from core.models.turno import (
    DIAS_LABORALES,
    DIAS_TODA_LA_SEMANA,
)
from core.repositories.audit_log_repository_sqlite import (
    AuditLogRepositorySQLite,
)
from core.repositories.turno_repository_sqlite import TurnoRepositorySQLite
from core.services.audit_logger import AuditLogger
from core.services.errors import (
    DuplicateTurnoNombreError,
    InvalidBitmaskError,
    InvalidDescansoError,
    InvalidTimeError,
    MissingRequiredFieldError,
    TurnoNotFoundError,
)
from core.services.turno_service import TurnoService
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "infrastructure" / "database" / "migrations"

ACTOR_ID = 1


def _seed_actor_user(db: Database, user_id: int = ACTOR_ID) -> None:
    """Seed de un usuario dummy que satisface la FK ``audit_log.user_id``."""
    with db.transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO usuarios "
            "(id, username, password_hash, full_name, role_id, created_at, updated_at) "
            "VALUES (?, 'test_actor', 'hash', 'Test Actor', 2, "
            "'2024-01-01T00:00:00', '2024-01-01T00:00:00')",
            (user_id,),
        )


@pytest.fixture
def setup(tmp_path: Path) -> Tuple[TurnoService, Database]:
    db = Database(tmp_path / "test_turno.db")
    MigrationsRunner(db, MIGRATIONS_DIR).run()
    _seed_actor_user(db)
    service = TurnoService(
        turno_read=TurnoRepositorySQLite(db),
        turno_write=TurnoRepositorySQLite(db),
        audit_logger=AuditLogger(AuditLogRepositorySQLite(db), machine_name="test"),
    )
    return service, db


def _count_audit(db: Database, action: str) -> int:
    with db.transaction() as conn:
        cur = conn.execute("SELECT COUNT(*) AS n FROM audit_log WHERE action = ?", (action,))
        return int(cur.fetchone()["n"])


# ── create_turno ──────────────────────────────────────────────────────────────


def test_create_turno_diurno_ok(setup: Tuple[TurnoService, Database]) -> None:
    service, db = setup
    t = service.create_turno(
        nombre="Oficina",
        hora_entrada="08:00",
        hora_salida="17:00",
        minutos_descanso=60,
        dias_semana=DIAS_LABORALES,
        actor_user_id=ACTOR_ID,
    )
    assert t.id is not None
    assert t.cruza_medianoche is False
    assert t.is_active is True
    assert _count_audit(db, "turno_created") == 1


def test_create_turno_nocturno_marca_cruza_medianoche(
    setup: Tuple[TurnoService, Database],
) -> None:
    service, _ = setup
    t = service.create_turno(
        nombre="Vigilancia",
        hora_entrada="22:00",
        hora_salida="06:00",
        minutos_descanso=30,
        dias_semana=DIAS_TODA_LA_SEMANA,
        actor_user_id=ACTOR_ID,
    )
    assert t.cruza_medianoche is True


def test_create_turno_trim_nombre(
    setup: Tuple[TurnoService, Database],
) -> None:
    service, _ = setup
    t = service.create_turno(
        nombre="   Oficina   ",
        hora_entrada="08:00",
        hora_salida="17:00",
        minutos_descanso=0,
        dias_semana=DIAS_LABORALES,
        actor_user_id=ACTOR_ID,
    )
    assert t.nombre == "Oficina"


def test_create_turno_duplicado_falla(
    setup: Tuple[TurnoService, Database],
) -> None:
    service, _ = setup
    service.create_turno("T", "08:00", "17:00", 0, DIAS_LABORALES, ACTOR_ID)
    with pytest.raises(DuplicateTurnoNombreError):
        service.create_turno("T", "07:00", "15:00", 0, DIAS_LABORALES, ACTOR_ID)


def test_create_turno_nombre_vacio_falla(
    setup: Tuple[TurnoService, Database],
) -> None:
    service, _ = setup
    with pytest.raises(MissingRequiredFieldError):
        service.create_turno("   ", "08:00", "17:00", 0, DIAS_LABORALES, ACTOR_ID)


def test_create_turno_hora_invalida_falla(
    setup: Tuple[TurnoService, Database],
) -> None:
    service, _ = setup
    with pytest.raises(InvalidTimeError):
        service.create_turno("T", "25:00", "17:00", 0, DIAS_LABORALES, ACTOR_ID)


def test_create_turno_bitmask_cero_falla(
    setup: Tuple[TurnoService, Database],
) -> None:
    service, _ = setup
    with pytest.raises(InvalidBitmaskError):
        service.create_turno("T", "08:00", "17:00", 0, 0, ACTOR_ID)


def test_create_turno_bitmask_fuera_de_rango_falla(
    setup: Tuple[TurnoService, Database],
) -> None:
    service, _ = setup
    with pytest.raises(InvalidBitmaskError):
        service.create_turno("T", "08:00", "17:00", 0, 128, ACTOR_ID)


def test_create_turno_descanso_mayor_que_duracion_falla(
    setup: Tuple[TurnoService, Database],
) -> None:
    service, _ = setup
    # Turno de 4 horas (240 min) con 300 min de descanso → falla.
    with pytest.raises(InvalidDescansoError):
        service.create_turno("T", "08:00", "12:00", 300, DIAS_LABORALES, ACTOR_ID)


def test_create_turno_descanso_igual_a_duracion_falla(
    setup: Tuple[TurnoService, Database],
) -> None:
    service, _ = setup
    # Turno de 4 horas (240 min) con 240 min de descanso → falla (>=).
    with pytest.raises(InvalidDescansoError):
        service.create_turno("T", "08:00", "12:00", 240, DIAS_LABORALES, ACTOR_ID)


# ── update_turno ──────────────────────────────────────────────────────────────


def test_update_turno_ok(setup: Tuple[TurnoService, Database]) -> None:
    service, db = setup
    t = service.create_turno("T", "08:00", "17:00", 60, DIAS_LABORALES, ACTOR_ID)
    assert t.id is not None
    service.update_turno(
        turno_id=t.id,
        nombre="T Mod",
        hora_entrada="09:00",
        hora_salida="18:00",
        minutos_descanso=45,
        dias_semana=DIAS_LABORALES,
        actor_user_id=ACTOR_ID,
    )
    actualizado = service.get_turno(t.id)
    assert actualizado.nombre == "T Mod"
    assert actualizado.hora_entrada == "09:00"
    assert actualizado.minutos_descanso == 45
    assert _count_audit(db, "turno_updated") == 1


def test_update_turno_cambia_a_nocturno_marca_cruza(
    setup: Tuple[TurnoService, Database],
) -> None:
    service, _ = setup
    t = service.create_turno("T", "08:00", "17:00", 0, DIAS_LABORALES, ACTOR_ID)
    assert t.id is not None
    service.update_turno(
        turno_id=t.id,
        nombre="T Noche",
        hora_entrada="22:00",
        hora_salida="06:00",
        minutos_descanso=0,
        dias_semana=DIAS_TODA_LA_SEMANA,
        actor_user_id=ACTOR_ID,
    )
    actualizado = service.get_turno(t.id)
    assert actualizado.cruza_medianoche is True


def test_update_turno_preserva_is_active(
    setup: Tuple[TurnoService, Database],
) -> None:
    service, _ = setup
    t = service.create_turno("T", "08:00", "17:00", 0, DIAS_LABORALES, ACTOR_ID)
    assert t.id is not None
    service.archive_turno(t.id, ACTOR_ID)
    service.update_turno(t.id, "T Renombrado", "08:00", "17:00", 0, DIAS_LABORALES, ACTOR_ID)
    # Update no reactiva — preserva el estado.
    assert service.get_turno(t.id).is_active is False


def test_update_turno_colision_nombre_falla(
    setup: Tuple[TurnoService, Database],
) -> None:
    service, _ = setup
    t1 = service.create_turno("A", "08:00", "17:00", 0, DIAS_LABORALES, ACTOR_ID)
    service.create_turno("B", "07:00", "16:00", 0, DIAS_LABORALES, ACTOR_ID)
    assert t1.id is not None
    with pytest.raises(DuplicateTurnoNombreError):
        service.update_turno(t1.id, "B", "08:00", "17:00", 0, DIAS_LABORALES, ACTOR_ID)


def test_update_turno_inexistente_falla(
    setup: Tuple[TurnoService, Database],
) -> None:
    service, _ = setup
    with pytest.raises(TurnoNotFoundError):
        service.update_turno(9999, "X", "08:00", "17:00", 0, DIAS_LABORALES, ACTOR_ID)


# ── archive / unarchive ───────────────────────────────────────────────────────


def test_archive_turno_ok(setup: Tuple[TurnoService, Database]) -> None:
    service, db = setup
    t = service.create_turno("T", "08:00", "17:00", 0, DIAS_LABORALES, ACTOR_ID)
    assert t.id is not None
    service.archive_turno(t.id, ACTOR_ID)
    assert service.get_turno(t.id).is_active is False
    assert _count_audit(db, "turno_archived") == 1


def test_archive_turno_idempotente(
    setup: Tuple[TurnoService, Database],
) -> None:
    service, db = setup
    t = service.create_turno("T", "08:00", "17:00", 0, DIAS_LABORALES, ACTOR_ID)
    assert t.id is not None
    service.archive_turno(t.id, ACTOR_ID)
    service.archive_turno(t.id, ACTOR_ID)
    assert _count_audit(db, "turno_archived") == 1


def test_unarchive_turno_ok(setup: Tuple[TurnoService, Database]) -> None:
    service, _ = setup
    t = service.create_turno("T", "08:00", "17:00", 0, DIAS_LABORALES, ACTOR_ID)
    assert t.id is not None
    service.archive_turno(t.id, ACTOR_ID)
    service.unarchive_turno(t.id, ACTOR_ID)
    assert service.get_turno(t.id).is_active is True


# ── list_turnos ───────────────────────────────────────────────────────────────


def test_list_turnos_solo_activos_filtra(
    setup: Tuple[TurnoService, Database],
) -> None:
    service, _ = setup
    t1 = service.create_turno("A", "08:00", "17:00", 0, DIAS_LABORALES, ACTOR_ID)
    service.create_turno("B", "07:00", "16:00", 0, DIAS_LABORALES, ACTOR_ID)
    assert t1.id is not None
    service.archive_turno(t1.id, ACTOR_ID)
    activos = service.list_turnos()
    assert [t.nombre for t in activos] == ["B"]
    todos = service.list_turnos(solo_activos=False)
    assert len(todos) == 2
