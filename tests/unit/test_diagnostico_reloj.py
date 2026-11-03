"""Probar conexión con el reloj: diagnóstico, puesta en hora y el adapter real (simulado)."""

from __future__ import annotations

import socket
from datetime import datetime
from pathlib import Path
from typing import Any, List, Tuple

import pytest
from zk.exception import ZKErrorResponse

from core.models.cargo import Cargo
from core.models.departamento import Departamento
from core.models.dispositivo import Dispositivo
from core.models.empleado import Empleado
from core.models.info_reloj import InfoReloj, UsuarioReloj
from core.repositories.audit_log_repository_sqlite import AuditLogRepositorySQLite
from core.repositories.cargo_repository_sqlite import CargoRepositorySQLite
from core.repositories.departamento_repository_sqlite import DepartamentoRepositorySQLite
from core.repositories.dispositivo_repository_sqlite import DispositivoRepositorySQLite
from core.repositories.empleado_repository_sqlite import EmpleadoRepositorySQLite
from core.services.audit_logger import AuditLogger
from core.services.diagnostico_reloj_service import DiagnosticoRelojService
from core.services.errors import DispositivoNotFoundError, RelojNoRespondeError
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner
from infrastructure.zkteco import pyzk_adapter
from infrastructure.zkteco.exceptions import ZKConnectionError, ZKTimeoutError
from infrastructure.zkteco.fake_adapter import FakeZKTecoAdapter
from tests.unit.test_catalogo_service import (
    ACTOR_ID,
    MIGRATIONS_DIR,
    _count_audit,
    _seed_actor_user,
)

AHORA = datetime(2026, 10, 31, 9, 0, 0)

Entorno = Tuple[DiagnosticoRelojService, FakeZKTecoAdapter, Database, int]


@pytest.fixture
def entorno(tmp_path: Path) -> Entorno:
    db = Database(tmp_path / "diagnostico.db")
    MigrationsRunner(db, MIGRATIONS_DIR).run()
    _seed_actor_user(db)
    reloj = DispositivoRepositorySQLite(db).create(
        Dispositivo(id=None, nombre="Entrada", ip="192.168.1.201")
    )
    assert reloj.id is not None
    depto = DepartamentoRepositorySQLite(db).create(Departamento(id=None, nombre="Ventas"))
    cargo = CargoRepositorySQLite(db).create(Cargo(id=None, nombre="Vendedor"))
    assert depto.id is not None and cargo.id is not None
    empleados = EmpleadoRepositorySQLite(db)
    for i, (nombre, zk) in enumerate([("Ana", 1), ("Luis", 2), ("Rosa", None), ("Juan", 7)]):
        empleados.create(
            Empleado(
                id=None,
                dni=f"0501-1990-{10000 + i}",
                nombres=nombre,
                apellidos="Paz",
                departamento_id=depto.id,
                cargo_id=cargo.id,
                fecha_ingreso="2024-01-01",
                zkteco_id=zk,
            )
        )
    adapter = FakeZKTecoAdapter()
    service = DiagnosticoRelojService(
        adapter=adapter,
        dispositivo_read=DispositivoRepositorySQLite(db),
        empleado_read=empleados,
        audit_logger=AuditLogger(AuditLogRepositorySQLite(db), machine_name="test"),
        ahora=lambda: AHORA,
    )
    return service, adapter, db, reloj.id


def test_diagnostico_cruza_usuarios_con_empleados(entorno: Entorno) -> None:
    service, adapter, _, reloj_id = entorno
    adapter.info = InfoReloj(
        nombre_equipo="K40",
        hora=datetime(2026, 10, 31, 8, 53, 0),
        usuarios=(
            UsuarioReloj("2", "LUIS"),
            UsuarioReloj("1", "ANA"),
            UsuarioReloj("15", "PEDRO"),
            UsuarioReloj("admin"),
        ),
        marcas=950,
        capacidad_marcas=1000,
    )

    d = service.probar(reloj_id)

    assert d.desfase_segundos == -420
    assert d.hora_desfasada is True
    assert [u.user_id for u in d.usuarios_sin_empleado] == ["15", "admin"]
    assert [e.nombres for e in d.empleados_sin_id] == ["Rosa"]
    assert [e.nombres for e in d.empleados_no_registrados] == ["Juan"]
    assert d.memoria_casi_llena is True


def test_diagnostico_hora_bien_y_sin_datos_de_memoria(entorno: Entorno) -> None:
    service, adapter, _, reloj_id = entorno
    adapter.info = InfoReloj(hora=datetime(2026, 10, 31, 9, 0, 40))

    d = service.probar(reloj_id)

    assert d.desfase_segundos == 40
    assert d.hora_desfasada is False
    assert d.uso_memoria is None
    assert d.memoria_casi_llena is False


