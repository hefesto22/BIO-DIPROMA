"""Tests del CatalogoService (departamentos + cargos).

Integración con repos SQLite reales sobre ``tmp_path`` + AuditLogger
real. Cubre CRUD de ambos catálogos, duplicados, archivado con y sin
empleados activos, reactivación, y la verificación de que cada write
escribe una entrada en ``audit_log``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Tuple

import pytest

from core.models.empleado import Empleado
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
from core.services.audit_logger import AuditLogger
from core.services.catalogo_service import CatalogoService
from core.services.errors import (
    CatalogoInUseError,
    CatalogoNotFoundError,
    DuplicateNombreError,
    MissingRequiredFieldError,
)
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
def setup(tmp_path: Path) -> Tuple[CatalogoService, Database]:
    """Crea DB + servicio con todas sus dependencias reales."""
    db = Database(tmp_path / "test_catalogo.db")
    MigrationsRunner(db, MIGRATIONS_DIR).run()
    _seed_actor_user(db)
    service = CatalogoService(
        dep_read=DepartamentoRepositorySQLite(db),
        dep_write=DepartamentoRepositorySQLite(db),
        cargo_read=CargoRepositorySQLite(db),
        cargo_write=CargoRepositorySQLite(db),
        empleado_read=EmpleadoRepositorySQLite(db),
        audit_logger=AuditLogger(AuditLogRepositorySQLite(db), machine_name="test"),
    )
    return service, db


def _count_audit(db: Database, action: str) -> int:
    with db.transaction() as conn:
        cur = conn.execute("SELECT COUNT(*) AS n FROM audit_log WHERE action = ?", (action,))
        return int(cur.fetchone()["n"])


# ── Departamentos: CRUD ───────────────────────────────────────────────────────


def test_create_departamento_ok(setup: Tuple[CatalogoService, Database]) -> None:
    service, db = setup
    dep = service.create_departamento("Recursos Humanos", actor_user_id=ACTOR_ID)
    assert dep.id is not None
    assert dep.nombre == "Recursos Humanos"
    assert dep.is_active is True
    assert _count_audit(db, "departamento_created") == 1


def test_create_departamento_trim_y_colapsa_espacios(
    setup: Tuple[CatalogoService, Database],
) -> None:
    service, _ = setup
    dep = service.create_departamento("  Obras    Públicas  ", ACTOR_ID)
    assert dep.nombre == "Obras Públicas"


def test_create_departamento_vacio_levanta_missing(
    setup: Tuple[CatalogoService, Database],
) -> None:
    service, _ = setup
    with pytest.raises(MissingRequiredFieldError):
        service.create_departamento("   ", ACTOR_ID)


def test_create_departamento_duplicado_falla(
    setup: Tuple[CatalogoService, Database],
) -> None:
    service, _ = setup
    service.create_departamento("Admin", ACTOR_ID)
    with pytest.raises(DuplicateNombreError):
        service.create_departamento("Admin", ACTOR_ID)


def test_create_departamento_case_sensitive(
    setup: Tuple[CatalogoService, Database],
) -> None:
    service, _ = setup
    service.create_departamento("Admin", ACTOR_ID)
    # Distinto case → se considera distinto (por diseño).
    otro = service.create_departamento("admin", ACTOR_ID)
    assert otro.id is not None


def test_list_departamentos_solo_activos_por_default(
    setup: Tuple[CatalogoService, Database],
) -> None:
    service, _ = setup
    d1 = service.create_departamento("D1", ACTOR_ID)
    service.create_departamento("D2", ACTOR_ID)
    assert d1.id is not None
    service.archive_departamento(d1.id, ACTOR_ID)
    activos = service.list_departamentos()
    nombres = [d.nombre for d in activos]
    assert "D1" not in nombres
    assert "D2" in nombres
    todos = service.list_departamentos(solo_activos=False)
    assert len(todos) == 2


def test_get_departamento_inexistente_falla(
    setup: Tuple[CatalogoService, Database],
) -> None:
    service, _ = setup
    with pytest.raises(CatalogoNotFoundError):
        service.get_departamento(9999)


def test_rename_departamento_ok(setup: Tuple[CatalogoService, Database]) -> None:
    service, db = setup
    dep = service.create_departamento("Viejo", ACTOR_ID)
    assert dep.id is not None
    service.rename_departamento(dep.id, "Nuevo", ACTOR_ID)
    assert service.get_departamento(dep.id).nombre == "Nuevo"
    assert _count_audit(db, "departamento_renamed") == 1


def test_rename_departamento_mismo_nombre_no_op(
    setup: Tuple[CatalogoService, Database],
) -> None:
    service, db = setup
    dep = service.create_departamento("Igual", ACTOR_ID)
    assert dep.id is not None
    service.rename_departamento(dep.id, "Igual", ACTOR_ID)
    assert _count_audit(db, "departamento_renamed") == 0


def test_rename_departamento_colisiona_con_otro_falla(
    setup: Tuple[CatalogoService, Database],
) -> None:
    service, _ = setup
    d1 = service.create_departamento("A", ACTOR_ID)
    service.create_departamento("B", ACTOR_ID)
    assert d1.id is not None
    with pytest.raises(DuplicateNombreError):
        service.rename_departamento(d1.id, "B", ACTOR_ID)


def test_archive_departamento_sin_empleados_ok(
    setup: Tuple[CatalogoService, Database],
) -> None:
    service, db = setup
    dep = service.create_departamento("Archivable", ACTOR_ID)
    assert dep.id is not None
    service.archive_departamento(dep.id, ACTOR_ID)
    assert service.get_departamento(dep.id).is_active is False
    assert _count_audit(db, "departamento_archived") == 1


def test_archive_departamento_con_empleado_activo_falla(
    setup: Tuple[CatalogoService, Database],
) -> None:
    service, db = setup
    dep = service.create_departamento("Operaciones", ACTOR_ID)
    cargo = service.create_cargo("Jefe", ACTOR_ID)
    assert dep.id is not None and cargo.id is not None
    EmpleadoRepositorySQLite(db).create(
        Empleado(
            id=None,
            dni="0501-1980-11111",
            nombres="Ana",
            apellidos="Sosa",
            departamento_id=dep.id,
            cargo_id=cargo.id,
            fecha_ingreso="2024-01-01",
        )
    )
    with pytest.raises(CatalogoInUseError):
        service.archive_departamento(dep.id, ACTOR_ID)


def test_archive_departamento_idempotente(
    setup: Tuple[CatalogoService, Database],
) -> None:
    service, db = setup
    dep = service.create_departamento("X", ACTOR_ID)
    assert dep.id is not None
    service.archive_departamento(dep.id, ACTOR_ID)
    # Segunda llamada es no-op (no rompe, no re-audita).
    service.archive_departamento(dep.id, ACTOR_ID)
    assert _count_audit(db, "departamento_archived") == 1


def test_unarchive_departamento_ok(
    setup: Tuple[CatalogoService, Database],
) -> None:
    service, db = setup
    dep = service.create_departamento("Vuelve", ACTOR_ID)
    assert dep.id is not None
    service.archive_departamento(dep.id, ACTOR_ID)
    service.unarchive_departamento(dep.id, ACTOR_ID)
    assert service.get_departamento(dep.id).is_active is True
    assert _count_audit(db, "departamento_unarchived") == 1


# ── Cargos: se replica el comportamiento (smoke tests) ────────────────────────


def test_create_cargo_ok(setup: Tuple[CatalogoService, Database]) -> None:
    service, db = setup
    cargo = service.create_cargo("Contador", ACTOR_ID)
    assert cargo.id is not None
    assert cargo.nombre == "Contador"
    assert _count_audit(db, "cargo_created") == 1


def test_create_cargo_duplicado_falla(
    setup: Tuple[CatalogoService, Database],
) -> None:
    service, _ = setup
    service.create_cargo("Analista", ACTOR_ID)
    with pytest.raises(DuplicateNombreError):
        service.create_cargo("Analista", ACTOR_ID)


def test_rename_cargo_colision_falla(
    setup: Tuple[CatalogoService, Database],
) -> None:
    service, _ = setup
    c1 = service.create_cargo("A", ACTOR_ID)
    service.create_cargo("B", ACTOR_ID)
    assert c1.id is not None
    with pytest.raises(DuplicateNombreError):
        service.rename_cargo(c1.id, "B", ACTOR_ID)


def test_archive_cargo_con_empleado_activo_falla(
    setup: Tuple[CatalogoService, Database],
) -> None:
    service, db = setup
    dep = service.create_departamento("D", ACTOR_ID)
    cargo = service.create_cargo("C", ACTOR_ID)
    assert dep.id is not None and cargo.id is not None
    EmpleadoRepositorySQLite(db).create(
        Empleado(
            id=None,
            dni="0501-1980-22222",
            nombres="Luis",
            apellidos="Mora",
            departamento_id=dep.id,
            cargo_id=cargo.id,
            fecha_ingreso="2024-01-01",
        )
    )
    with pytest.raises(CatalogoInUseError):
        service.archive_cargo(cargo.id, ACTOR_ID)


def test_unarchive_cargo_ok(setup: Tuple[CatalogoService, Database]) -> None:
    service, _ = setup
    cargo = service.create_cargo("Temp", ACTOR_ID)
    assert cargo.id is not None
    service.archive_cargo(cargo.id, ACTOR_ID)
    service.unarchive_cargo(cargo.id, ACTOR_ID)
    assert service.get_cargo(cargo.id).is_active is True


# ── Sub-3.2.A: cargos por departamento ────────────────────────────────────────


def test_create_cargo_global_default_departamento_id_es_none(
    setup: Tuple[CatalogoService, Database],
) -> None:
    """Sin pasar ``departamento_id``, el cargo queda como global."""
    service, _ = setup
    cargo = service.create_cargo("Auxiliar", ACTOR_ID)
    assert cargo.departamento_id is None


def test_create_cargo_especifico_de_departamento(
    setup: Tuple[CatalogoService, Database],
) -> None:
    service, _ = setup
    dep = service.create_departamento("Tesorería", ACTOR_ID)
    assert dep.id is not None
    cargo = service.create_cargo("Tesorero", ACTOR_ID, departamento_id=dep.id)
    assert cargo.departamento_id == dep.id


def test_create_cargo_con_departamento_inexistente_falla(
    setup: Tuple[CatalogoService, Database],
) -> None:
    service, _ = setup
    with pytest.raises(CatalogoNotFoundError):
        service.create_cargo("Fantasma", ACTOR_ID, departamento_id=999)


def test_list_cargos_para_departamento_incluye_globales_y_especificos(
    setup: Tuple[CatalogoService, Database],
) -> None:
    """Sub-3.2.A: el dropdown debe mostrar globales + los del depto elegido."""
    service, _ = setup
    tesoreria = service.create_departamento("Tesorería", ACTOR_ID)
    obras = service.create_departamento("Obras", ACTOR_ID)
    assert tesoreria.id is not None and obras.id is not None
    auxiliar = service.create_cargo("Auxiliar", ACTOR_ID)  # global
    tesorero = service.create_cargo("Tesorero", ACTOR_ID, departamento_id=tesoreria.id)
    ingeniero = service.create_cargo("Ingeniero", ACTOR_ID, departamento_id=obras.id)

    para_tesoreria = service.list_cargos_para_departamento(tesoreria.id)
    nombres_tesoreria = {c.nombre for c in para_tesoreria}
    assert "Auxiliar" in nombres_tesoreria  # global
    assert "Tesorero" in nombres_tesoreria  # del depto
    assert "Ingeniero" not in nombres_tesoreria  # de otro depto

    para_obras = service.list_cargos_para_departamento(obras.id)
    nombres_obras = {c.nombre for c in para_obras}
    assert "Auxiliar" in nombres_obras
    assert "Ingeniero" in nombres_obras
    assert "Tesorero" not in nombres_obras

    # Sanity: los IDs creados son distintos.
    assert {auxiliar.id, tesorero.id, ingeniero.id} == {auxiliar.id, tesorero.id, ingeniero.id}


def test_list_cargos_para_departamento_excluye_archivados(
    setup: Tuple[CatalogoService, Database],
) -> None:
    service, _ = setup
    dep = service.create_departamento("RRHH", ACTOR_ID)
    assert dep.id is not None
    cargo = service.create_cargo("Asistente RH", ACTOR_ID, departamento_id=dep.id)
    assert cargo.id is not None
    service.archive_cargo(cargo.id, ACTOR_ID)
    listado = service.list_cargos_para_departamento(dep.id)
    assert all(c.id != cargo.id for c in listado)


def test_list_cargos_para_departamento_inexistente_falla(
    setup: Tuple[CatalogoService, Database],
) -> None:
    service, _ = setup
    with pytest.raises(CatalogoNotFoundError):
        service.list_cargos_para_departamento(9999)


def test_set_cargo_departamento_cambia_y_audita(
    setup: Tuple[CatalogoService, Database],
) -> None:
    service, db = setup
    dep_a = service.create_departamento("DepA", ACTOR_ID)
    dep_b = service.create_departamento("DepB", ACTOR_ID)
    assert dep_a.id is not None and dep_b.id is not None
    cargo = service.create_cargo("Coord", ACTOR_ID, departamento_id=dep_a.id)
    assert cargo.id is not None

    service.set_cargo_departamento(cargo.id, dep_b.id, ACTOR_ID)
    actualizado = service.get_cargo(cargo.id)
    assert actualizado.departamento_id == dep_b.id
    assert _count_audit(db, "cargo_depto_changed") == 1


def test_set_cargo_departamento_a_none_lo_vuelve_global(
    setup: Tuple[CatalogoService, Database],
) -> None:
    service, _ = setup
    dep = service.create_departamento("Algo", ACTOR_ID)
    assert dep.id is not None
    cargo = service.create_cargo("Especifico", ACTOR_ID, departamento_id=dep.id)
    assert cargo.id is not None
    service.set_cargo_departamento(cargo.id, None, ACTOR_ID)
    assert service.get_cargo(cargo.id).departamento_id is None


def test_set_cargo_departamento_idempotente_si_no_cambia(
    setup: Tuple[CatalogoService, Database],
) -> None:
    service, db = setup
    dep = service.create_departamento("X", ACTOR_ID)
    assert dep.id is not None
    cargo = service.create_cargo("Y", ACTOR_ID, departamento_id=dep.id)
    assert cargo.id is not None

    antes = _count_audit(db, "cargo_depto_changed")
    service.set_cargo_departamento(cargo.id, dep.id, ACTOR_ID)
    despues = _count_audit(db, "cargo_depto_changed")
    assert antes == despues  # no se audita un no-op


def test_create_empleado_rechaza_cargo_de_otro_departamento(
    setup: Tuple[CatalogoService, Database],
) -> None:
    """EmpleadoService valida que el cargo elegido pertenezca al depto."""
    from core.repositories.empleado_repository_sqlite import EmpleadoRepositorySQLite
    from core.repositories.empleado_turno_repository_sqlite import (
        EmpleadoTurnoRepositorySQLite,
    )
    from core.repositories.turno_repository_sqlite import TurnoRepositorySQLite
    from core.services.empleado_service import EmpleadoService

    service, db = setup
    audit = AuditLogger(AuditLogRepositorySQLite(db))
    emp_repo = EmpleadoRepositorySQLite(db)
    et_repo = EmpleadoTurnoRepositorySQLite(db)
    emp_service = EmpleadoService(
        empleado_read=emp_repo,
        empleado_write=emp_repo,
        empleado_turno_read=et_repo,
        empleado_turno_write=et_repo,
        departamento_read=DepartamentoRepositorySQLite(db),
        cargo_read=CargoRepositorySQLite(db),
        turno_read=TurnoRepositorySQLite(db),
        audit_logger=audit,
    )

    tesoreria = service.create_departamento("Tesorería", ACTOR_ID)
    obras = service.create_departamento("Obras", ACTOR_ID)
    assert tesoreria.id is not None and obras.id is not None
    cargo_tes = service.create_cargo("Tesorero", ACTOR_ID, departamento_id=tesoreria.id)
    assert cargo_tes.id is not None

    with pytest.raises(CatalogoNotFoundError):
        emp_service.create_empleado(
            dni="0501-1980-99999",
            nombres="Juan",
            apellidos="Pérez",
            departamento_id=obras.id,  # depto distinto al del cargo
            cargo_id=cargo_tes.id,
            fecha_ingreso="2026-01-01",
            actor_user_id=ACTOR_ID,
        )


def test_create_empleado_acepta_cargo_global(
    setup: Tuple[CatalogoService, Database],
) -> None:
    """Cargo global (departamento_id=None) acepta cualquier departamento."""
    from core.repositories.empleado_repository_sqlite import EmpleadoRepositorySQLite
    from core.repositories.empleado_turno_repository_sqlite import (
        EmpleadoTurnoRepositorySQLite,
    )
    from core.repositories.turno_repository_sqlite import TurnoRepositorySQLite
    from core.services.empleado_service import EmpleadoService

    service, db = setup
    audit = AuditLogger(AuditLogRepositorySQLite(db))
    emp_repo = EmpleadoRepositorySQLite(db)
    et_repo = EmpleadoTurnoRepositorySQLite(db)
    emp_service = EmpleadoService(
        empleado_read=emp_repo,
        empleado_write=emp_repo,
        empleado_turno_read=et_repo,
        empleado_turno_write=et_repo,
        departamento_read=DepartamentoRepositorySQLite(db),
        cargo_read=CargoRepositorySQLite(db),
        turno_read=TurnoRepositorySQLite(db),
        audit_logger=audit,
    )

    obras = service.create_departamento("Obras", ACTOR_ID)
    assert obras.id is not None
    auxiliar_global = service.create_cargo("Auxiliar", ACTOR_ID)  # sin depto
    assert auxiliar_global.id is not None

    creado = emp_service.create_empleado(
        dni="0501-1980-77777",
        nombres="Pedro",
        apellidos="Sosa",
        departamento_id=obras.id,
        cargo_id=auxiliar_global.id,
        fecha_ingreso="2026-01-01",
        actor_user_id=ACTOR_ID,
    )
    assert creado.id is not None
