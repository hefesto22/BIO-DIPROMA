"""Tests del ``DispositivoConfigService``.

Integración con ``DispositivoRepositorySQLite`` real sobre ``tmp_path`` +
``AuditLogger`` real. Cubre CRUD, validaciones (IP/puerto/nombre),
duplicados (por nombre y por endpoint), idempotencia de archive/unarchive,
y verificación de que cada mutación persiste audit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Tuple

import pytest

from core.repositories.audit_log_repository_sqlite import (
    AuditLogRepositorySQLite,
)
from core.repositories.dispositivo_repository_sqlite import (
    DispositivoRepositorySQLite,
)
from core.services.audit_logger import AuditLogger
from core.services.dispositivo_config_service import DispositivoConfigService
from core.services.errors import (
    DispositivoNotFoundError,
    DuplicateDispositivoEndpointError,
    DuplicateDispositivoNombreError,
    InvalidIPError,
    InvalidPuertoError,
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
def setup(tmp_path: Path) -> Tuple[DispositivoConfigService, Database]:
    """Crea DB + servicio con sus dependencias reales."""
    db = Database(tmp_path / "test_dispositivo_config.db")
    MigrationsRunner(db, MIGRATIONS_DIR).run()
    _seed_actor_user(db)
    repo = DispositivoRepositorySQLite(db)
    service = DispositivoConfigService(
        dispositivo_read=repo,
        dispositivo_write=repo,
        audit_logger=AuditLogger(AuditLogRepositorySQLite(db), machine_name="test"),
    )
    return service, db


def _count_audit(db: Database, action: str) -> int:
    with db.transaction() as conn:
        cur = conn.execute("SELECT COUNT(*) AS n FROM audit_log WHERE action = ?", (action,))
        return int(cur.fetchone()["n"])


# ── Create — happy path y normalización ───────────────────────────────────────


def test_create_dispositivo_ok(
    setup: Tuple[DispositivoConfigService, Database],
) -> None:
    service, db = setup
    disp = service.create_dispositivo(
        nombre="Sede Principal",
        ip="192.168.0.101",
        puerto=4370,
        actor_user_id=ACTOR_ID,
    )
    assert disp.id is not None
    assert disp.nombre == "Sede Principal"
    assert disp.ip == "192.168.0.101"
    assert disp.puerto == 4370
    assert disp.is_active is True
    assert _count_audit(db, "dispositivo_created") == 1


def test_create_dispositivo_normaliza_nombre(
    setup: Tuple[DispositivoConfigService, Database],
) -> None:
    service, _ = setup
    disp = service.create_dispositivo("  Bodega   Obras  ", "10.0.0.5", 4370, ACTOR_ID)
    assert disp.nombre == "Bodega Obras"


def test_create_dispositivo_persiste_ip_canonica(
    setup: Tuple[DispositivoConfigService, Database],
) -> None:
    """La IP guardada en BD es la forma canónica devuelta por IPv4Address."""
    service, _ = setup
    disp = service.create_dispositivo("Reloj", "192.168.1.10", 4370, ACTOR_ID)
    assert disp.ip == "192.168.1.10"


def test_create_dispositivo_recorta_espacios_en_ip(
    setup: Tuple[DispositivoConfigService, Database],
) -> None:
    """Los espacios alrededor de la IP se eliminan antes de validar."""
    service, _ = setup
    disp = service.create_dispositivo("Reloj", "  192.168.1.10  ", 4370, ACTOR_ID)
    assert disp.ip == "192.168.1.10"


# ── Create — validación de campo ──────────────────────────────────────────────


def test_create_dispositivo_nombre_vacio_levanta_missing(
    setup: Tuple[DispositivoConfigService, Database],
) -> None:
    service, _ = setup
    with pytest.raises(MissingRequiredFieldError):
        service.create_dispositivo("   ", "192.168.0.101", 4370, ACTOR_ID)


@pytest.mark.parametrize(
    "ip_invalida",
    [
        "",
        "no-es-ip",
        "192.168.0",
        "192.168.0.300",
        "::1",
        "fe80::1",
        "192.168.0.1.5",
        "256.256.256.256",
    ],
)
def test_create_dispositivo_ip_invalida_levanta_invalid_ip(
    setup: Tuple[DispositivoConfigService, Database],
    ip_invalida: str,
) -> None:
    service, _ = setup
    with pytest.raises(InvalidIPError):
        service.create_dispositivo("Reloj", ip_invalida, 4370, ACTOR_ID)


@pytest.mark.parametrize("puerto_invalido", [0, -1, 65536, 100000])
def test_create_dispositivo_puerto_fuera_de_rango_levanta_invalid_puerto(
    setup: Tuple[DispositivoConfigService, Database],
    puerto_invalido: int,
) -> None:
    service, _ = setup
    with pytest.raises(InvalidPuertoError):
        service.create_dispositivo("Reloj", "192.168.0.101", puerto_invalido, ACTOR_ID)


def test_create_dispositivo_puerto_bool_rechazado(
    setup: Tuple[DispositivoConfigService, Database],
) -> None:
    """``True``/``False`` son ``int`` en Python — rechazar explícito."""
    service, _ = setup
    with pytest.raises(InvalidPuertoError):
        # Pasar ``True`` (que es ``int`` en Python) debe ser rechazado en runtime
        # por la validación explícita ``isinstance(puerto, bool)`` del servicio.
        service.create_dispositivo("Reloj", "192.168.0.101", True, ACTOR_ID)


# ── Create — duplicados ───────────────────────────────────────────────────────


def test_create_dispositivo_duplicate_nombre(
    setup: Tuple[DispositivoConfigService, Database],
) -> None:
    service, _ = setup
    service.create_dispositivo("Sede Principal", "192.168.0.101", 4370, ACTOR_ID)
    with pytest.raises(DuplicateDispositivoNombreError):
        service.create_dispositivo("Sede Principal", "10.0.0.5", 4370, ACTOR_ID)


def test_create_dispositivo_duplicate_endpoint(
    setup: Tuple[DispositivoConfigService, Database],
) -> None:
    service, _ = setup
    service.create_dispositivo("Sede Principal", "192.168.0.101", 4370, ACTOR_ID)
    with pytest.raises(DuplicateDispositivoEndpointError):
        service.create_dispositivo("Otra etiqueta", "192.168.0.101", 4370, ACTOR_ID)


# ── Update ────────────────────────────────────────────────────────────────────


def test_update_dispositivo_cambia_campos(
    setup: Tuple[DispositivoConfigService, Database],
) -> None:
    service, db = setup
    disp = service.create_dispositivo("Reloj A", "192.168.0.101", 4370, ACTOR_ID)
    assert disp.id is not None

    service.update_dispositivo(
        dispositivo_id=disp.id,
        nombre="Reloj A renombrado",
        ip="192.168.0.150",
        puerto=4371,
        actor_user_id=ACTOR_ID,
    )

    actualizado = service.get_dispositivo(disp.id)
    assert actualizado.nombre == "Reloj A renombrado"
    assert actualizado.ip == "192.168.0.150"
    assert actualizado.puerto == 4371
    assert _count_audit(db, "dispositivo_updated") == 1


def test_update_dispositivo_sin_cambios_es_noop(
    setup: Tuple[DispositivoConfigService, Database],
) -> None:
    service, db = setup
    disp = service.create_dispositivo("Reloj A", "192.168.0.101", 4370, ACTOR_ID)
    assert disp.id is not None
    service.update_dispositivo(disp.id, "Reloj A", "192.168.0.101", 4370, ACTOR_ID)
    assert _count_audit(db, "dispositivo_updated") == 0


def test_update_dispositivo_id_inexistente_falla(
    setup: Tuple[DispositivoConfigService, Database],
) -> None:
    service, _ = setup
    with pytest.raises(DispositivoNotFoundError):
        service.update_dispositivo(999, "X", "192.168.0.101", 4370, ACTOR_ID)


def test_update_dispositivo_nombre_colision_falla(
    setup: Tuple[DispositivoConfigService, Database],
) -> None:
    service, _ = setup
    service.create_dispositivo("Reloj A", "192.168.0.101", 4370, ACTOR_ID)
    disp_b = service.create_dispositivo("Reloj B", "10.0.0.5", 4370, ACTOR_ID)
    assert disp_b.id is not None
    with pytest.raises(DuplicateDispositivoNombreError):
        service.update_dispositivo(disp_b.id, "Reloj A", "10.0.0.5", 4370, ACTOR_ID)


def test_update_dispositivo_endpoint_colision_falla(
    setup: Tuple[DispositivoConfigService, Database],
) -> None:
    service, _ = setup
    service.create_dispositivo("Reloj A", "192.168.0.101", 4370, ACTOR_ID)
    disp_b = service.create_dispositivo("Reloj B", "10.0.0.5", 4370, ACTOR_ID)
    assert disp_b.id is not None
    with pytest.raises(DuplicateDispositivoEndpointError):
        service.update_dispositivo(disp_b.id, "Reloj B", "192.168.0.101", 4370, ACTOR_ID)


def test_update_dispositivo_misma_fila_no_es_colision(
    setup: Tuple[DispositivoConfigService, Database],
) -> None:
    """Editar la misma fila con sus mismos valores no debe disparar
    DuplicateDispositivoNombreError aunque ``get_by_nombre`` la encuentre."""
    service, _ = setup
    disp = service.create_dispositivo("Reloj A", "192.168.0.101", 4370, ACTOR_ID)
    assert disp.id is not None
    # Cambiar solo el puerto manteniendo nombre+IP — el pre-check encuentra
    # la propia fila pero el chequeo `id != dispositivo_id` la ignora.
    service.update_dispositivo(disp.id, "Reloj A", "192.168.0.101", 4371, ACTOR_ID)
    actualizado = service.get_dispositivo(disp.id)
    assert actualizado.puerto == 4371


# ── Archive / Unarchive ───────────────────────────────────────────────────────


def test_archive_dispositivo_marca_inactivo(
    setup: Tuple[DispositivoConfigService, Database],
) -> None:
    service, db = setup
    disp = service.create_dispositivo("Reloj A", "192.168.0.101", 4370, ACTOR_ID)
    assert disp.id is not None
    service.archive_dispositivo(disp.id, ACTOR_ID)
    archivado = service.get_dispositivo(disp.id)
    assert archivado.is_active is False
    assert _count_audit(db, "dispositivo_archived") == 1


def test_archive_dispositivo_idempotente(
    setup: Tuple[DispositivoConfigService, Database],
) -> None:
    service, db = setup
    disp = service.create_dispositivo("Reloj A", "192.168.0.101", 4370, ACTOR_ID)
    assert disp.id is not None
    service.archive_dispositivo(disp.id, ACTOR_ID)
    service.archive_dispositivo(disp.id, ACTOR_ID)  # segundo no escribe audit
    assert _count_audit(db, "dispositivo_archived") == 1


def test_archive_dispositivo_inexistente_falla(
    setup: Tuple[DispositivoConfigService, Database],
) -> None:
    service, _ = setup
    with pytest.raises(DispositivoNotFoundError):
        service.archive_dispositivo(999, ACTOR_ID)


def test_unarchive_dispositivo_reactiva(
    setup: Tuple[DispositivoConfigService, Database],
) -> None:
    service, db = setup
    disp = service.create_dispositivo("Reloj A", "192.168.0.101", 4370, ACTOR_ID)
    assert disp.id is not None
    service.archive_dispositivo(disp.id, ACTOR_ID)
    service.unarchive_dispositivo(disp.id, ACTOR_ID)
    reactivado = service.get_dispositivo(disp.id)
    assert reactivado.is_active is True
    assert _count_audit(db, "dispositivo_unarchived") == 1


def test_unarchive_dispositivo_idempotente_si_ya_activo(
    setup: Tuple[DispositivoConfigService, Database],
) -> None:
    service, db = setup
    disp = service.create_dispositivo("Reloj A", "192.168.0.101", 4370, ACTOR_ID)
    assert disp.id is not None
    service.unarchive_dispositivo(disp.id, ACTOR_ID)  # ya activo
    assert _count_audit(db, "dispositivo_unarchived") == 0


# ── Listado ───────────────────────────────────────────────────────────────────


def test_list_dispositivos_solo_activos_por_default(
    setup: Tuple[DispositivoConfigService, Database],
) -> None:
    service, _ = setup
    a = service.create_dispositivo("Reloj A", "192.168.0.101", 4370, ACTOR_ID)
    b = service.create_dispositivo("Reloj B", "10.0.0.5", 4370, ACTOR_ID)
    assert a.id is not None and b.id is not None
    service.archive_dispositivo(b.id, ACTOR_ID)

    activos = service.list_dispositivos()
    nombres_activos = {d.nombre for d in activos}
    assert nombres_activos == {"Reloj A"}


def test_list_dispositivos_incluye_archivados_si_se_pide(
    setup: Tuple[DispositivoConfigService, Database],
) -> None:
    service, _ = setup
    service.create_dispositivo("Reloj A", "192.168.0.101", 4370, ACTOR_ID)
    b = service.create_dispositivo("Reloj B", "10.0.0.5", 4370, ACTOR_ID)
    assert b.id is not None
    service.archive_dispositivo(b.id, ACTOR_ID)

    todos = service.list_dispositivos(solo_activos=False)
    nombres = {d.nombre for d in todos}
    assert nombres == {"Reloj A", "Reloj B"}


def test_get_dispositivo_inexistente_falla(
    setup: Tuple[DispositivoConfigService, Database],
) -> None:
    service, _ = setup
    with pytest.raises(DispositivoNotFoundError):
        service.get_dispositivo(999)
