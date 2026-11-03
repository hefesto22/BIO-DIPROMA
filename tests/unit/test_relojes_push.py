"""Relojes en modo PUSH (ADMS) y opciones de conexión directa.

Se levanta el servidor push de verdad (puerto libre) contra una base de
demo y un "reloj" simulado le habla por HTTP igual que un ZKTeco.
"""

from __future__ import annotations

import http.client
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import pytest

from bin import seed_demo
from core.models.dispositivo import MODO_PUSH, Dispositivo
from core.repositories.dispositivo_repository_sqlite import DispositivoRepositorySQLite
from core.repositories.registro_raw_repository_sqlite import RegistroRawRepositorySQLite
from core.repositories.sincronizacion_repository_sqlite import SincronizacionRepositorySQLite
from core.services.errors import (
    DuplicateNumeroSerieError,
    InvalidClaveComunicacionError,
    MissingRequiredFieldError,
)
from core.services.recepcion_push_service import MarcaPush, RecepcionPushService
from infrastructure.database.connection import Database
from infrastructure.zkteco import pyzk_adapter
from infrastructure.zkteco.servidor_push import (
    ServidorPush,
    leer_marcas,
    opciones_para_reloj,
)

ACTOR = 1
SERIE = "CDQ9201060223"


@pytest.fixture
def demo(tmp_path: Path) -> Iterator[Tuple[Database, seed_demo._Servicios]]:
    db_path = tmp_path / "push.db"
    assert seed_demo.main(["--db-path", str(db_path), "--semanas", "1"]) == 0
    db = Database(db_path)
    yield db, seed_demo._Servicios(db)


@pytest.fixture
def servidor(
    demo: Tuple[Database, seed_demo._Servicios],
) -> Iterator[Tuple[ServidorPush, RecepcionPushService, Database, seed_demo._Servicios]]:
    db, s = demo
    disp = DispositivoRepositorySQLite(db)
    recepcion = RecepcionPushService(
        dispositivo_read=disp,
        dispositivo_write=disp,
        sincronizacion_write=SincronizacionRepositorySQLite(db),
        registro_raw_write=RegistroRawRepositorySQLite(db),
        consolidador=s.consolidacion,
    )
    srv = ServidorPush(recepcion, puerto=0, host="127.0.0.1")
    assert srv.iniciar()
    yield srv, recepcion, db, s
    srv.detener()


def _pedir(srv: ServidorPush, metodo: str, ruta: str, cuerpo: str = "") -> Tuple[int, str]:
    conn = http.client.HTTPConnection("127.0.0.1", srv.puerto, timeout=10)
    conn.request(metodo, ruta, body=cuerpo.encode() if cuerpo else None)
    r = conn.getresponse()
    texto = r.read().decode()
    conn.close()
    return r.status, texto


def _consulta(db: Database, sql: str, params: Tuple[Any, ...] = ()) -> List[sqlite3.Row]:
    with db.transaction() as conn:
        return list(conn.execute(sql, params).fetchall())


def _dia_habil_viejo() -> date:
    """Un martes de hace ~2 meses: fuera del rango que siembra la demo."""
    d = date.today() - timedelta(days=60)
    return d + timedelta(days=(1 - d.weekday()) % 7)


def _registrar_push(s: seed_demo._Servicios, serie: str = SERIE) -> Dispositivo:
    return s.dispositivos.create_dispositivo(
        "Entrada facial",
        "192.168.1.210",
        4370,
        ACTOR,
        modo=MODO_PUSH,
        numero_serie=serie,
    )


# ── Protocolo (funciones puras) ──────────────────────────────────────────────


def test_leer_marcas_con_tabs_espacios_y_basura() -> None:
    texto = (
        "1\t2026-09-01 07:58:12\t0\t15\t0\t0\n"
        "7  2026-09-01 16:02:00  1  1\n"
        "\n"
        "basura sin fecha\n"
        "3\t2026-13-45 99:00:00\t0\t1\n"
        "admin\t2026-09-01 08:00:00\n"
    )
    marcas = leer_marcas(texto)
    assert marcas == [
        MarcaPush("1", datetime(2026, 9, 1, 7, 58, 12), 0, 15),
        MarcaPush("7", datetime(2026, 9, 1, 16, 2, 0), 1, 1),
        MarcaPush("admin", datetime(2026, 9, 1, 8, 0, 0), 0, 0),
    ]


def test_opciones_para_reloj() -> None:
    texto = opciones_para_reloj(SERIE, None, -6)
    assert texto.startswith(f"GET OPTION FROM: {SERIE}\n")
    assert "ATTLOGStamp=None" in texto and "TimeZone=-6" in texto and "Realtime=1" in texto
    assert "ATTLOGStamp=1234" in opciones_para_reloj(SERIE, "1234", -6)


