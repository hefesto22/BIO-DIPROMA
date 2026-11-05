"""Instalación nueva: usuarios admin y administrador, y el turno Operativo."""

from __future__ import annotations

from pathlib import Path

import pytest

import config
from core.models.turno import DIAS_LABORALES, SABADO
from core.repositories.audit_log_repository_sqlite import AuditLogRepositorySQLite
from core.repositories.rol_repository_sqlite import RolRepositorySQLite
from core.repositories.turno_repository_sqlite import TurnoRepositorySQLite
from core.repositories.usuario_repository_sqlite import UsuarioRepositorySQLite
from core.services.audit_logger import AuditLogger
from core.services.instalacion_service import CLAVE_INICIAL, InstalacionService
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner
from infrastructure.security.bcrypt_hasher import BcryptHasher


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "t.db")
    MigrationsRunner(database, config.MIGRATIONS_DIR).run()
    return database


def _servicio(db: Database) -> InstalacionService:
    usuarios = UsuarioRepositorySQLite(db)
    turnos = TurnoRepositorySQLite(db)
    return InstalacionService(
        usuario_read=usuarios,
        usuario_write=usuarios,
        rol_read=RolRepositorySQLite(db),
        turno_read=turnos,
        turno_write=turnos,
        hasher=BcryptHasher(12),
        audit_logger=AuditLogger(AuditLogRepositorySQLite(db)),
    )


def test_base_nueva_trae_superadmin_administrador_y_turno(db: Database) -> None:
    resultado = _servicio(db).preparar()
    assert resultado.usuarios == ("admin", "administrador")
    assert resultado.turno == "Operativo"

    roles = RolRepositorySQLite(db)
    usuarios = {u.username: u for u in UsuarioRepositorySQLite(db).list_all()}
    for username, codigo, nombre in (
        ("admin", "SUPERADMIN", "Super Administrador"),
        ("administrador", "ADMIN", "Administrador"),
    ):
        u = usuarios[username]
        rol = roles.get_by_id(u.role_id)
        assert rol is not None and rol.code == codigo
        assert u.full_name == nombre and u.is_active
        assert BcryptHasher(12).verify(CLAVE_INICIAL, u.password_hash)

    # Acceso total: el Administrador tiene los mismos permisos que el Super Admin.
    admin_rol, super_rol = roles.get_by_code("ADMIN"), roles.get_by_code("SUPERADMIN")
    assert admin_rol is not None and super_rol is not None
    assert admin_rol.permissions == super_rol.permissions


def test_turno_operativo_de_7_a_5_con_almuerzo_y_sabado(db: Database) -> None:
    _servicio(db).preparar()
    (turno,) = TurnoRepositorySQLite(db).list_all()
    assert (turno.nombre, turno.hora_entrada, turno.hora_salida) == ("Operativo", "07:00", "17:00")
    assert turno.dias_semana == DIAS_LABORALES
    assert (turno.almuerzo_inicio, turno.almuerzo_fin, turno.minutos_descanso) == (
        "12:00",
        "13:00",
        60,
    )
    (sabado,) = turno.horarios_extra
    assert sabado.dias_semana == SABADO
    assert (sabado.hora_entrada, sabado.hora_salida, sabado.minutos_descanso) == (
        "07:00",
        "12:00",
        0,
    )
    assert sabado.almuerzo_inicio is None


def test_no_hace_nada_en_una_base_en_uso(db: Database) -> None:
    servicio = _servicio(db)
    servicio.preparar()
    segundo = servicio.preparar()
    assert not segundo.hizo_algo
    assert len(UsuarioRepositorySQLite(db).list_all()) == 2
    assert len(TurnoRepositorySQLite(db).list_all()) == 1


def test_si_ya_hay_turnos_solo_crea_los_usuarios(db: Database) -> None:
    turnos = TurnoRepositorySQLite(db)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO turnos (nombre, hora_entrada, hora_salida, dias_semana) "
            "VALUES ('Propio', '08:00', '16:00', 124)"
        )
    resultado = _servicio(db).preparar()
    assert resultado.usuarios == ("admin", "administrador") and resultado.turno is None
    assert [t.nombre for t in turnos.list_all()] == ["Propio"]


def test_deja_constancia_en_la_bitacora(db: Database) -> None:
    _servicio(db).preparar()
    with db.transaction() as conn:
        fila = conn.execute(
            "SELECT details FROM audit_log WHERE action = 'instalacion_inicial'"
        ).fetchone()
    assert fila is not None and "administrador" in fila[0] and "Operativo" in fila[0]
