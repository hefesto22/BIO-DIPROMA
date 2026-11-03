"""Script de seed de datos de prueba para entornos de QA.

Se ejecuta con:

    python -m bin.seed_test_data
    python -m bin.seed_test_data --db-path "dist/zkteco/data/zkteco_app.db"

Comportamiento:
    - Por defecto conecta a la BD que resuelve ``config.DATABASE_PATH``
      (modo dev = ``data/zkteco_app.db`` en la raíz del repo).
    - Con ``--db-path`` apunta a otra BD — útil para sembrar la BD que
      usa el .exe empaquetado, que vive en ``dist/zkteco/data/``.
    - Si existe un usuario activo, lo usa como ``actor_user_id`` para el
      audit log; si no, aborta con instrucción de correr el setup_wizard
      primero contra la misma BD.
    - Crea (idempotente) los catálogos y empleados de prueba listados
      abajo. Si ya existen los DNI/nombres canónicos, no falla — los
      omite e informa.
    - Imprime un resumen al final.

Datos sembrados (todos marcados con prefijo "TEST" o DNIs 9999-* para
distinguirlos de datos reales y poder borrarlos fácil después):

    Departamento:
        · "TEST"
    Cargo:
        · "TEST"
    Turno:
        · "Test Diurno 08-17"  (08:00-17:00, L-V, 60min descanso, tol 10/0)
    Empleados:
        · DNI 9999-9999-00001 — "Test Indice"  — ZKTeco ID 1
        · DNI 9999-9999-00002 — "Test Medio"   — ZKTeco ID 2
        · DNI 9999-9999-00003 — "Test Anular"  — ZKTeco ID 3
    Asignaciones de turno:
        · Cada empleado de test → "Test Diurno 08-17" desde 2026-01-01.

Para limpiar antes de producción:
    Archivar manualmente desde la UI los empleados con DNI que empieza
    con "9999-", el turno "Test Diurno 08-17", y el cargo/dep "TEST".
    El audit_log queda como historia.

Este script NO se ejecuta automáticamente en producción — solo cuando
el operador lo corre explícitamente desde terminal.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

import config
from core.repositories.audit_log_repository_sqlite import AuditLogRepositorySQLite
from core.repositories.cargo_repository_sqlite import CargoRepositorySQLite
from core.repositories.departamento_repository_sqlite import (
    DepartamentoRepositorySQLite,
)
from core.repositories.empleado_repository_sqlite import EmpleadoRepositorySQLite
from core.repositories.empleado_turno_repository_sqlite import (
    EmpleadoTurnoRepositorySQLite,
)
from core.repositories.turno_repository_sqlite import TurnoRepositorySQLite
from core.repositories.usuario_repository_sqlite import UsuarioRepositorySQLite
from core.services.audit_logger import AuditLogger
from core.services.catalogo_service import CatalogoService
from core.services.empleado_service import EmpleadoService
from core.services.errors import (
    DuplicateDNIError,
    DuplicateNombreError,
    DuplicateTurnoNombreError,
    DuplicateZktecoIdError,
    TurnoYaAsignadoError,
)
from core.services.turno_service import TurnoService
from core.models.turno import DIAS_LABORALES
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner

# Datos de prueba — modificá acá si querés agregar más empleados u otros catálogos.
_DEPARTAMENTO_TEST = "TEST"
_CARGO_TEST = "TEST"
_TURNO_TEST_NOMBRE = "Test Diurno 08-17"
_TURNO_TEST_HORA_ENTRADA = "08:00"
_TURNO_TEST_HORA_SALIDA = "17:00"
_TURNO_TEST_DESCANSO_MIN = 60
_TURNO_TEST_DIAS = DIAS_LABORALES  # bitmask Lun-Vie
_FECHA_INGRESO_TEST = "2026-01-01"

# Empleados a sembrar: (dni, nombres, apellidos, zkteco_id, dedo_etiqueta).
# Solo los 3 que Mauricio enroló en el K40. Si enrola más, agregar acá.
_EMPLEADOS_TEST = [
    ("9999-9999-00001", "Test", "Indice", 1, "índice"),
    ("9999-9999-00002", "Test", "Medio", 2, "medio"),
    ("9999-9999-00003", "Test", "Anular", 3, "anular"),
]


def main(argv: Optional[list[str]] = None) -> int:
    """Ejecuta el seed. Devuelve el exit code.

    Args:
        argv: Lista de argumentos (sin ``argv[0]``). Si es ``None`` usa
            ``sys.argv[1:]``. Permite pasar args programáticamente desde
            tests.
    """
    args = _parse_args(argv)
    logging.basicConfig(level=config.LOG_LEVEL, format=config.LOG_FORMAT)

    db_path = _resolver_db_path(args.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    database = Database(db_path)
    MigrationsRunner(database, config.MIGRATIONS_DIR).run()

    actor_id = _resolver_actor_user_id(database)
    if actor_id is None:
        print(
            f"✗ No se encontró ningún usuario activo en {db_path}."
            "\n  Corré primero el setup_wizard contra esa misma BD:"
            "\n    python -m bin.setup_wizard"
        )
        return 1

    print("═══════════════════════════════════════════════════════════════")
    print(f"  Seed de datos de prueba — {config.APP_NAME}")
    print(f"  BD: {db_path}")
    print(f"  Actor (audit): user_id={actor_id}")
    print("═══════════════════════════════════════════════════════════════")

    catalogo_svc, turno_svc, empleado_svc = _build_services(database)
    creados: dict[str, int] = {
        "departamento": 0,
        "cargo": 0,
        "turno": 0,
        "empleado": 0,
        "asignacion_turno": 0,
    }

    departamento_id = _ensure_departamento(catalogo_svc, actor_id, creados)
    cargo_id = _ensure_cargo(catalogo_svc, actor_id, creados)
    turno_id = _ensure_turno(turno_svc, actor_id, creados)

    for dni, nombres, apellidos, zkteco_id, dedo in _EMPLEADOS_TEST:
        emp_id = _ensure_empleado(
            empleado_svc,
            dni=dni,
            nombres=nombres,
            apellidos=apellidos,
            zkteco_id=zkteco_id,
            dedo=dedo,
            departamento_id=departamento_id,
            cargo_id=cargo_id,
            actor_id=actor_id,
            creados=creados,
        )
        if emp_id is not None:
            _ensure_asignacion_turno(
                empleado_svc,
                empleado_id=emp_id,
                turno_id=turno_id,
                actor_id=actor_id,
                creados=creados,
            )

    print()
    print("───────────────────────────────────────────────────────────────")
    print("  Resumen del seed:")
    print(f"    · Departamentos creados: {creados['departamento']}")
    print(f"    · Cargos creados:        {creados['cargo']}")
    print(f"    · Turnos creados:        {creados['turno']}")
    print(f"    · Empleados creados:     {creados['empleado']}")
    print(f"    · Asignaciones de turno: {creados['asignacion_turno']}")
    print("───────────────────────────────────────────────────────────────")
    print("  Listo. Ya podés marcar en el K40, sincronizar y ver reportes.")
    return 0


# ── Argumentos y resolución de BD ────────────────────────────────────────────


def _parse_args(argv: Optional[list[str]]) -> argparse.Namespace:
    """Parsea ``--db-path`` y devuelve el ``Namespace``."""
    parser = argparse.ArgumentParser(
        prog="bin.seed_test_data",
        description=(
            "Siembra datos de prueba en la BD indicada. Si no se pasa "
            "--db-path usa la BD por defecto del config."
        ),
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help=(
            "Ruta al archivo SQLite a sembrar. Útil para apuntar a la BD "
            "del .exe empaquetado (p.ej. dist/zkteco/data/zkteco_app.db)."
        ),
    )
    return parser.parse_args(argv)


def _resolver_db_path(explicit: Optional[str]) -> Path:
    """Devuelve el ``Path`` absoluto de la BD a sembrar.

    Si el caller pasó ``--db-path`` se usa ese valor (resuelto contra el
    cwd actual). Si no, cae al ``config.DATABASE_PATH`` que ya viene
    pre-resuelto por ``infrastructure.paths``.
    """
    if explicit is not None:
        return Path(explicit).resolve()
    return config.DATABASE_PATH


# ── Composition root del seed ────────────────────────────────────────────────


def _build_services(
    database: Database,
) -> tuple[CatalogoService, TurnoService, EmpleadoService]:
    """Compone los 3 services que el seed necesita con sus repos reales."""
    audit = AuditLogger(AuditLogRepositorySQLite(database))
    dep_repo = DepartamentoRepositorySQLite(database)
    cargo_repo = CargoRepositorySQLite(database)
    emp_repo = EmpleadoRepositorySQLite(database)
    emp_turno_repo = EmpleadoTurnoRepositorySQLite(database)
    turno_repo = TurnoRepositorySQLite(database)

    catalogo_svc = CatalogoService(
        dep_read=dep_repo,
        dep_write=dep_repo,
        cargo_read=cargo_repo,
        cargo_write=cargo_repo,
        empleado_read=emp_repo,
        audit_logger=audit,
    )
    turno_svc = TurnoService(
        turno_read=turno_repo,
        turno_write=turno_repo,
        audit_logger=audit,
    )
    empleado_svc = EmpleadoService(
        empleado_read=emp_repo,
        empleado_write=emp_repo,
        empleado_turno_read=emp_turno_repo,
        empleado_turno_write=emp_turno_repo,
        departamento_read=dep_repo,
        cargo_read=cargo_repo,
        turno_read=turno_repo,
        audit_logger=audit,
    )
    return catalogo_svc, turno_svc, empleado_svc


def _resolver_actor_user_id(database: Database) -> Optional[int]:
    """Devuelve el user_id del primer usuario activo, o ``None``.

    Cualquier usuario activo sirve como actor del audit log para los
    inserts de seed; no necesitamos exigir SUPERADMIN específicamente.
    """
    repo = UsuarioRepositorySQLite(database)
    for usuario in repo.list_all():
        if usuario.id is not None and usuario.is_active:
            return usuario.id
    return None


# ── Helpers idempotentes ─────────────────────────────────────────────────────


def _ensure_departamento(
    catalogo_svc: CatalogoService, actor_id: int, creados: dict[str, int]
) -> int:
    """Crea el departamento de test si no existe; devuelve su id."""
    for dep in catalogo_svc.list_departamentos(solo_activos=False):
        if dep.nombre == _DEPARTAMENTO_TEST and dep.id is not None:
            print(f'• Departamento "{_DEPARTAMENTO_TEST}" ya existe (id={dep.id}).')
            return dep.id
    try:
        creado = catalogo_svc.create_departamento(_DEPARTAMENTO_TEST, actor_id)
    except DuplicateNombreError:
        # Race condition extremadamente improbable; releemos.
        for dep in catalogo_svc.list_departamentos(solo_activos=False):
            if dep.nombre == _DEPARTAMENTO_TEST and dep.id is not None:
                return dep.id
        raise
    assert creado.id is not None
    creados["departamento"] += 1
    print(f'✓ Departamento "{_DEPARTAMENTO_TEST}" creado (id={creado.id}).')
    return creado.id


def _ensure_cargo(catalogo_svc: CatalogoService, actor_id: int, creados: dict[str, int]) -> int:
    """Crea el cargo de test si no existe; devuelve su id."""
    for cargo in catalogo_svc.list_cargos(solo_activos=False):
        if cargo.nombre == _CARGO_TEST and cargo.id is not None:
            print(f'• Cargo "{_CARGO_TEST}" ya existe (id={cargo.id}).')
            return cargo.id
    try:
        creado = catalogo_svc.create_cargo(_CARGO_TEST, actor_id)
    except DuplicateNombreError:
        for cargo in catalogo_svc.list_cargos(solo_activos=False):
            if cargo.nombre == _CARGO_TEST and cargo.id is not None:
                return cargo.id
        raise
    assert creado.id is not None
    creados["cargo"] += 1
    print(f'✓ Cargo "{_CARGO_TEST}" creado (id={creado.id}).')
    return creado.id


def _ensure_turno(turno_svc: TurnoService, actor_id: int, creados: dict[str, int]) -> int:
    """Crea el turno de test si no existe; devuelve su id."""
    for turno in turno_svc.list_turnos(solo_activos=False):
        if turno.nombre == _TURNO_TEST_NOMBRE and turno.id is not None:
            print(f'• Turno "{_TURNO_TEST_NOMBRE}" ya existe (id={turno.id}).')
            return turno.id
    try:
        creado = turno_svc.create_turno(
            nombre=_TURNO_TEST_NOMBRE,
            hora_entrada=_TURNO_TEST_HORA_ENTRADA,
            hora_salida=_TURNO_TEST_HORA_SALIDA,
            minutos_descanso=_TURNO_TEST_DESCANSO_MIN,
            dias_semana=_TURNO_TEST_DIAS,
            actor_user_id=actor_id,
        )
    except DuplicateTurnoNombreError:
        for turno in turno_svc.list_turnos(solo_activos=False):
            if turno.nombre == _TURNO_TEST_NOMBRE and turno.id is not None:
                return turno.id
        raise
    assert creado.id is not None
    creados["turno"] += 1
    print(f'✓ Turno "{_TURNO_TEST_NOMBRE}" creado (id={creado.id}).')
    return creado.id


def _ensure_empleado(
    empleado_svc: EmpleadoService,
    *,
    dni: str,
    nombres: str,
    apellidos: str,
    zkteco_id: int,
    dedo: str,
    departamento_id: int,
    cargo_id: int,
    actor_id: int,
    creados: dict[str, int],
) -> Optional[int]:
    """Crea el empleado si no existe; devuelve su id o None ante conflicto irreparable."""
    existente = empleado_svc.get_by_dni(dni) if hasattr(empleado_svc, "get_by_dni") else None
    if existente is not None and existente.id is not None:
        print(f'• Empleado "{nombres} {apellidos}" (DNI {dni}) ya existe (id={existente.id}).')
        return existente.id
    try:
        creado = empleado_svc.create_empleado(
            dni=dni,
            nombres=nombres,
            apellidos=apellidos,
            departamento_id=departamento_id,
            cargo_id=cargo_id,
            fecha_ingreso=_FECHA_INGRESO_TEST,
            actor_user_id=actor_id,
            zkteco_id=zkteco_id,
        )
    except DuplicateDNIError:
        print(f"• Empleado con DNI {dni} ya existe — se omite.")
        return None
    except DuplicateZktecoIdError:
        print(
            f"⚠ El ZKTeco ID {zkteco_id} ya está tomado por otro empleado — "
            f'no se creó "{nombres} {apellidos}". Verificá manualmente.'
        )
        return None
    assert creado.id is not None
    creados["empleado"] += 1
    print(
        f'✓ Empleado "{nombres} {apellidos}" creado '
        f"(id={creado.id}, ZKTeco ID {zkteco_id}, dedo {dedo})."
    )
    return creado.id


def _ensure_asignacion_turno(
    empleado_svc: EmpleadoService,
    *,
    empleado_id: int,
    turno_id: int,
    actor_id: int,
    creados: dict[str, int],
) -> None:
    """Asigna el turno al empleado si aún no tiene uno vigente."""
    vigente = empleado_svc.get_turno_vigente(empleado_id)
    if vigente is not None:
        print(f"  · Empleado id={empleado_id} ya tiene turno vigente (id={vigente.turno_id}).")
        return
    try:
        empleado_svc.asignar_turno(
            empleado_id=empleado_id,
            turno_id=turno_id,
            fecha_inicio=_FECHA_INGRESO_TEST,
            actor_user_id=actor_id,
        )
    except TurnoYaAsignadoError:
        # Defensa por si get_turno_vigente reportó None pero hay race.
        return
    creados["asignacion_turno"] += 1
    print(f"  · Turno asignado a empleado id={empleado_id} desde {_FECHA_INGRESO_TEST}.")


if __name__ == "__main__":
    sys.exit(main())