# ── Reloj sin registrar ──────────────────────────────────────────────────────


def test_reloj_desconocido_no_guarda_y_queda_detectado(servidor: Any) -> None:
    srv, recepcion, db, _s = servidor
    antes = _consulta(db, "SELECT COUNT(*) AS n FROM registros_raw")[0]["n"]

    estado, texto = _pedir(srv, "GET", f"/iclock/cdata?SN={SERIE}&options=all&pushver=2.4.1")
    assert estado == 200 and "ATTLOGStamp=None" in texto
    estado, _ = _pedir(
        srv,
        "POST",
        f"/iclock/cdata?SN={SERIE}&table=ATTLOG&Stamp=99",
        "1\t2026-09-01 07:58:12\t0\t15\n",
    )
    assert estado == 403  # el reloj conserva las marcas y reintenta

    assert _consulta(db, "SELECT COUNT(*) AS n FROM registros_raw")[0]["n"] == antes
    detectados = recepcion.detectados()
    assert [(d.numero_serie, d.ip, d.con_marcas) for d in detectados] == [
        (SERIE, "127.0.0.1", True)
    ]


# ── Reloj registrado ─────────────────────────────────────────────────────────


def test_reloj_push_guarda_marcas_y_calcula_la_asistencia(servidor: Any) -> None:
    srv, recepcion, db, s = servidor
    reloj = _registrar_push(s)
    dia = _dia_habil_viejo().isoformat()
    emp = _consulta(db, "SELECT id FROM empleados WHERE zkteco_id = 1")[0]["id"]

    estado, texto = _pedir(srv, "GET", f"/iclock/cdata?SN={SERIE}&options=all")
    assert estado == 200 and "ATTLOGStamp=None" in texto
    assert recepcion.detectados() == []

    cuerpo = (
        f"1\t{dia} 07:58:12\t0\t15\t0\n1\t{dia} 16:03:40\t1\t1\t0\nadmin\t{dia} 08:00:00\t0\t1\n"
    )
    estado, texto = _pedir(
        srv, "POST", f"/iclock/cdata.aspx?SN={SERIE}&table=ATTLOG&Stamp=500", cuerpo
    )
    assert (estado, texto) == (200, "OK: 3")

    raws = _consulta(
        db,
        "SELECT zkteco_user_id, timestamp, tipo_marcada FROM registros_raw "
        "WHERE dispositivo_id = ? ORDER BY timestamp",
        (reloj.id,),
    )
    assert [tuple(r) for r in raws] == [
        (1, f"{dia}T07:58:12", "CHECK_IN"),
        (1, f"{dia}T16:03:40", "CHECK_OUT"),
    ]
    sync = _consulta(
        db,
        "SELECT iniciada_por_user_id, estado, registros_recibidos FROM sincronizaciones "
        "WHERE dispositivo_id = ?",
        (reloj.id,),
    )
    assert [tuple(r) for r in sync] == [(None, "OK", 2)]
    asis = _consulta(
        db,
        "SELECT hora_entrada_real, hora_salida_real FROM asistencias "
        "WHERE empleado_id = ? AND fecha = ?",
        (emp, dia),
    )
    assert [tuple(r) for r in asis] == [("07:58:12", "16:03:40")]

    # El mismo lote otra vez (el reloj reintenta): no se duplica nada.
    estado, _ = _pedir(srv, "POST", f"/iclock/cdata?SN={SERIE}&table=ATTLOG&Stamp=500", cuerpo)
    assert estado == 200
    assert (
        len(_consulta(db, "SELECT id FROM registros_raw WHERE dispositivo_id = ?", (reloj.id,)))
        == 2
    )
    assert (
        _consulta(
            db,
            "SELECT registros_recibidos AS n FROM sincronizaciones WHERE dispositivo_id = ?",
            (reloj.id,),
        )[0]["n"]
        == 2
    )

    # Al reconectarse se le devuelve su marca de agua; y queda el último contacto.
    _, texto = _pedir(srv, "GET", f"/iclock/cdata?SN={SERIE}&options=all")
    assert "ATTLOGStamp=500" in texto
    estado, texto = _pedir(srv, "GET", f"/iclock/getrequest?SN={SERIE}")
    assert (estado, texto) == (200, "OK")
    guardado = s.dispositivos.get_dispositivo(reloj.id or 0)
    assert guardado.push_stamp == "500" and guardado.ultimo_contacto is not None


