"""Tests del EmpleadoRepositorySQLite.

Cubre CRUD completo, búsquedas (id/dni/zkteco_id), listados filtrados,
update sin tocar baja, deactivate/reactivate coherentes con el CHECK
del schema, e integridad (FKs + UNIQUE de dni y zkteco_id + CHECK de
coherencia de baja + CHECK de motivo_baja válido).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Tuple

import pytest

from core.models.empleado import Empleado, MotivoBaja
from core.repositories.cargo_repository_sqlite import CargoRepositorySQLite
from core.repositories.departamento_repository_sqlite import (
    DepartamentoRepositorySQLite,
)
from core.repositories.empleado_repository_sqlite import (
    EmpleadoRepositorySQLite,
)
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner
from core.models.cargo import Cargo
from core.models.departamento import Departamento

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "infrastructure" / "database" / "migrations"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def setup(
    tmp_path: Path,
) -> Tuple[EmpleadoRepositorySQLite, Database, int, int]:
    """Crea DB con migraciones aplicadas y seedea 1 depto y 1 cargo.

    Returns:
        Tupla (repo, database, departamento_id, cargo_id) — los ids son FKs
        válidas para crear empleados; ``database`` se expone para que los
        tests que necesiten instanciar repos auxiliares (p. ej. para crear
        un segundo departamento) no tengan que tocar estado privado del repo.
    """
    db = Database(tmp_path / "test_empleado.db")
    MigrationsRunner(db, MIGRATIONS_DIR).run()
    depto_repo = DepartamentoRepositorySQLite(db)
    cargo_repo = CargoRepositorySQLite(db)
    depto = depto_repo.create(Departamento(id=None, nombre="Admin"))
    cargo = cargo_repo.create(Cargo(id=None, nombre="Secretaria"))
    assert depto.id is not None and cargo.id is not None
    return EmpleadoRepositorySQLite(db), db, depto.id, cargo.id


def _nuevo_empleado(
    departamento_id: int,
    cargo_id: int,
    dni: str = "0501-1990-12345",
    zkteco_id: int | None = None,
    nombres: str = "Juan",
    apellidos: str = "Pérez",
) -> Empleado:
    return Empleado(
        id=None,
        dni=dni,
        nombres=nombres,
        apellidos=apellidos,
        departamento_id=departamento_id,
        cargo_id=cargo_id,
        fecha_ingreso="2020-01-15",
        telefono="9999-0000",
        email="juan@example.com",
        zkteco_id=zkteco_id,
    )


# ── create + read ─────────────────────────────────────────────────────────────


def test_create_asigna_id_y_timestamps(
    setup: Tuple[EmpleadoRepositorySQLite, Database, int, int],
) -> None:
    repo, _db, depto_id, cargo_id = setup
    creado = repo.create(_nuevo_empleado(depto_id, cargo_id))
    assert creado.id is not None and creado.id > 0
    assert creado.created_at is not None
    assert creado.updated_at is not None
    assert creado.is_active is True
    assert creado.fecha_baja is None
    assert creado.motivo_baja is None


def test_get_by_id_encuentra_creado(
    setup: Tuple[EmpleadoRepositorySQLite, Database, int, int],
) -> None:
    repo, _db, depto_id, cargo_id = setup
    creado = repo.create(_nuevo_empleado(depto_id, cargo_id, nombres="Ana"))
    assert creado.id is not None
    leido = repo.get_by_id(creado.id)
    assert leido is not None
    assert leido.nombres == "Ana"
    assert leido.departamento_id == depto_id


def test_get_by_id_inexistente(
    setup: Tuple[EmpleadoRepositorySQLite, Database, int, int],
) -> None:
    repo, _db, _depto_id, _cargo_id = setup
    assert repo.get_by_id(9999) is None


def test_get_by_dni_encuentra_creado(
    setup: Tuple[EmpleadoRepositorySQLite, Database, int, int],
) -> None:
    repo, _db, depto_id, cargo_id = setup
    repo.create(_nuevo_empleado(depto_id, cargo_id, dni="0501-1985-00001"))
    leido = repo.get_by_dni("0501-1985-00001")
    assert leido is not None


def test_get_by_dni_inexistente(
    setup: Tuple[EmpleadoRepositorySQLite, Database, int, int],
) -> None:
    repo, _db, _depto_id, _cargo_id = setup
    assert repo.get_by_dni("9999-9999-99999") is None


def test_get_by_zkteco_id_encuentra_creado(
    setup: Tuple[EmpleadoRepositorySQLite, Database, int, int],
) -> None:
    repo, _db, depto_id, cargo_id = setup
    repo.create(_nuevo_empleado(depto_id, cargo_id, zkteco_id=42))
    leido = repo.get_by_zkteco_id(42)
    assert leido is not None
    assert leido.zkteco_id == 42


def test_get_by_zkteco_id_inexistente(
    setup: Tuple[EmpleadoRepositorySQLite, Database, int, int],
) -> None:
    repo, _db, _depto_id, _cargo_id = setup
    assert repo.get_by_zkteco_id(42) is None


def test_create_con_dni_duplicado_falla(
    setup: Tuple[EmpleadoRepositorySQLite, Database, int, int],
) -> None:
    repo, _db, depto_id, cargo_id = setup
    repo.create(_nuevo_empleado(depto_id, cargo_id, dni="0501-1990-11111"))
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(_nuevo_empleado(depto_id, cargo_id, dni="0501-1990-11111", nombres="Otro"))


def test_create_con_zkteco_id_duplicado_falla(
    setup: Tuple[EmpleadoRepositorySQLite, Database, int, int],
) -> None:
    repo, _db, depto_id, cargo_id = setup
    repo.create(_nuevo_empleado(depto_id, cargo_id, dni="0501-1990-11111", zkteco_id=7))
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(_nuevo_empleado(depto_id, cargo_id, dni="0501-1990-22222", zkteco_id=7))


def test_create_permite_varios_con_zkteco_id_null(
    setup: Tuple[EmpleadoRepositorySQLite, Database, int, int],
) -> None:
    """SQLite semantics: múltiples NULLs no violan UNIQUE."""
    repo, _db, depto_id, cargo_id = setup
    repo.create(_nuevo_empleado(depto_id, cargo_id, dni="0501-1990-11111", zkteco_id=None))
    repo.create(_nuevo_empleado(depto_id, cargo_id, dni="0501-1990-22222", zkteco_id=None))
    # No debe levantar excepción.
    assert len(repo.list_all()) == 2


def test_create_con_departamento_inexistente_falla(
    setup: Tuple[EmpleadoRepositorySQLite, Database, int, int],
) -> None:
    repo, _db, _depto_id, cargo_id = setup
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(_nuevo_empleado(999, cargo_id))


def test_create_con_cargo_inexistente_falla(
    setup: Tuple[EmpleadoRepositorySQLite, Database, int, int],
) -> None:
    repo, _db, depto_id, _cargo_id = setup
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(_nuevo_empleado(depto_id, 999))


def test_create_con_is_active_false_y_sin_baja_falla(
    setup: Tuple[EmpleadoRepositorySQLite, Database, int, int],
) -> None:
    """El CHECK de coherencia debe rechazar inactivo sin campos de baja."""
    repo, _db, depto_id, cargo_id = setup
    emp = _nuevo_empleado(depto_id, cargo_id)
    emp.is_active = False  # pero sin fecha_baja/motivo_baja
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(emp)


# ── listados ──────────────────────────────────────────────────────────────────


def test_list_all_vacio(
    setup: Tuple[EmpleadoRepositorySQLite, Database, int, int],
) -> None:
    repo, _db, _depto_id, _cargo_id = setup
    assert repo.list_all() == []


def test_list_all_ordenado_por_apellido_luego_nombre(
    setup: Tuple[EmpleadoRepositorySQLite, Database, int, int],
) -> None:
    repo, _db, depto_id, cargo_id = setup
    repo.create(
        _nuevo_empleado(
            depto_id, cargo_id, dni="0501-1990-11111", apellidos="Zamora", nombres="Ana"
        )
    )
    repo.create(
        _nuevo_empleado(
            depto_id, cargo_id, dni="0501-1990-22222", apellidos="Acosta", nombres="Bruno"
        )
    )
    repo.create(
        _nuevo_empleado(
            depto_id, cargo_id, dni="0501-1990-33333", apellidos="Acosta", nombres="Ana"
        )
    )
    nombres = [(e.apellidos, e.nombres) for e in repo.list_all()]
    assert nombres == [("Acosta", "Ana"), ("Acosta", "Bruno"), ("Zamora", "Ana")]


def test_list_active_excluye_archivados(
    setup: Tuple[EmpleadoRepositorySQLite, Database, int, int],
) -> None:
    repo, _db, depto_id, cargo_id = setup
    activo = repo.create(_nuevo_empleado(depto_id, cargo_id, dni="0501-1990-11111"))
    archivado = repo.create(
        _nuevo_empleado(depto_id, cargo_id, dni="0501-1990-22222", nombres="Arch")
    )
    assert archivado.id is not None
    repo.deactivate(
        archivado.id,
        fecha_baja="2026-01-15",
        motivo_baja=MotivoBaja.RENUNCIA.value,
        nota_baja=None,
    )
    activos = repo.list_active()
    assert len(activos) == 1
    assert activos[0].id == activo.id


def test_list_by_departamento(
    setup: Tuple[EmpleadoRepositorySQLite, Database, int, int],
) -> None:
    repo, db, depto_id, cargo_id = setup
    # Creamos un segundo depto y ponemos empleados ahí — usamos el Database
    # inyectado por la fixture (no tocamos estado privado del repo).
    depto_repo = DepartamentoRepositorySQLite(db)
    otro = depto_repo.create(Departamento(id=None, nombre="Otro"))
    assert otro.id is not None
    repo.create(_nuevo_empleado(depto_id, cargo_id, dni="0501-1990-11111"))
    repo.create(_nuevo_empleado(otro.id, cargo_id, dni="0501-1990-22222"))
    repo.create(_nuevo_empleado(otro.id, cargo_id, dni="0501-1990-33333"))
    assert len(repo.list_by_departamento(depto_id)) == 1
    assert len(repo.list_by_departamento(otro.id)) == 2


def test_list_by_departamento_solo_activos_false_incluye_archivados(
    setup: Tuple[EmpleadoRepositorySQLite, Database, int, int],
) -> None:
    repo, _db, depto_id, cargo_id = setup
    activo = repo.create(_nuevo_empleado(depto_id, cargo_id, dni="0501-1990-11111"))
    archivado = repo.create(_nuevo_empleado(depto_id, cargo_id, dni="0501-1990-22222"))
    assert archivado.id is not None
    repo.deactivate(
        archivado.id,
        fecha_baja="2026-01-15",
        motivo_baja=MotivoBaja.DESPIDO.value,
        nota_baja=None,
    )
    # Default (solo_activos=True) filtra.
    assert len(repo.list_by_departamento(depto_id)) == 1
    # solo_activos=False trae los dos.
    todos = repo.list_by_departamento(depto_id, solo_activos=False)
    assert {e.id for e in todos} == {activo.id, archivado.id}


def test_list_by_cargo(
    setup: Tuple[EmpleadoRepositorySQLite, Database, int, int],
) -> None:
    repo, db, depto_id, cargo_id = setup
    cargo_repo = CargoRepositorySQLite(db)
    otro = cargo_repo.create(Cargo(id=None, nombre="Otro"))
    assert otro.id is not None
    repo.create(_nuevo_empleado(depto_id, cargo_id, dni="0501-1990-11111"))
    repo.create(_nuevo_empleado(depto_id, otro.id, dni="0501-1990-22222"))
    assert len(repo.list_by_cargo(cargo_id)) == 1
    assert len(repo.list_by_cargo(otro.id)) == 1


# ── update ────────────────────────────────────────────────────────────────────


def test_update_modifica_campos_editables(
    setup: Tuple[EmpleadoRepositorySQLite, Database, int, int],
) -> None:
    repo, _db, depto_id, cargo_id = setup
    creado = repo.create(_nuevo_empleado(depto_id, cargo_id))
    assert creado.id is not None
    creado.nombres = "Juan Carlos"
    creado.telefono = "8888-1111"
    creado.email = "jc@example.com"
    creado.zkteco_id = 99
    repo.update(creado)
    leido = repo.get_by_id(creado.id)
    assert leido is not None
    assert leido.nombres == "Juan Carlos"
    assert leido.telefono == "8888-1111"
    assert leido.email == "jc@example.com"
    assert leido.zkteco_id == 99


def test_update_no_afecta_is_active_ni_baja(
    setup: Tuple[EmpleadoRepositorySQLite, Database, int, int],
) -> None:
    """update() NO toca is_active ni los campos de baja."""
    repo, _db, depto_id, cargo_id = setup
    creado = repo.create(_nuevo_empleado(depto_id, cargo_id))
    assert creado.id is not None
    # Intentamos "colar" un cambio de estado vía update — no debe aplicar.
    creado.is_active = False
    creado.fecha_baja = "2026-01-01"
    creado.motivo_baja = "DESPIDO"
    repo.update(creado)
    leido = repo.get_by_id(creado.id)
    assert leido is not None
    assert leido.is_active is True
    assert leido.fecha_baja is None
    assert leido.motivo_baja is None


def test_update_sin_id_levanta_value_error(
    setup: Tuple[EmpleadoRepositorySQLite, Database, int, int],
) -> None:
    repo, _db, depto_id, cargo_id = setup
    emp = _nuevo_empleado(depto_id, cargo_id)
    with pytest.raises(ValueError):
        repo.update(emp)


# ── deactivate / reactivate ───────────────────────────────────────────────────


def test_deactivate_marca_baja(
    setup: Tuple[EmpleadoRepositorySQLite, Database, int, int],
) -> None:
    repo, _db, depto_id, cargo_id = setup
    creado = repo.create(_nuevo_empleado(depto_id, cargo_id))
    assert creado.id is not None
    repo.deactivate(
        creado.id,
        fecha_baja="2026-03-15",
        motivo_baja=MotivoBaja.DESPIDO.value,
        nota_baja=None,
    )
    leido = repo.get_by_id(creado.id)
    assert leido is not None
    assert leido.is_active is False
    assert leido.fecha_baja == "2026-03-15"
    assert leido.motivo_baja == "DESPIDO"
    assert leido.nota_baja is None


def test_deactivate_con_motivo_otro_y_nota(
    setup: Tuple[EmpleadoRepositorySQLite, Database, int, int],
) -> None:
    repo, _db, depto_id, cargo_id = setup
    creado = repo.create(_nuevo_empleado(depto_id, cargo_id))
    assert creado.id is not None
    repo.deactivate(
        creado.id,
        fecha_baja="2026-03-15",
        motivo_baja=MotivoBaja.OTRO.value,
        nota_baja="Motivo especial documentado",
    )
    leido = repo.get_by_id(creado.id)
    assert leido is not None
    assert leido.motivo_baja == "OTRO"
    assert leido.nota_baja == "Motivo especial documentado"


def test_deactivate_con_motivo_invalido_falla(
    setup: Tuple[EmpleadoRepositorySQLite, Database, int, int],
) -> None:
    """El CHECK del schema debe rechazar motivos fuera del catálogo."""
    repo, _db, depto_id, cargo_id = setup
    creado = repo.create(_nuevo_empleado(depto_id, cargo_id))
    assert creado.id is not None
    with pytest.raises(sqlite3.IntegrityError):
        repo.deactivate(
            creado.id,
            fecha_baja="2026-03-15",
            motivo_baja="INVENTADO",
            nota_baja=None,
        )


def test_reactivate_limpia_campos_de_baja(
    setup: Tuple[EmpleadoRepositorySQLite, Database, int, int],
) -> None:
    repo, _db, depto_id, cargo_id = setup
    creado = repo.create(_nuevo_empleado(depto_id, cargo_id))
    assert creado.id is not None
    repo.deactivate(
        creado.id,
        fecha_baja="2026-03-15",
        motivo_baja=MotivoBaja.RENUNCIA.value,
        nota_baja=None,
    )
    repo.reactivate(creado.id)
    leido = repo.get_by_id(creado.id)
    assert leido is not None
    assert leido.is_active is True
    assert leido.fecha_baja is None
    assert leido.motivo_baja is None
    assert leido.nota_baja is None
