"""Archivar pasando el personal a otro departamento o cargo, y la regla de
que un cargo no puede quedar "solo para X" si lo usan empleados de otros.
"""

from __future__ import annotations

from pathlib import Path
from typing import Tuple

import pytest

from core.models.empleado import Empleado
from core.repositories.audit_log_repository_sqlite import AuditLogRepositorySQLite
from core.repositories.cargo_repository_sqlite import CargoRepositorySQLite
from core.repositories.departamento_repository_sqlite import DepartamentoRepositorySQLite
from core.repositories.empleado_repository_sqlite import EmpleadoRepositorySQLite
from core.services.audit_logger import AuditLogger
from core.services.catalogo_service import CatalogoService, ResultadoMovimiento
from core.services.errors import (
    CargoIncompatibleError,
    CargoUsadoEnOtrosDepartamentosError,
    DestinoInvalidoError,
    lista_natural,
)
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner
from tests.unit.test_catalogo_service import (
    ACTOR_ID,
    MIGRATIONS_DIR,
    _count_audit,
    _seed_actor_user,
)

Setup = Tuple[CatalogoService, Database]


@pytest.fixture
def setup(tmp_path: Path) -> Setup:
    db = Database(tmp_path / "catalogo_mover.db")
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


_DNI = iter(f"0501-1990-{n:05d}" for n in range(10000, 99999))


def _empleado(db: Database, depto: int, cargo: int, activo: bool = True) -> Empleado:
    repo = EmpleadoRepositorySQLite(db)
    e = repo.create(
        Empleado(
            id=None,
            dni=next(_DNI),
            nombres="Ana",
            apellidos="Sosa",
            departamento_id=depto,
            cargo_id=cargo,
            fecha_ingreso="2024-01-01",
        )
    )
    assert e.id is not None
    if not activo:
        repo.deactivate(e.id, "2025-01-01", "RENUNCIA", None)
    return e


def _get(db: Database, empleado_id: int) -> Empleado:
    e = EmpleadoRepositorySQLite(db).get_by_id(empleado_id)
    assert e is not None
    return e


# ── Departamentos ─────────────────────────────────────────────────────────────


def test_archivar_departamento_moviendo_pasa_empleados_y_cargos(setup: Setup) -> None:
    service, db = setup
    bodega = service.create_departamento("Bodega", ACTOR_ID)
    logistica = service.create_departamento("Logística", ACTOR_ID)
    assert bodega.id is not None and logistica.id is not None
    auxiliar = service.create_cargo("Auxiliar de Bodega", ACTOR_ID, bodega.id)
    general = service.create_cargo("Conserje", ACTOR_ID)
    assert auxiliar.id is not None and general.id is not None
    a = _empleado(db, bodega.id, auxiliar.id)
    b = _empleado(db, bodega.id, general.id)
    baja = _empleado(db, bodega.id, general.id, activo=False)

    r = service.archive_departamento_moviendo(bodega.id, logistica.id, ACTOR_ID)

    assert r == ResultadoMovimiento(empleados=2, cargos=1)
    assert service.get_departamento(bodega.id).is_active is False
    assert _get(db, a.id or 0).departamento_id == logistica.id
    assert _get(db, b.id or 0).departamento_id == logistica.id
    # Los de baja conservan su historia.
    assert _get(db, baja.id or 0).departamento_id == bodega.id
    assert service.get_cargo(auxiliar.id).departamento_id == logistica.id
    assert service.get_cargo(general.id).departamento_id is None
    assert _count_audit(db, "departamento_merged") == 1


def test_archivar_departamento_moviendo_al_mismo_falla(setup: Setup) -> None:
    service, _ = setup
    d = service.create_departamento("Ventas", ACTOR_ID)
    assert d.id is not None
    with pytest.raises(DestinoInvalidoError, match="distinto"):
        service.archive_departamento_moviendo(d.id, d.id, ACTOR_ID)


def test_archivar_departamento_moviendo_a_archivado_falla(setup: Setup) -> None:
    service, _ = setup
    a = service.create_departamento("Ventas", ACTOR_ID)
    b = service.create_departamento("Viejo", ACTOR_ID)
    assert a.id is not None and b.id is not None
    service.archive_departamento(b.id, ACTOR_ID)
    with pytest.raises(DestinoInvalidoError, match="archivado"):
        service.archive_departamento_moviendo(a.id, b.id, ACTOR_ID)
    assert service.get_departamento(a.id).is_active is True


def test_archivar_departamento_moviendo_ya_archivado_no_hace_nada(setup: Setup) -> None:
    service, db = setup
    a = service.create_departamento("Viejo", ACTOR_ID)
    b = service.create_departamento("Nuevo", ACTOR_ID)
    assert a.id is not None and b.id is not None
    service.archive_departamento(a.id, ACTOR_ID)
    assert service.archive_departamento_moviendo(a.id, b.id, ACTOR_ID).empleados == 0
    assert _count_audit(db, "departamento_merged") == 0


# ── Cargos ────────────────────────────────────────────────────────────────────


