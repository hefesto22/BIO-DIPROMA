"""Tests del EmpleadoService.

Integración con repos SQLite reales + AuditLogger real. Cubre CRUD,
ciclo de vida (baja/reactivación), asignación y cambio de turno. La
baja cierra automáticamente el turno vigente — esto se verifica en el
historial de ``empleado_turnos``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Tuple

import pytest

from core.models.cargo import Cargo
from core.models.departamento import Departamento
from core.models.turno import DIAS_LABORALES, Turno
from core.repositories.audit_log_repository_sqlite import (
    AuditLogRepositorySQLite,
)
from core.repositories.cargo_repository_sqlite import CargoRepositorySQLite
from core.repositories.departamento_repository_sqlite import (
    DepartamentoRepositorySQLite,
)
from core.repositories.empleado_repository_sqlite import (
    EmpleadoRepositorySQLite,
)
from core.repositories.empleado_turno_repository_sqlite import (
    EmpleadoTurnoRepositorySQLite,
)
from core.repositories.turno_repository_sqlite import TurnoRepositorySQLite
from core.services.audit_logger import AuditLogger
from core.services.empleado_service import EmpleadoService
from core.services.errors import (
    CatalogoNotFoundError,
    DuplicateDNIError,
    DuplicateZktecoIdError,
    EmpleadoAlreadyActiveError,
    EmpleadoAlreadyInactiveError,
    EmpleadoNotFoundError,
    InvalidDateError,
    InvalidDNIError,
    InvalidMotivoBajaError,
    MissingRequiredFieldError,
    SinTurnoVigenteError,
    TurnoInactiveError,
    TurnoNotFoundError,
    TurnoBitmaskSolapadoError,
)
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "infrastructure" / "database" / "migrations"

ACTOR_ID = 1
DNI_VALIDO = "0501-1990-12345"


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
def setup(tmp_path: Path) -> Tuple[EmpleadoService, Database, int, int, int, int]:
    """Crea DB + servicio + seed de 1 depto, 1 cargo y 2 turnos activos.

    Returns:
        (service, db, departamento_id, cargo_id, turno_a_id, turno_b_id).
    """
    db = Database(tmp_path / "test_empleado.db")
    MigrationsRunner(db, MIGRATIONS_DIR).run()
    _seed_actor_user(db)

    dep = DepartamentoRepositorySQLite(db).create(Departamento(id=None, nombre="Admin"))
    cargo = CargoRepositorySQLite(db).create(Cargo(id=None, nombre="Analista"))
    t_a = TurnoRepositorySQLite(db).create(
        Turno(
            id=None,
            nombre="A",
            hora_entrada="08:00",
            hora_salida="17:00",
            dias_semana=DIAS_LABORALES,
        )
    )
    t_b = TurnoRepositorySQLite(db).create(
        Turno(
            id=None,
            nombre="B",
            hora_entrada="07:00",
            hora_salida="16:00",
            dias_semana=DIAS_LABORALES,
        )
    )
    assert dep.id is not None and cargo.id is not None
    assert t_a.id is not None and t_b.id is not None

    service = EmpleadoService(
        empleado_read=EmpleadoRepositorySQLite(db),
        empleado_write=EmpleadoRepositorySQLite(db),
        empleado_turno_read=EmpleadoTurnoRepositorySQLite(db),
        empleado_turno_write=EmpleadoTurnoRepositorySQLite(db),
        departamento_read=DepartamentoRepositorySQLite(db),
        cargo_read=CargoRepositorySQLite(db),
        turno_read=TurnoRepositorySQLite(db),
        audit_logger=AuditLogger(AuditLogRepositorySQLite(db), machine_name="test"),
    )
    return service, db, dep.id, cargo.id, t_a.id, t_b.id


def _count_audit(db: Database, action: str) -> int:
    with db.transaction() as conn:
        cur = conn.execute("SELECT COUNT(*) AS n FROM audit_log WHERE action = ?", (action,))
        return int(cur.fetchone()["n"])


# ── create_empleado ───────────────────────────────────────────────────────────


def test_create_empleado_ok(
    setup: Tuple[EmpleadoService, Database, int, int, int, int],
) -> None:
    service, db, dep_id, cargo_id, _, _ = setup
    emp = service.create_empleado(
        dni=DNI_VALIDO,
        nombres="Juan",
        apellidos="Pérez",
        departamento_id=dep_id,
        cargo_id=cargo_id,
        fecha_ingreso="2024-01-15",
        actor_user_id=ACTOR_ID,
    )
    assert emp.id is not None
    assert emp.dni == DNI_VALIDO
    assert emp.is_active is True
    assert _count_audit(db, "empleado_created") == 1


def test_create_empleado_dni_invalido_falla(
    setup: Tuple[EmpleadoService, Database, int, int, int, int],
) -> None:
    service, _, dep_id, cargo_id, _, _ = setup
    with pytest.raises(InvalidDNIError):
        service.create_empleado(
            dni="mal-formato",
            nombres="X",
            apellidos="Y",
            departamento_id=dep_id,
            cargo_id=cargo_id,
            fecha_ingreso="2024-01-01",
            actor_user_id=ACTOR_ID,
        )


def test_create_empleado_nombres_vacios_falla(
    setup: Tuple[EmpleadoService, Database, int, int, int, int],
) -> None:
    service, _, dep_id, cargo_id, _, _ = setup
    with pytest.raises(MissingRequiredFieldError):
        service.create_empleado(
            dni=DNI_VALIDO,
            nombres="   ",
            apellidos="Pérez",
            departamento_id=dep_id,
            cargo_id=cargo_id,
            fecha_ingreso="2024-01-01",
            actor_user_id=ACTOR_ID,
        )


def test_create_empleado_dni_duplicado_falla(
    setup: Tuple[EmpleadoService, Database, int, int, int, int],
) -> None:
    service, _, dep_id, cargo_id, _, _ = setup
    service.create_empleado(
        dni=DNI_VALIDO,
        nombres="A",
        apellidos="B",
        departamento_id=dep_id,
        cargo_id=cargo_id,
        fecha_ingreso="2024-01-01",
        actor_user_id=ACTOR_ID,
    )
    with pytest.raises(DuplicateDNIError):
        service.create_empleado(
            dni=DNI_VALIDO,
            nombres="Otro",
            apellidos="Nombre",
            departamento_id=dep_id,
            cargo_id=cargo_id,
            fecha_ingreso="2024-01-01",
            actor_user_id=ACTOR_ID,
        )


def test_create_empleado_zkteco_duplicado_falla(
    setup: Tuple[EmpleadoService, Database, int, int, int, int],
) -> None:
    service, _, dep_id, cargo_id, _, _ = setup
    service.create_empleado(
        dni="0501-1990-00001",
        nombres="A",
        apellidos="B",
        departamento_id=dep_id,
        cargo_id=cargo_id,
        fecha_ingreso="2024-01-01",
        actor_user_id=ACTOR_ID,
        zkteco_id=42,
    )
    with pytest.raises(DuplicateZktecoIdError):
        service.create_empleado(
            dni="0501-1990-00002",
            nombres="C",
            apellidos="D",
            departamento_id=dep_id,
            cargo_id=cargo_id,
            fecha_ingreso="2024-01-01",
            actor_user_id=ACTOR_ID,
            zkteco_id=42,
        )


def test_create_empleado_departamento_archivado_falla(
    setup: Tuple[EmpleadoService, Database, int, int, int, int],
) -> None:
    service, db, dep_id, cargo_id, _, _ = setup
    DepartamentoRepositorySQLite(db).archive(dep_id)
    with pytest.raises(CatalogoNotFoundError):
        service.create_empleado(
            dni=DNI_VALIDO,
            nombres="X",
            apellidos="Y",
            departamento_id=dep_id,
            cargo_id=cargo_id,
            fecha_ingreso="2024-01-01",
            actor_user_id=ACTOR_ID,
        )


def test_create_empleado_cargo_archivado_falla(
    setup: Tuple[EmpleadoService, Database, int, int, int, int],
) -> None:
    service, db, dep_id, cargo_id, _, _ = setup
    CargoRepositorySQLite(db).archive(cargo_id)
    with pytest.raises(CatalogoNotFoundError):
        service.create_empleado(
            dni=DNI_VALIDO,
            nombres="X",
            apellidos="Y",
            departamento_id=dep_id,
            cargo_id=cargo_id,
            fecha_ingreso="2024-01-01",
            actor_user_id=ACTOR_ID,
        )


def test_create_empleado_fecha_ingreso_invalida_falla(
    setup: Tuple[EmpleadoService, Database, int, int, int, int],
) -> None:
    service, _, dep_id, cargo_id, _, _ = setup
    with pytest.raises(InvalidDateError):
        service.create_empleado(
            dni=DNI_VALIDO,
            nombres="X",
            apellidos="Y",
            departamento_id=dep_id,
            cargo_id=cargo_id,
            fecha_ingreso="15/01/2024",
            actor_user_id=ACTOR_ID,
        )


# ── update_empleado ───────────────────────────────────────────────────────────


def test_update_empleado_ok(
    setup: Tuple[EmpleadoService, Database, int, int, int, int],
) -> None:
    service, db, dep_id, cargo_id, _, _ = setup
    emp = service.create_empleado(DNI_VALIDO, "A", "B", dep_id, cargo_id, "2024-01-01", ACTOR_ID)
    assert emp.id is not None
    service.update_empleado(
        empleado_id=emp.id,
        dni=DNI_VALIDO,
        nombres="Ana",
        apellidos="Sosa",
        departamento_id=dep_id,
        cargo_id=cargo_id,
        fecha_ingreso="2024-01-01",
        actor_user_id=ACTOR_ID,
        telefono="555-0100",
    )
    actualizado = service.get_empleado(emp.id)
    assert actualizado.nombres == "Ana"
    assert actualizado.apellidos == "Sosa"
    assert actualizado.telefono == "555-0100"
    assert _count_audit(db, "empleado_updated") == 1


def test_update_empleado_inexistente_falla(
    setup: Tuple[EmpleadoService, Database, int, int, int, int],
) -> None:
    service, _, dep_id, cargo_id, _, _ = setup
    with pytest.raises(EmpleadoNotFoundError):
        service.update_empleado(
            9999, DNI_VALIDO, "X", "Y", dep_id, cargo_id, "2024-01-01", ACTOR_ID
        )


def test_update_empleado_dni_colision_falla(
    setup: Tuple[EmpleadoService, Database, int, int, int, int],
) -> None:
    service, _, dep_id, cargo_id, _, _ = setup
    service.create_empleado("0501-1990-00001", "A", "B", dep_id, cargo_id, "2024-01-01", ACTOR_ID)
    otro = service.create_empleado(
        "0501-1990-00002", "C", "D", dep_id, cargo_id, "2024-01-01", ACTOR_ID
    )
    assert otro.id is not None
    with pytest.raises(DuplicateDNIError):
        service.update_empleado(
            otro.id,
            "0501-1990-00001",  # colisiona
            "C",
            "D",
            dep_id,
            cargo_id,
            "2024-01-01",
            ACTOR_ID,
        )


# ── deactivate_empleado ───────────────────────────────────────────────────────


def test_deactivate_empleado_ok_sin_turno_vigente(
    setup: Tuple[EmpleadoService, Database, int, int, int, int],
) -> None:
    service, db, dep_id, cargo_id, _, _ = setup
    emp = service.create_empleado(DNI_VALIDO, "A", "B", dep_id, cargo_id, "2024-01-01", ACTOR_ID)
    assert emp.id is not None
    service.deactivate_empleado(
        emp.id,
        fecha_baja="2026-04-20",
        motivo_baja="RENUNCIA",
        actor_user_id=ACTOR_ID,
    )
    actualizado = service.get_empleado(emp.id)
    assert actualizado.is_active is False
    assert actualizado.fecha_baja == "2026-04-20"
    assert actualizado.motivo_baja == "RENUNCIA"
    assert _count_audit(db, "empleado_deactivated") == 1


def test_deactivate_empleado_cierra_turno_vigente(
    setup: Tuple[EmpleadoService, Database, int, int, int, int],
) -> None:
    service, _, dep_id, cargo_id, turno_a, _ = setup
    emp = service.create_empleado(DNI_VALIDO, "A", "B", dep_id, cargo_id, "2024-01-01", ACTOR_ID)
    assert emp.id is not None
    service.asignar_turno(emp.id, turno_a, "2024-01-15", ACTOR_ID)
    service.deactivate_empleado(emp.id, "2026-04-20", "JUBILACION", ACTOR_ID)
    # Ya no hay vigente.
    assert service.get_turno_vigente(emp.id) is None
    # Pero el historial preservó la fila con fecha_fin.
    historial = service.get_historial_turnos(emp.id)
    assert len(historial) == 1
    assert historial[0].fecha_fin == "2026-04-20"


def test_deactivate_empleado_ya_inactivo_falla(
    setup: Tuple[EmpleadoService, Database, int, int, int, int],
) -> None:
    service, _, dep_id, cargo_id, _, _ = setup
    emp = service.create_empleado(DNI_VALIDO, "A", "B", dep_id, cargo_id, "2024-01-01", ACTOR_ID)
    assert emp.id is not None
    service.deactivate_empleado(emp.id, "2026-04-20", "DESPIDO", ACTOR_ID)
    with pytest.raises(EmpleadoAlreadyInactiveError):
        service.deactivate_empleado(emp.id, "2026-04-21", "OTRO", ACTOR_ID, "x")


def test_deactivate_empleado_motivo_invalido_falla(
    setup: Tuple[EmpleadoService, Database, int, int, int, int],
) -> None:
    service, _, dep_id, cargo_id, _, _ = setup
    emp = service.create_empleado(DNI_VALIDO, "A", "B", dep_id, cargo_id, "2024-01-01", ACTOR_ID)
    assert emp.id is not None
    with pytest.raises(InvalidMotivoBajaError):
        service.deactivate_empleado(emp.id, "2026-04-20", "VACACIONES", ACTOR_ID)


def test_deactivate_empleado_motivo_otro_sin_nota_falla(
    setup: Tuple[EmpleadoService, Database, int, int, int, int],
) -> None:
    service, _, dep_id, cargo_id, _, _ = setup
    emp = service.create_empleado(DNI_VALIDO, "A", "B", dep_id, cargo_id, "2024-01-01", ACTOR_ID)
    assert emp.id is not None
    with pytest.raises(MissingRequiredFieldError):
        service.deactivate_empleado(emp.id, "2026-04-20", "OTRO", ACTOR_ID, nota_baja="   ")


def test_deactivate_empleado_motivo_otro_con_nota_ok(
    setup: Tuple[EmpleadoService, Database, int, int, int, int],
) -> None:
    service, _, dep_id, cargo_id, _, _ = setup
    emp = service.create_empleado(DNI_VALIDO, "A", "B", dep_id, cargo_id, "2024-01-01", ACTOR_ID)
    assert emp.id is not None
    service.deactivate_empleado(emp.id, "2026-04-20", "OTRO", ACTOR_ID, nota_baja="Mutuo acuerdo")
    assert service.get_empleado(emp.id).nota_baja == "Mutuo acuerdo"


# ── reactivate_empleado ───────────────────────────────────────────────────────


def test_reactivate_empleado_ok(
    setup: Tuple[EmpleadoService, Database, int, int, int, int],
) -> None:
    service, db, dep_id, cargo_id, _, _ = setup
    emp = service.create_empleado(DNI_VALIDO, "A", "B", dep_id, cargo_id, "2024-01-01", ACTOR_ID)
    assert emp.id is not None
    service.deactivate_empleado(emp.id, "2026-04-20", "RENUNCIA", ACTOR_ID)
    service.reactivate_empleado(emp.id, ACTOR_ID)
    actualizado = service.get_empleado(emp.id)
    assert actualizado.is_active is True
    assert actualizado.fecha_baja is None
    assert _count_audit(db, "empleado_reactivated") == 1


def test_reactivate_empleado_ya_activo_falla(
    setup: Tuple[EmpleadoService, Database, int, int, int, int],
) -> None:
    service, _, dep_id, cargo_id, _, _ = setup
    emp = service.create_empleado(DNI_VALIDO, "A", "B", dep_id, cargo_id, "2024-01-01", ACTOR_ID)
    assert emp.id is not None
    with pytest.raises(EmpleadoAlreadyActiveError):
        service.reactivate_empleado(emp.id, ACTOR_ID)


def test_reactivate_no_restaura_turno_vigente(
    setup: Tuple[EmpleadoService, Database, int, int, int, int],
) -> None:
    service, _, dep_id, cargo_id, turno_a, _ = setup
    emp = service.create_empleado(DNI_VALIDO, "A", "B", dep_id, cargo_id, "2024-01-01", ACTOR_ID)
    assert emp.id is not None
    service.asignar_turno(emp.id, turno_a, "2024-01-15", ACTOR_ID)
    service.deactivate_empleado(emp.id, "2026-04-20", "JUBILACION", ACTOR_ID)
    service.reactivate_empleado(emp.id, ACTOR_ID)
    # Reactivar NO vuelve a abrir el turno vigente.
    assert service.get_turno_vigente(emp.id) is None


# ── asignar_turno ─────────────────────────────────────────────────────────────


def test_asignar_turno_ok(
    setup: Tuple[EmpleadoService, Database, int, int, int, int],
) -> None:
    service, db, dep_id, cargo_id, turno_a, _ = setup
    emp = service.create_empleado(DNI_VALIDO, "A", "B", dep_id, cargo_id, "2024-01-01", ACTOR_ID)
    assert emp.id is not None
    service.asignar_turno(emp.id, turno_a, "2024-01-15", ACTOR_ID)
    vigente = service.get_turno_vigente(emp.id)
    assert vigente is not None
    assert vigente.turno_id == turno_a
    assert _count_audit(db, "turno_asignado") == 1


def test_asignar_segundo_turno_con_bitmask_solapado_falla(
    setup: Tuple[EmpleadoService, Database, int, int, int, int],
) -> None:
    """Sub-3.2.B: dos turnos con bitmasks que se solapan no pueden coexistir.

    En el setup ambos turnos usan DIAS_LABORALES (lun-vie), así que la
    intersección no es vacía y la asignación del segundo se rechaza con
    ``TurnoBitmaskSolapadoError``.
    """
    service, _, dep_id, cargo_id, turno_a, turno_b = setup
    emp = service.create_empleado(DNI_VALIDO, "A", "B", dep_id, cargo_id, "2024-01-01", ACTOR_ID)
    assert emp.id is not None
    service.asignar_turno(emp.id, turno_a, "2024-01-15", ACTOR_ID)
    with pytest.raises(TurnoBitmaskSolapadoError):
        service.asignar_turno(emp.id, turno_b, "2024-02-01", ACTOR_ID)


def test_asignar_segundo_turno_con_bitmask_disjunto_ok(
    setup: Tuple[EmpleadoService, Database, int, int, int, int],
) -> None:
    """Sub-3.2.B: dos turnos con bitmasks disjuntos coexisten como vigentes.

    El caso real: lun-vie 8-17 + sábado 8-13. Ambas asignaciones quedan
    abiertas (``fecha_fin IS NULL``) y la consolidación elige cuál
    aplica según el día de la semana.
    """
    from core.models.turno import DIAS_LABORALES, SABADO, Turno
    from core.repositories.turno_repository_sqlite import TurnoRepositorySQLite

    service, db, dep_id, cargo_id, turno_a, _ = setup
    # Creamos un turno solo para sábado.
    turno_sabado = TurnoRepositorySQLite(db).create(
        Turno(
            id=None,
            nombre="Sábado 08-13",
            hora_entrada="08:00",
            hora_salida="13:00",
            dias_semana=SABADO,
        )
    )
    assert turno_sabado.id is not None
    # Sanity: turno_a usa DIAS_LABORALES (no incluye sábado) → disjunto.
    assert (DIAS_LABORALES & SABADO) == 0

    emp = service.create_empleado(DNI_VALIDO, "A", "B", dep_id, cargo_id, "2024-01-01", ACTOR_ID)
    assert emp.id is not None
    service.asignar_turno(emp.id, turno_a, "2024-01-15", ACTOR_ID)
    # Distinta fecha_inicio para no chocar con el UNIQUE
    # (empleado_id, fecha_inicio) de empleado_turnos.
    service.asignar_turno(emp.id, turno_sabado.id, "2024-01-16", ACTOR_ID)

    # El empleado ahora tiene 2 vigentes.
    from core.repositories.empleado_turno_repository_sqlite import (
        EmpleadoTurnoRepositorySQLite,
    )

    et_repo = EmpleadoTurnoRepositorySQLite(db)
    vigentes = et_repo.list_vigentes(emp.id)
    turno_ids = {v.turno_id for v in vigentes}
    assert turno_ids == {turno_a, turno_sabado.id}


def test_asignar_turno_empleado_inactivo_falla(
    setup: Tuple[EmpleadoService, Database, int, int, int, int],
) -> None:
    service, _, dep_id, cargo_id, turno_a, _ = setup
    emp = service.create_empleado(DNI_VALIDO, "A", "B", dep_id, cargo_id, "2024-01-01", ACTOR_ID)
    assert emp.id is not None
    service.deactivate_empleado(emp.id, "2024-06-01", "RENUNCIA", ACTOR_ID)
    with pytest.raises(EmpleadoAlreadyInactiveError):
        service.asignar_turno(emp.id, turno_a, "2024-07-01", ACTOR_ID)


def test_asignar_turno_archivado_falla(
    setup: Tuple[EmpleadoService, Database, int, int, int, int],
) -> None:
    service, db, dep_id, cargo_id, turno_a, _ = setup
    emp = service.create_empleado(DNI_VALIDO, "A", "B", dep_id, cargo_id, "2024-01-01", ACTOR_ID)
    assert emp.id is not None
    TurnoRepositorySQLite(db).archive(turno_a)
    with pytest.raises(TurnoInactiveError):
        service.asignar_turno(emp.id, turno_a, "2024-01-15", ACTOR_ID)


def test_asignar_turno_inexistente_falla(
    setup: Tuple[EmpleadoService, Database, int, int, int, int],
) -> None:
    service, _, dep_id, cargo_id, _, _ = setup
    emp = service.create_empleado(DNI_VALIDO, "A", "B", dep_id, cargo_id, "2024-01-01", ACTOR_ID)
    assert emp.id is not None
    with pytest.raises(TurnoNotFoundError):
        service.asignar_turno(emp.id, 9999, "2024-01-15", ACTOR_ID)


# ── cambiar_turno ─────────────────────────────────────────────────────────────


def test_cambiar_turno_ok(
    setup: Tuple[EmpleadoService, Database, int, int, int, int],
) -> None:
    service, db, dep_id, cargo_id, turno_a, turno_b = setup
    emp = service.create_empleado(DNI_VALIDO, "A", "B", dep_id, cargo_id, "2024-01-01", ACTOR_ID)
    assert emp.id is not None
    service.asignar_turno(emp.id, turno_a, "2024-01-15", ACTOR_ID)
    nueva = service.cambiar_turno(emp.id, turno_b, "2024-06-01", ACTOR_ID)
    assert nueva.turno_id == turno_b
    historial = service.get_historial_turnos(emp.id)
    assert len(historial) == 2
    # La anterior quedó cerrada con fecha_fin = fecha_inicio_nueva - 1 día.
    cerrada = [h for h in historial if h.turno_id == turno_a][0]
    assert cerrada.fecha_fin == "2024-05-31"
    assert _count_audit(db, "turno_cambiado") == 1


def test_cambiar_turno_sin_vigente_falla(
    setup: Tuple[EmpleadoService, Database, int, int, int, int],
) -> None:
    service, _, dep_id, cargo_id, _, turno_b = setup
    emp = service.create_empleado(DNI_VALIDO, "A", "B", dep_id, cargo_id, "2024-01-01", ACTOR_ID)
    assert emp.id is not None
    with pytest.raises(SinTurnoVigenteError):
        service.cambiar_turno(emp.id, turno_b, "2024-06-01", ACTOR_ID)


def test_cambiar_turno_fecha_anterior_al_inicio_vigente_falla(
    setup: Tuple[EmpleadoService, Database, int, int, int, int],
) -> None:
    service, _, dep_id, cargo_id, turno_a, turno_b = setup
    emp = service.create_empleado(DNI_VALIDO, "A", "B", dep_id, cargo_id, "2024-01-01", ACTOR_ID)
    assert emp.id is not None
    service.asignar_turno(emp.id, turno_a, "2024-06-01", ACTOR_ID)
    with pytest.raises(InvalidDateError):
        # fecha_inicio_nueva <= fecha_inicio_vigente
        service.cambiar_turno(emp.id, turno_b, "2024-06-01", ACTOR_ID)


def test_cambiar_turno_empleado_inactivo_falla(
    setup: Tuple[EmpleadoService, Database, int, int, int, int],
) -> None:
    service, _, dep_id, cargo_id, turno_a, turno_b = setup
    emp = service.create_empleado(DNI_VALIDO, "A", "B", dep_id, cargo_id, "2024-01-01", ACTOR_ID)
    assert emp.id is not None
    service.asignar_turno(emp.id, turno_a, "2024-01-15", ACTOR_ID)
    service.deactivate_empleado(emp.id, "2024-06-01", "RENUNCIA", ACTOR_ID)
    with pytest.raises(EmpleadoAlreadyInactiveError):
        service.cambiar_turno(emp.id, turno_b, "2024-07-01", ACTOR_ID)


# ── get_by_dni ────────────────────────────────────────────────────────────────


def test_get_by_dni_existente(
    setup: Tuple[EmpleadoService, Database, int, int, int, int],
) -> None:
    service, _, dep_id, cargo_id, _, _ = setup
    service.create_empleado(DNI_VALIDO, "A", "B", dep_id, cargo_id, "2024-01-01", ACTOR_ID)
    emp = service.get_by_dni(DNI_VALIDO)
    assert emp is not None
    assert emp.dni == DNI_VALIDO


def test_get_by_dni_inexistente_devuelve_none(
    setup: Tuple[EmpleadoService, Database, int, int, int, int],
) -> None:
    service, _, _, _, _, _ = setup
    assert service.get_by_dni("9999-9999-99999") is None