def test_diagnostico_reloj_que_no_responde(entorno: Entorno) -> None:
    service, adapter, _, reloj_id = entorno
    adapter.set_next_error(ZKConnectionError("No se pudo conectar al dispositivo Entrada."))
    with pytest.raises(RelojNoRespondeError, match="No se pudo conectar"):
        service.probar(reloj_id)


def test_diagnostico_reloj_inexistente(entorno: Entorno) -> None:
    service, _, _, _ = entorno
    with pytest.raises(DispositivoNotFoundError):
        service.probar(999)


def test_poner_en_hora_usa_la_hora_de_la_pc_y_audita(entorno: Entorno) -> None:
    service, adapter, db, reloj_id = entorno
    puesta = service.poner_en_hora(reloj_id, ACTOR_ID)
    assert puesta == AHORA
    assert adapter.horas_puestas == [(reloj_id, AHORA)]
    assert _count_audit(db, "dispositivo_hora_ajustada") == 1
    assert service.probar(reloj_id).desfase_segundos == 0


def test_poner_en_hora_reloj_que_no_responde(entorno: Entorno) -> None:
    service, adapter, db, reloj_id = entorno
    adapter.set_next_error(ZKTimeoutError("Timeout"))
    with pytest.raises(RelojNoRespondeError):
        service.poner_en_hora(reloj_id, ACTOR_ID)
    assert _count_audit(db, "dispositivo_hora_ajustada") == 0


# ── Adapter real con pyzk simulado ────────────────────────────────────────────


class _Usuario:
    def __init__(self, user_id: str, name: str) -> None:
        self.user_id = user_id
        self.name = name


class _Conexion:
    def __init__(self, falla_usuarios: bool = False) -> None:
        self.records = 0
        self.rec_cap = 0
        self.desconectado = False
        self.hora_puesta: List[datetime] = []
        self._falla_usuarios = falla_usuarios

    def get_device_name(self) -> str:
        raise ZKErrorResponse("no soportado")

    def get_serialnumber(self) -> str:
        return "ABC123 "

    def get_firmware_version(self) -> str:
        return "Ver 6.60"

    def get_time(self) -> datetime:
        return AHORA

    def get_users(self) -> List[_Usuario]:
        if self._falla_usuarios:
            raise socket.timeout()
        return [_Usuario("1", "ANA "), _Usuario("2", "")]

    def read_sizes(self) -> bool:
        self.records, self.rec_cap = 120, 100000
        return True

    def set_time(self, momento: datetime) -> bool:
        self.hora_puesta.append(momento)
        return True

    def disconnect(self) -> None:
        self.desconectado = True


def _adapter_con(monkeypatch: pytest.MonkeyPatch, conn: _Conexion) -> Any:
    class _ZK:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def connect(self) -> _Conexion:
            return conn

    class _Socket:
        def close(self) -> None:
            pass

    monkeypatch.setattr(pyzk_adapter, "ZK", _ZK)
    monkeypatch.setattr("socket.create_connection", lambda *_a, **_k: _Socket())
    return pyzk_adapter.PyzkAdapter(timeout_seconds=3)


_RELOJ = Dispositivo(id=1, nombre="Entrada", ip="192.168.1.201")


def test_pyzk_leer_info(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _Conexion()
    info = _adapter_con(monkeypatch, conn).leer_info(_RELOJ)
    assert info.nombre_equipo == ""
    assert info.serie == "ABC123"
    assert info.firmware == "Ver 6.60"
    assert info.hora == AHORA
    assert info.usuarios == (UsuarioReloj("1", "ANA"), UsuarioReloj("2", ""))
    assert (info.marcas, info.capacidad_marcas) == (120, 100000)
    assert conn.desconectado is True


def test_pyzk_leer_info_timeout_desconecta(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _Conexion(falla_usuarios=True)
    with pytest.raises(ZKTimeoutError, match="leer los usuarios"):
        _adapter_con(monkeypatch, conn).leer_info(_RELOJ)
    assert conn.desconectado is True


def test_pyzk_poner_en_hora(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _Conexion()
    _adapter_con(monkeypatch, conn).poner_en_hora(_RELOJ, AHORA)
    assert conn.hora_puesta == [AHORA]
    assert conn.desconectado is True


def test_usuario_reloj_numero() -> None:
    assert UsuarioReloj("25").numero == 25
    assert UsuarioReloj(" 7 ").numero == 7
    assert UsuarioReloj("admin").numero is None


def test_pyzk_puerto_cerrado_falla_rapido(monkeypatch: pytest.MonkeyPatch) -> None:
    def rechazar(*_a: Any, **_k: Any) -> Any:
        raise ConnectionRefusedError()

    monkeypatch.setattr("socket.create_connection", rechazar)
    with pytest.raises(ZKConnectionError, match="192.168.1.201:4370"):
        pyzk_adapter.PyzkAdapter(timeout_seconds=3).leer_info(_RELOJ)