def test_archivar_cargo_moviendo_pasa_solo_los_activos(setup: Setup) -> None:
    service, db = setup
    d = service.create_departamento("Ventas", ACTOR_ID)
    assert d.id is not None
    vendedora = service.create_cargo("Vendedora", ACTOR_ID)
    vendedor = service.create_cargo("Vendedor", ACTOR_ID)
    assert vendedora.id is not None and vendedor.id is not None
    a = _empleado(db, d.id, vendedora.id)
    baja = _empleado(db, d.id, vendedora.id, activo=False)

    r = service.archive_cargo_moviendo(vendedora.id, vendedor.id, ACTOR_ID)

    assert r.empleados == 1
    assert service.get_cargo(vendedora.id).is_active is False
    assert _get(db, a.id or 0).cargo_id == vendedor.id
    assert _get(db, baja.id or 0).cargo_id == vendedora.id
    assert _count_audit(db, "cargo_merged") == 1


def test_archivar_cargo_moviendo_a_cargo_de_otro_departamento_falla(setup: Setup) -> None:
    service, db = setup
    ventas = service.create_departamento("Ventas", ACTOR_ID)
    bodega = service.create_departamento("Bodega", ACTOR_ID)
    assert ventas.id is not None and bodega.id is not None
    general = service.create_cargo("Auxiliar", ACTOR_ID)
    cajero = service.create_cargo("Cajero", ACTOR_ID, ventas.id)
    assert general.id is not None and cajero.id is not None
    _empleado(db, bodega.id, general.id)
    _empleado(db, ventas.id, general.id)

    with pytest.raises(CargoIncompatibleError) as exc:
        service.archive_cargo_moviendo(general.id, cajero.id, ACTOR_ID)
    assert "1 empleado es de Bodega" in str(exc.value)
    assert service.get_cargo(general.id).is_active is True


def test_cargos_destino_solo_los_compatibles(setup: Setup) -> None:
    service, db = setup
    ventas = service.create_departamento("Ventas", ACTOR_ID)
    bodega = service.create_departamento("Bodega", ACTOR_ID)
    assert ventas.id is not None and bodega.id is not None
    origen = service.create_cargo("Vendedora", ACTOR_ID, ventas.id)
    general = service.create_cargo("Auxiliar", ACTOR_ID)
    de_ventas = service.create_cargo("Vendedor", ACTOR_ID, ventas.id)
    service.create_cargo("Bodeguero", ACTOR_ID, bodega.id)
    assert origen.id is not None
    _empleado(db, ventas.id, origen.id)

    nombres = {c.nombre for c in service.cargos_destino_para(origen.id)}

    assert nombres == {general.nombre, de_ventas.nombre}


def test_archivar_cargo_moviendo_al_mismo_falla(setup: Setup) -> None:
    service, _ = setup
    c = service.create_cargo("Cajero", ACTOR_ID)
    assert c.id is not None
    with pytest.raises(DestinoInvalidoError):
        service.archive_cargo_moviendo(c.id, c.id, ACTOR_ID)


# ── Regla: cargo propio de un departamento ───────────────────────────────────


def test_no_se_puede_dejar_cargo_solo_para_un_depto_si_lo_usan_otros(setup: Setup) -> None:
    service, db = setup
    ventas = service.create_departamento("Ventas", ACTOR_ID)
    bodega = service.create_departamento("Bodega", ACTOR_ID)
    gerencia = service.create_departamento("Gerencia", ACTOR_ID)
    assert ventas.id is not None and bodega.id is not None and gerencia.id is not None
    c = service.create_cargo("Asistente", ACTOR_ID)
    assert c.id is not None
    _empleado(db, ventas.id, c.id)
    _empleado(db, bodega.id, c.id)
    _empleado(db, gerencia.id, c.id)

    with pytest.raises(CargoUsadoEnOtrosDepartamentosError) as exc:
        service.set_cargo_departamento(c.id, ventas.id, ACTOR_ID)
    assert "lo tienen 2 empleados de Bodega y Gerencia" in str(exc.value)
    assert service.get_cargo(c.id).departamento_id is None


def test_cargo_puede_quedar_solo_para_su_unico_departamento(setup: Setup) -> None:
    service, db = setup
    ventas = service.create_departamento("Ventas", ACTOR_ID)
    bodega = service.create_departamento("Bodega", ACTOR_ID)
    assert ventas.id is not None and bodega.id is not None
    c = service.create_cargo("Cajero", ACTOR_ID)
    assert c.id is not None
    _empleado(db, ventas.id, c.id)
    # Un empleado de baja de otro departamento no lo impide.
    _empleado(db, bodega.id, c.id, activo=False)

    service.set_cargo_departamento(c.id, ventas.id, ACTOR_ID)

    assert service.get_cargo(c.id).departamento_id == ventas.id


def test_lista_natural() -> None:
    assert lista_natural([]) == ""
    assert lista_natural(["A"]) == "A"
    assert lista_natural(["A", "B"]) == "A y B"
    assert lista_natural(["A", "B", "C"]) == "A, B y C"
