"""Tests del ``SincronizacionService``.

Diseño:
    - Se usan stubs manuales en memoria para los repositorios (no SQLite)
      porque interesa validar el flujo del servicio, no el SQL. El contrato
      del SQL ya vive en los tests de repositorio.
    - ``FakeZKTecoAdapter`` real se usa como double del adapter — replica
      el flujo de validación + error.
    - ``AuditLogger`` real + ``FakeAuditRepo`` para verificar qué acciones
      quedaron auditadas.

Cubre:
    - Happy path: sync OK → cabecera EN_CURSO → pull → bulk insert →
      marcar_ok → audit ``sync.ok``.
    - Error en adapter → marcar_fallida + ``sync.fallida`` + re-propaga.
    - ``recover_huerfanas`` cierra huérfanas y deja las ya cerradas.
    - Dispositivo inexistente / archivado → excepción de dominio antes
      de tocar la BD.
    - Rango inválido (invertido o malformado) → excepción de dominio.
    - Consolidador inyectado: corre inline tras ``marcar_ok``; si lanza,
      la sync queda OK y ``error_consolidacion`` se llena.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Dict, List, Optional, Sequence

import pytest

from core.models.dispositivo import Dispositivo
from core.models.registro_raw import RegistroRaw, TipoMarcada
from core.models.sincronizacion import EstadoSincronizacion, Sincronizacion
from core.repositories.dispositivo_repository import IDispositivoReadRepository
from core.repositories.registro_raw_repository import IRegistroRawWriteRepository
from core.repositories.sincronizacion_repository import (
    ISincronizacionReadRepository,
    ISincronizacionWriteRepository,
)
from core.services.audit_logger import AuditLogger
from core.services.errors import (
    DispositivoInactiveError,
    DispositivoNotFoundError,
    InvalidDateError,
    InvalidRangoError,
)
from core.services.sincronizacion_result import ResultadoConsolidacion
from core.services.sincronizacion_service import (
    IConsolidadorDeRango,
    SincronizacionService,
)
from infrastructure.zkteco.exceptions import (
    ZKConnectionError,
    ZKTimeoutError,
)
from infrastructure.zkteco.fake_adapter import FakeZKTecoAdapter
from tests.unit.test_audit_logger import FakeAuditRepo

# ── Stubs de repositorios en memoria ─────────────────────────────────────────


class FakeDispositivoReadRepo(IDispositivoReadRepository):
    """Devuelve dispositivos de un dict interno."""

    def __init__(self, dispositivos: Optional[Dict[int, Dispositivo]] = None) -> None:
        self._dispositivos: Dict[int, Dispositivo] = dispositivos or {}

    def agregar(self, dispositivo: Dispositivo) -> None:
        assert dispositivo.id is not None
        self._dispositivos[dispositivo.id] = dispositivo

    def get_by_id(self, dispositivo_id: int) -> Optional[Dispositivo]:
        return self._dispositivos.get(dispositivo_id)

    def get_by_nombre(self, nombre: str) -> Optional[Dispositivo]:  # pragma: no cover
        raise NotImplementedError

    def get_by_ip_puerto(self, ip: str, puerto: int) -> Optional[Dispositivo]:  # pragma: no cover
        raise NotImplementedError

    def get_by_numero_serie(self, numero_serie: str) -> Optional[Dispositivo]:  # pragma: no cover
        raise NotImplementedError

    def list_all(self) -> List[Dispositivo]:  # pragma: no cover
        return list(self._dispositivos.values())

    def list_active(self) -> List[Dispositivo]:  # pragma: no cover
        return [d for d in self._dispositivos.values() if d.is_active]


class FakeSincronizacionRepo(ISincronizacionReadRepository, ISincronizacionWriteRepository):
    """Combina read + write en un solo fake para facilitar la inyección."""

    def __init__(self) -> None:
        self._syncs: Dict[int, Sincronizacion] = {}
        self._seq = 0
        self.ok_calls: List[Dict[str, object]] = []
        self.fallida_calls: List[Dict[str, object]] = []

    # ── write ─────────────────────────────────────────────────────────

    def create(self, sincronizacion: Sincronizacion) -> Sincronizacion:
        self._seq += 1
        nueva = Sincronizacion(
            id=self._seq,
            dispositivo_id=sincronizacion.dispositivo_id,
            iniciada_por_user_id=sincronizacion.iniciada_por_user_id,
            inicio=sincronizacion.inicio,
            rango_desde=sincronizacion.rango_desde,
            rango_hasta=sincronizacion.rango_hasta,
            fin=sincronizacion.fin,
            registros_recibidos=sincronizacion.registros_recibidos,
            estado=sincronizacion.estado,
            error_mensaje=sincronizacion.error_mensaje,
        )
        self._syncs[self._seq] = nueva
        return nueva

    def marcar_ok(self, sincronizacion_id: int, fin: str, registros_recibidos: int) -> None:
        sync = self._syncs[sincronizacion_id]
        sync.fin = fin
        sync.registros_recibidos = registros_recibidos
        sync.estado = EstadoSincronizacion.OK.value
        sync.error_mensaje = None
        self.ok_calls.append(
            {"id": sincronizacion_id, "fin": fin, "recibidos": registros_recibidos}
        )

    def marcar_fallida(self, sincronizacion_id: int, fin: str, error_mensaje: str) -> None:
        sync = self._syncs[sincronizacion_id]
        sync.fin = fin
        sync.estado = EstadoSincronizacion.FALLIDA.value
        sync.error_mensaje = error_mensaje
        self.fallida_calls.append({"id": sincronizacion_id, "fin": fin, "error": error_mensaje})

    # ── read ──────────────────────────────────────────────────────────

    def get_by_id(self, sincronizacion_id: int) -> Optional[Sincronizacion]:
        return self._syncs.get(sincronizacion_id)

    def list_recientes(self, limit: int = 50) -> List[Sincronizacion]:  # pragma: no cover
        return list(self._syncs.values())[:limit]

    def list_by_dispositivo(
        self, dispositivo_id: int, limit: int = 50
    ) -> List[Sincronizacion]:  # pragma: no cover
        return [s for s in self._syncs.values() if s.dispositivo_id == dispositivo_id][:limit]

    def list_en_curso(self) -> List[Sincronizacion]:
        return [s for s in self._syncs.values() if s.estado == EstadoSincronizacion.EN_CURSO.value]


class FakeRegistroRawWriteRepo(IRegistroRawWriteRepository):
    """Acumula los bulks recibidos; cuenta insertados sin dedupe."""

    def __init__(self) -> None:
        self.bulks: List[List[RegistroRaw]] = []

    def create_bulk(self, registros: Sequence[RegistroRaw]) -> int:
        lista = list(registros)
        self.bulks.append(lista)
        return len(lista)


class ConsolidadorStub:
    """Stub que cumple ``IConsolidadorDeRango``.

    Si ``next_error`` está seteado, la próxima llamada lo lanza. Si no,
    devuelve ``result``. También registra los (desde, hasta) recibidos.
    """

    def __init__(
        self,
        result: Optional[ResultadoConsolidacion] = None,
        next_error: Optional[Exception] = None,
    ) -> None:
        self.result = result or ResultadoConsolidacion(empleados_procesados=0, dias_procesados=0)
        self.next_error = next_error
        self.calls: List[Dict[str, str]] = []

    def consolidar_rango(self, desde: str, hasta: str) -> ResultadoConsolidacion:
        self.calls.append({"desde": desde, "hasta": hasta})
        if self.next_error is not None:
            exc = self.next_error
            self.next_error = None
            raise exc
        return self.result


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def dispositivo_activo() -> Dispositivo:
    return Dispositivo(id=1, nombre="Principal", ip="10.0.0.1", puerto=4370, is_active=True)


@pytest.fixture
def dispositivo_archivado() -> Dispositivo:
    return Dispositivo(id=2, nombre="Viejo", ip="10.0.0.2", puerto=4370, is_active=False)


@pytest.fixture
def service_y_fakes(dispositivo_activo: Dispositivo) -> Dict[str, object]:
    """Arma un servicio con todas sus dependencias como fakes.

    Retorna un dict con acceso a cada fake — así los tests pueden hacer
    aserciones contra ellos.
    """
    dispositivos = FakeDispositivoReadRepo()
    dispositivos.agregar(dispositivo_activo)
    sync_repo = FakeSincronizacionRepo()
    raw_repo = FakeRegistroRawWriteRepo()
    adapter = FakeZKTecoAdapter()
    audit_repo = FakeAuditRepo()
    audit = AuditLogger(audit_repo, machine_name="PC-TEST")
    service = SincronizacionService(
        dispositivo_read=dispositivos,
        sincronizacion_read=sync_repo,
        sincronizacion_write=sync_repo,
        registro_raw_write=raw_repo,
        adapter=adapter,
        audit_logger=audit,
    )
    return {
        "service": service,
        "dispositivos": dispositivos,
        "sync_repo": sync_repo,
        "raw_repo": raw_repo,
        "adapter": adapter,
        "audit_repo": audit_repo,
    }


# ── Tests: happy path ────────────────────────────────────────────────────────


def test_ejecutar_sync_ok_persiste_y_audita(service_y_fakes: Dict[str, object]) -> None:
    """Pull con 2 marcadas → crea cabecera, bulk-insert, marcar_ok, audit."""
    service = service_y_fakes["service"]
    assert isinstance(service, SincronizacionService)
    adapter = service_y_fakes["adapter"]
    assert isinstance(adapter, FakeZKTecoAdapter)
    sync_repo = service_y_fakes["sync_repo"]
    assert isinstance(sync_repo, FakeSincronizacionRepo)
    raw_repo = service_y_fakes["raw_repo"]
    assert isinstance(raw_repo, FakeRegistroRawWriteRepo)
    audit_repo = service_y_fakes["audit_repo"]
    assert isinstance(audit_repo, FakeAuditRepo)

    adapter.preload_attendance(
        dispositivo_id=1,
        zkteco_user_id=42,
        timestamp=datetime(2026, 4, 15, 8, 0),
        status=0,  # CHECK_IN
    )
    adapter.preload_attendance(
        dispositivo_id=1,
        zkteco_user_id=42,
        timestamp=datetime(2026, 4, 15, 17, 0),
        status=1,  # CHECK_OUT
    )

    resultado = service.ejecutar(
        dispositivo_id=1,
        rango_desde="2026-04-15",
        rango_hasta="2026-04-15",
        iniciada_por_user_id=7,
    )

    assert resultado.sincronizacion_id == 1
    assert resultado.dispositivo_id == 1
    assert resultado.registros_recibidos == 2
    assert resultado.consolidacion is None  # sin consolidador inyectado
    assert resultado.error_consolidacion is None

    # Sync quedó OK con los mismos 2 registros contados.
    sync = sync_repo.get_by_id(1)
    assert sync is not None
    assert sync.estado == EstadoSincronizacion.OK.value
    assert sync.registros_recibidos == 2
    assert sync.fin is not None
    assert sync.error_mensaje is None

    # Bulk-insert disparado con 2 registros.
    assert len(raw_repo.bulks) == 1
    assert len(raw_repo.bulks[0]) == 2
    assert all(
        r.tipo_marcada in {TipoMarcada.CHECK_IN.value, TipoMarcada.CHECK_OUT.value}
        for r in raw_repo.bulks[0]
    )

    # Audit: una sola entrada ``sync.ok``.
    acciones = [e.action for e in audit_repo.entradas]
    assert acciones == ["sync.ok"]
    detalles = json.loads(audit_repo.entradas[0].details or "{}")
    assert detalles["sincronizacion_id"] == 1
    assert detalles["dispositivo_id"] == 1
    assert detalles["registros_recibidos"] == 2


def test_ejecutar_pasa_fechas_como_date_al_adapter(service_y_fakes: Dict[str, object]) -> None:
    """Contrato del adapter: ``desde`` y ``hasta`` son ``date``, no strings."""
    service = service_y_fakes["service"]
    adapter = service_y_fakes["adapter"]
    assert isinstance(service, SincronizacionService)
    assert isinstance(adapter, FakeZKTecoAdapter)

    service.ejecutar(
        dispositivo_id=1,
        rango_desde="2026-04-10",
        rango_hasta="2026-04-15",
        iniciada_por_user_id=None,
    )
    assert len(adapter.pulls) == 1
    pull = adapter.pulls[0]
    assert pull.desde == date(2026, 4, 10)
    assert pull.hasta == date(2026, 4, 15)
    assert pull.dispositivo_id == 1
    assert pull.sincronizacion_id == 1


def test_ejecutar_sin_marcadas_marca_ok_con_cero(service_y_fakes: Dict[str, object]) -> None:
    """Un pull vacío sigue siendo una sync OK con ``registros_recibidos=0``."""
    service = service_y_fakes["service"]
    sync_repo = service_y_fakes["sync_repo"]
    assert isinstance(service, SincronizacionService)
    assert isinstance(sync_repo, FakeSincronizacionRepo)

    resultado = service.ejecutar(
        dispositivo_id=1,
        rango_desde="2026-04-15",
        rango_hasta="2026-04-15",
        iniciada_por_user_id=None,
    )
    assert resultado.registros_recibidos == 0
    sync = sync_repo.get_by_id(1)
    assert sync is not None
    assert sync.estado == EstadoSincronizacion.OK.value


# ── Tests: errores del adapter ───────────────────────────────────────────────


def test_ejecutar_con_error_de_conexion_marca_fallida_y_re_propaga(
    service_y_fakes: Dict[str, object],
) -> None:
    service = service_y_fakes["service"]
    adapter = service_y_fakes["adapter"]
    sync_repo = service_y_fakes["sync_repo"]
    audit_repo = service_y_fakes["audit_repo"]
    raw_repo = service_y_fakes["raw_repo"]
    assert isinstance(service, SincronizacionService)
    assert isinstance(adapter, FakeZKTecoAdapter)
    assert isinstance(sync_repo, FakeSincronizacionRepo)
    assert isinstance(audit_repo, FakeAuditRepo)
    assert isinstance(raw_repo, FakeRegistroRawWriteRepo)

    adapter.set_next_error(ZKConnectionError("Red caída"))

    with pytest.raises(ZKConnectionError, match="Red caída"):
        service.ejecutar(
            dispositivo_id=1,
            rango_desde="2026-04-15",
            rango_hasta="2026-04-15",
            iniciada_por_user_id=99,
        )

    # La sync se creó y quedó FALLIDA con el mensaje de error.
    sync = sync_repo.get_by_id(1)
    assert sync is not None
    assert sync.estado == EstadoSincronizacion.FALLIDA.value
    assert sync.error_mensaje == "Red caída"
    assert sync.fin is not None

    # No hubo bulk-insert: el adapter falló antes de devolver registros.
    assert raw_repo.bulks == []

    # Audit: sólo ``sync.fallida``, sin ``sync.ok``.
    acciones = [e.action for e in audit_repo.entradas]
    assert acciones == ["sync.fallida"]
    detalles = json.loads(audit_repo.entradas[0].details or "{}")
    assert detalles["error"] == "Red caída"
    assert detalles["dispositivo_id"] == 1


def test_ejecutar_timeout_tambien_marca_fallida(
    service_y_fakes: Dict[str, object],
) -> None:
    """Cualquier subclase de ``ZKAdapterError`` dispara el mismo flujo."""
    service = service_y_fakes["service"]
    adapter = service_y_fakes["adapter"]
    sync_repo = service_y_fakes["sync_repo"]
    assert isinstance(service, SincronizacionService)
    assert isinstance(adapter, FakeZKTecoAdapter)
    assert isinstance(sync_repo, FakeSincronizacionRepo)

    adapter.set_next_error(ZKTimeoutError("Device sin respuesta en 30s"))

    with pytest.raises(ZKTimeoutError):
        service.ejecutar(
            dispositivo_id=1,
            rango_desde="2026-04-15",
            rango_hasta="2026-04-15",
            iniciada_por_user_id=None,
        )
    sync = sync_repo.get_by_id(1)
    assert sync is not None
    assert sync.estado == EstadoSincronizacion.FALLIDA.value


# ── Tests: validaciones de entrada ───────────────────────────────────────────


def test_ejecutar_con_dispositivo_inexistente_levanta_antes_de_crear_sync(
    service_y_fakes: Dict[str, object],
) -> None:
    service = service_y_fakes["service"]
    sync_repo = service_y_fakes["sync_repo"]
    audit_repo = service_y_fakes["audit_repo"]
    assert isinstance(service, SincronizacionService)
    assert isinstance(sync_repo, FakeSincronizacionRepo)
    assert isinstance(audit_repo, FakeAuditRepo)

    with pytest.raises(DispositivoNotFoundError):
        service.ejecutar(
            dispositivo_id=999,
            rango_desde="2026-04-15",
            rango_hasta="2026-04-15",
            iniciada_por_user_id=None,
        )
    # No se creó cabecera ni se auditó nada.
    assert sync_repo.get_by_id(1) is None
    assert audit_repo.entradas == []


def test_ejecutar_con_dispositivo_archivado_levanta(
    dispositivo_archivado: Dispositivo,
) -> None:
    dispositivos = FakeDispositivoReadRepo()
    dispositivos.agregar(dispositivo_archivado)
    sync_repo = FakeSincronizacionRepo()
    service = SincronizacionService(
        dispositivo_read=dispositivos,
        sincronizacion_read=sync_repo,
        sincronizacion_write=sync_repo,
        registro_raw_write=FakeRegistroRawWriteRepo(),
        adapter=FakeZKTecoAdapter(),
        audit_logger=AuditLogger(FakeAuditRepo(), machine_name="PC-TEST"),
    )
    assert dispositivo_archivado.id is not None
    with pytest.raises(DispositivoInactiveError):
        service.ejecutar(
            dispositivo_id=dispositivo_archivado.id,
            rango_desde="2026-04-15",
            rango_hasta="2026-04-15",
            iniciada_por_user_id=None,
        )


def test_ejecutar_con_rango_invertido_levanta(service_y_fakes: Dict[str, object]) -> None:
    service = service_y_fakes["service"]
    assert isinstance(service, SincronizacionService)
    with pytest.raises(InvalidRangoError):
        service.ejecutar(
            dispositivo_id=1,
            rango_desde="2026-04-20",
            rango_hasta="2026-04-15",
            iniciada_por_user_id=None,
        )


def test_ejecutar_con_fecha_malformada_levanta_invalid_date(
    service_y_fakes: Dict[str, object],
) -> None:
    service = service_y_fakes["service"]
    assert isinstance(service, SincronizacionService)
    with pytest.raises(InvalidDateError):
        service.ejecutar(
            dispositivo_id=1,
            rango_desde="no-fecha",
            rango_hasta="2026-04-15",
            iniciada_por_user_id=None,
        )


# ── Tests: recover_huerfanas ─────────────────────────────────────────────────


def test_recover_huerfanas_sin_pendientes_retorna_cero(
    service_y_fakes: Dict[str, object],
) -> None:
    service = service_y_fakes["service"]
    assert isinstance(service, SincronizacionService)
    assert service.recover_huerfanas() == 0


def test_recover_huerfanas_cierra_en_curso(
    service_y_fakes: Dict[str, object],
) -> None:
    service = service_y_fakes["service"]
    sync_repo = service_y_fakes["sync_repo"]
    assert isinstance(service, SincronizacionService)
    assert isinstance(sync_repo, FakeSincronizacionRepo)

    # Simular dos syncs EN_CURSO "colgadas" de un arranque previo.
    sync_repo.create(
        Sincronizacion(
            id=None,
            dispositivo_id=1,
            iniciada_por_user_id=None,
            inicio="2026-04-23T10:00:00",
            rango_desde="2026-04-01",
            rango_hasta="2026-04-30",
        )
    )
    sync_repo.create(
        Sincronizacion(
            id=None,
            dispositivo_id=1,
            iniciada_por_user_id=None,
            inicio="2026-04-23T11:00:00",
            rango_desde="2026-04-01",
            rango_hasta="2026-04-30",
        )
    )
    # Y una ya cerrada como OK — no debe tocarla.
    sync_repo.marcar_ok(sincronizacion_id=2, fin="2026-04-23T11:30:00", registros_recibidos=5)

    cerradas = service.recover_huerfanas()
    assert cerradas == 1  # sólo 1 quedaba EN_CURSO

    sync_1 = sync_repo.get_by_id(1)
    sync_2 = sync_repo.get_by_id(2)
    assert sync_1 is not None and sync_2 is not None
    assert sync_1.estado == EstadoSincronizacion.FALLIDA.value
    assert sync_1.error_mensaje and "Interrumpida" in sync_1.error_mensaje
    assert sync_2.estado == EstadoSincronizacion.OK.value  # inalterada


# ── Tests: consolidación inline ──────────────────────────────────────────────


def test_ejecutar_con_consolidador_corre_inline_y_adjunta_resultado(
    dispositivo_activo: Dispositivo,
) -> None:
    dispositivos = FakeDispositivoReadRepo()
    dispositivos.agregar(dispositivo_activo)
    sync_repo = FakeSincronizacionRepo()
    resultado_consol = ResultadoConsolidacion(
        asistencias_upsertadas=3, empleados_procesados=3, dias_procesados=1
    )
    consolidador = ConsolidadorStub(result=resultado_consol)
    service = SincronizacionService(
        dispositivo_read=dispositivos,
        sincronizacion_read=sync_repo,
        sincronizacion_write=sync_repo,
        registro_raw_write=FakeRegistroRawWriteRepo(),
        adapter=FakeZKTecoAdapter(),
        audit_logger=AuditLogger(FakeAuditRepo(), machine_name="PC-TEST"),
        consolidador=consolidador,
    )

    res = service.ejecutar(
        dispositivo_id=1,
        rango_desde="2026-04-15",
        rango_hasta="2026-04-15",
        iniciada_por_user_id=None,
    )

    assert res.consolidacion is resultado_consol
    assert res.error_consolidacion is None
    # Consolidador recibió el rango como strings ISO.
    assert consolidador.calls == [{"desde": "2026-04-15", "hasta": "2026-04-15"}]


def test_ejecutar_con_consolidador_que_falla_no_corrompe_sync_ok(
    dispositivo_activo: Dispositivo,
) -> None:
    """Si el consolidador lanza, la sync SIGUE siendo OK y el error se reporta."""
    dispositivos = FakeDispositivoReadRepo()
    dispositivos.agregar(dispositivo_activo)
    sync_repo = FakeSincronizacionRepo()
    consolidador = ConsolidadorStub(
        next_error=ValueError("Historial de turnos corrupto para empleado 42")
    )
    service = SincronizacionService(
        dispositivo_read=dispositivos,
        sincronizacion_read=sync_repo,
        sincronizacion_write=sync_repo,
        registro_raw_write=FakeRegistroRawWriteRepo(),
        adapter=FakeZKTecoAdapter(),
        audit_logger=AuditLogger(FakeAuditRepo(), machine_name="PC-TEST"),
        consolidador=consolidador,
    )

    res = service.ejecutar(
        dispositivo_id=1,
        rango_desde="2026-04-15",
        rango_hasta="2026-04-15",
        iniciada_por_user_id=None,
    )

    # Sync OK intacta.
    sync = sync_repo.get_by_id(1)
    assert sync is not None
    assert sync.estado == EstadoSincronizacion.OK.value
    # Resultado lleva el error sin tirar toda la llamada.
    assert res.consolidacion is None
    assert res.error_consolidacion is not None
    assert "Historial" in res.error_consolidacion


def test_consolidador_no_se_invoca_si_el_pull_falla(
    dispositivo_activo: Dispositivo,
) -> None:
    """Consolidar raws que nunca se insertaron sería un pase en vacío."""
    dispositivos = FakeDispositivoReadRepo()
    dispositivos.agregar(dispositivo_activo)
    sync_repo = FakeSincronizacionRepo()
    consolidador = ConsolidadorStub()
    adapter = FakeZKTecoAdapter()
    adapter.set_next_error(ZKConnectionError("Red caída"))
    service = SincronizacionService(
        dispositivo_read=dispositivos,
        sincronizacion_read=sync_repo,
        sincronizacion_write=sync_repo,
        registro_raw_write=FakeRegistroRawWriteRepo(),
        adapter=adapter,
        audit_logger=AuditLogger(FakeAuditRepo(), machine_name="PC-TEST"),
        consolidador=consolidador,
    )

    with pytest.raises(ZKConnectionError):
        service.ejecutar(
            dispositivo_id=1,
            rango_desde="2026-04-15",
            rango_hasta="2026-04-15",
            iniciada_por_user_id=None,
        )
    assert consolidador.calls == []


# ── Tests: IConsolidadorDeRango es Protocol (structural) ─────────────────────


def test_consolidador_stub_satisface_el_protocolo() -> None:
    """Sanity check: el stub cumple el contrato sin heredar explícitamente."""
    stub: IConsolidadorDeRango = ConsolidadorStub()
    resultado = stub.consolidar_rango("2026-04-01", "2026-04-30")
    assert isinstance(resultado, ResultadoConsolidacion)