def test_reloj_archivado_no_guarda(servidor: Any) -> None:
    srv, _recepcion, db, s = servidor
    reloj = _registrar_push(s)
    s.dispositivos.archive_dispositivo(reloj.id or 0, ACTOR)
    estado, _ = _pedir(
        srv, "POST", f"/iclock/cdata?SN={SERIE}&table=ATTLOG", "1\t2026-09-01 07:58:12\t0\t1\n"
    )
    assert estado == 403
    assert not _consulta(db, "SELECT id FROM registros_raw WHERE dispositivo_id = ?", (reloj.id,))


def test_otras_tablas_y_comandos_se_contestan_ok(servidor: Any) -> None:
    srv, _recepcion, _db, s = servidor
    _registrar_push(s)
    assert _pedir(srv, "POST", f"/iclock/cdata?SN={SERIE}&table=OPERLOG", "OPLOG 4\t0") == (
        200,
        "OK",
    )
    assert _pedir(srv, "POST", f"/iclock/devicecmd?SN={SERIE}", "ID=1&Return=0") == (200, "OK")


def test_puerto_ocupado_deja_el_error(servidor: Any) -> None:
    srv, recepcion, _db, _s = servidor
    otro = ServidorPush(recepcion, puerto=srv.puerto, host="127.0.0.1")
    assert otro.iniciar() is False
    assert otro.error is not None and str(srv.puerto) in otro.error


# ── Alta de relojes con las opciones nuevas ──────────────────────────────────


def test_reloj_push_exige_serie_unica(demo: Tuple[Database, seed_demo._Servicios]) -> None:
    _db, s = demo
    with pytest.raises(MissingRequiredFieldError, match="número de serie"):
        s.dispositivos.create_dispositivo("Facial", "192.168.1.30", 4370, ACTOR, modo=MODO_PUSH)
    _registrar_push(s)
    with pytest.raises(DuplicateNumeroSerieError, match="Entrada facial"):
        s.dispositivos.create_dispositivo(
            "Otro", "192.168.1.31", 4370, ACTOR, modo=MODO_PUSH, numero_serie=f" {SERIE} "
        )


def test_clave_y_udp_se_guardan_y_validan(demo: Tuple[Database, seed_demo._Servicios]) -> None:
    _db, s = demo
    with pytest.raises(InvalidClaveComunicacionError):
        s.dispositivos.create_dispositivo(
            "Viejo", "192.168.1.40", 4370, ACTOR, clave_comunicacion=1000000
        )
    creado = s.dispositivos.create_dispositivo(
        "Viejo", "192.168.1.40", 4370, ACTOR, clave_comunicacion=1234, usar_udp=True
    )
    assert creado.id is not None
    # Editar solo el nombre conserva la clave y el UDP.
    s.dispositivos.update_dispositivo(creado.id, "Viejo K14", "192.168.1.40", 4370, ACTOR)
    leido = s.dispositivos.get_dispositivo(creado.id)
    assert (leido.nombre, leido.clave_comunicacion, leido.usar_udp) == ("Viejo K14", 1234, True)


def test_adapter_usa_clave_y_udp(monkeypatch: pytest.MonkeyPatch) -> None:
    creados: List[Dict[str, Any]] = []

    class _ZK:
        def __init__(self, ip: str, **kw: Any) -> None:
            creados.append({"ip": ip, **kw})

        def connect(self) -> Any:
            raise OSError("sin reloj")

    def no_tocar_red(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("por UDP no se prueba el puerto TCP")

    monkeypatch.setattr(pyzk_adapter, "ZK", _ZK)
    monkeypatch.setattr("socket.create_connection", no_tocar_red)
    reloj = Dispositivo(
        id=1, nombre="K14", ip="192.168.1.40", clave_comunicacion=1234, usar_udp=True
    )
    with pytest.raises(pyzk_adapter.ZKConnectionError):
        pyzk_adapter.PyzkAdapter(timeout_seconds=2).leer_info(reloj)
    assert creados[0]["password"] == 1234 and creados[0]["force_udp"] is True


def test_recepcion_ignora_ids_no_numericos(servidor: Any) -> None:
    _srv, recepcion, db, s = servidor
    reloj = _registrar_push(s)
    dia = datetime.combine(_dia_habil_viejo(), datetime.min.time()).replace(hour=8)
    guardadas: Optional[int] = recepcion.recibir_marcas(
        SERIE, "10.0.0.9", [MarcaPush("admin", dia), MarcaPush("2", dia)]
    )
    assert guardadas == 1
    assert (
        len(_consulta(db, "SELECT id FROM registros_raw WHERE dispositivo_id = ?", (reloj.id,)))
        == 1
    )
