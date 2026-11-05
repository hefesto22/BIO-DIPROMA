"""Tests de ``bin.crear_admin``: instalación nueva y recuperación del Super Administrador."""

from __future__ import annotations

from pathlib import Path

from bin import crear_admin
from core.repositories.rol_repository_sqlite import RolRepositorySQLite
from core.repositories.usuario_repository_sqlite import UsuarioRepositorySQLite
from infrastructure.database.connection import Database
from infrastructure.security.bcrypt_hasher import BcryptHasher


def _usuarios(db_path: Path) -> UsuarioRepositorySQLite:
    return UsuarioRepositorySQLite(Database(db_path))


def test_bd_nueva_queda_con_superadmin_y_administrador(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "zkteco_app.db"
    assert crear_admin.main(["--db-path", str(db_path)]) == 0
    todos = {u.username: u for u in _usuarios(db_path).list_all()}
    assert set(todos) == {"admin", "administrador"}
    roles = RolRepositorySQLite(Database(db_path))
    for username, codigo in (("admin", "SUPERADMIN"), ("administrador", "ADMIN")):
        usuario = todos[username]
        rol = roles.get_by_id(usuario.role_id)
        assert rol is not None and rol.code == codigo
        assert usuario.is_active
        assert BcryptHasher(12).verify("12345678", usuario.password_hash)


def test_no_crea_empleados_solo_turno_departamento_y_cargo(tmp_path: Path) -> None:
    db_path = tmp_path / "zkteco_app.db"
    crear_admin.main(["--db-path", str(db_path)])
    with Database(db_path).transaction() as conn:
        for tabla in ("empleados", "dispositivos", "asistencias"):
            assert conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0] == 0

        def nombres(tabla: str) -> list[str]:
            return [f[0] for f in conn.execute(f"SELECT nombre FROM {tabla}").fetchall()]

        assert nombres("turnos") == ["Operativo"]
        assert nombres("departamentos") == ["Contenedor"]
        assert nombres("cargos") == ["Usuario"]


def test_correrlo_otra_vez_restablece_la_clave_y_destraba(tmp_path: Path) -> None:
    db_path = tmp_path / "zkteco_app.db"
    crear_admin.main(["--db-path", str(db_path), "--clave", "otraClave1"])
    repo = _usuarios(db_path)
    admin = repo.get_by_username("admin")
    assert admin is not None and admin.id is not None
    repo.update_login_state(admin.id, failed_attempts=5, locked_until="2999-01-01T00:00:00+00:00")

    assert crear_admin.main(["--db-path", str(db_path)]) == 0
    admin = repo.get_by_username("admin")
    assert admin is not None
    assert len(repo.list_all()) == 2
    assert BcryptHasher(12).verify("12345678", admin.password_hash)
    assert admin.failed_attempts == 0 and admin.locked_until is None


def test_rechaza_clave_vacia(tmp_path: Path) -> None:
    assert crear_admin.main(["--db-path", str(tmp_path / "x.db"), "--clave", ""]) == 1


def test_superadmin_con_otro_usuario_y_clave(tmp_path: Path) -> None:
    db_path = tmp_path / "zkteco_app.db"
    assert (
        crear_admin.main(["--db-path", str(db_path), "--usuario", "mauricio", "--clave", "x1"]) == 0
    )
    repo = _usuarios(db_path)
    mauricio, administrador = repo.get_by_username("mauricio"), repo.get_by_username(
        "administrador"
    )
    assert mauricio is not None and administrador is not None
    assert BcryptHasher(12).verify("x1", mauricio.password_hash)
    assert BcryptHasher(12).verify("12345678", administrador.password_hash)
