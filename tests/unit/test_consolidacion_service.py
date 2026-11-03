"""Tests del ``ConsolidacionService`` con repositorios reales + SQLite.

Se usa un SQLite en memoria (archivo temporal) con las migraciones
completas aplicadas — a diferencia de los tests de
``SincronizacionService`` (que usan stubs manuales), aquí el servicio
se integra extremo a extremo con los repositorios concretos para
verificar que el contrato de lectura agrupada + UPSERT preserva las
invariantes críticas de la consolidación:

    - Pre-carga + agrupamiento: el servicio NO hace N+1 queries.
    - Precedencia FERIADO > SIN_TURNO (regla de `estado_no_trabaja`).
    - Bitmask de días de la semana produce SIN_TURNO en días no
      laborables.
    - Empleado sin historial de turno produce SIN_TURNO (no AUSENTE).
    - Turno nocturno cruza medianoche: raw en D+1 cuenta como salida
      del turno que entró en D.
    - Marcadas desde múltiples dispositivos se consolidan juntas.
    - ``zkteco_user_id`` sin empleado mapeado genera
      ``MarcadaDesconocida`` (no contamina asistencias).
    - Re-consolidar el mismo rango PRESERVA ``observaciones`` anotadas
      manualmente (idempotencia + UPSERT contract).
    - Empleados archivados NO entran a la consolidación.
    - Un empleado con historial roto aporta a ``errores_empleado`` sin
      abortar el batch.
    - Se emite una entrada de auditoría ``consolidacion.rango``.
    - Rango invertido / fecha malformada → ValidationError.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Tuple

import pytest

from core.models.asistencia import EstadoAsistencia
from core.models.cargo import Cargo
from core.models.departamento import Departamento
from core.models.dispositivo import Dispositivo
from core.models.empleado import Empleado
from core.models.feriado import Feriado
from core.models.registro_raw import RegistroRaw, TipoMarcada
from core.models.sincronizacion import Sincronizacion
from core.models.turno import (
    DIAS_LABORALES,
    DIAS_TODA_LA_SEMANA,
    Turno,
)
from core.repositories.asistencia_repository_sqlite import (
    AsistenciaRepositorySQLite,
)
from core.repositories.cargo_repository_sqlite import CargoRepositorySQLite
from core.repositories.departamento_repository_sqlite import (
    DepartamentoRepositorySQLite,
)
from core.repositories.dispositivo_repository_sqlite import (
    DispositivoRepositorySQLite,
)
from core.repositories.empleado_repository_sqlite import (
    EmpleadoRepositorySQLite,
)
from core.repositories.empleado_turno_repository_sqlite import (
    EmpleadoTurnoRepositorySQLite,
)
from core.repositories.feriado_repository_sqlite import (
    FeriadoRepositorySQLite,
)
from core.repositories.registro_raw_repository_sqlite import (
    RegistroRawRepositorySQLite,
)
from core.repositories.sincronizacion_repository_sqlite import (
    SincronizacionRepositorySQLite,
)
from core.repositories.turno_repository_sqlite import TurnoRepositorySQLite
from core.services.consolidacion_service import ConsolidacionService
from core.services.errors import EmpleadoNotFoundError, InvalidDateError, InvalidRangoError
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner
from tests.unit.test_audit_logger import FakeAuditRepo

# ── Rutas ─────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "infrastructure" / "database" / "migrations"


# ── Contexto compartido del setup ─────────────────────────────────────────────


class _Ctx:
    """Agrupa todos los handles creados por la fixture ``setup``.

    Evita tuplas anónimas largas y permite pasar un solo objeto a los
    tests. No es un fixture en sí mismo — lo construye la fixture
    ``setup`` abajo.
    """

    def __init__(
        self,
        db: Database,
        service: ConsolidacionService,
        audit_repo: FakeAuditRepo,
        asistencia_repo: AsistenciaRepositorySQLite,
        empleado_repo: EmpleadoRepositorySQLite,
        empleado_turno_repo: EmpleadoTurnoRepositorySQLite,
        turno_repo: TurnoRepositorySQLite,
        feriado_repo: FeriadoRepositorySQLite,
        registro_raw_repo: RegistroRawRepositorySQLite,
        sincronizacion_repo: SincronizacionRepositorySQLite,
        dispositivo_ids: Tuple[int, int],
        sincronizacion_id: int,
        turno_diurno_id: int,
        turno_nocturno_id: int,
    ) -> None:
        self.db = db
        self.service = service
        self.audit_repo = audit_repo
        self.asistencia_repo = asistencia_repo
        self.empleado_repo = empleado_repo
        self.empleado_turno_repo = empleado_turno_repo
        self.turno_repo = turno_repo
        self.feriado_repo = feriado_repo
        self.registro_raw_repo = registro_raw_repo
        self.sincronizacion_repo = sincronizacion_repo
        self.dispositivo_ids = dispositivo_ids
        self.sincronizacion_id = sincronizacion_id
        self.turno_diurno_id = turno_diurno_id
        self.turno_nocturno_id = turno_nocturno_id


# ── Fixture base ──────────────────────────────────────────────────────────────


@pytest.fixture
def setup(tmp_path: Path) -> _Ctx:
    """BD con migraciones + 2 dispositivos + 1 sync + 2 turnos + catálogos.

    No crea empleados — cada test los seedea según su caso. Esto mantiene
    cada test auto-contenido y evita acoplar sus asserts.
    """
    db = Database(tmp_path / "test_consolidacion.db")
    MigrationsRunner(db, MIGRATIONS_DIR).run()

    # Catálogos básicos.
    depto_repo = DepartamentoRepositorySQLite(db)
    cargo_repo = CargoRepositorySQLite(db)
    depto = depto_repo.create(Departamento(id=None, nombre="Administración"))
    cargo = cargo_repo.create(Cargo(id=None, nombre="Secretaria"))
    assert depto.id is not None and cargo.id is not None

    # Dos dispositivos: queremos probar que un empleado puede marcar en
    # cualquiera y ambas marcadas se consolidan.
    dispositivo_repo = DispositivoRepositorySQLite(db)
    disp_a = dispositivo_repo.create(
        Dispositivo(id=None, nombre="Reloj A", ip="10.0.0.1", puerto=4370)
    )
    disp_b = dispositivo_repo.create(
        Dispositivo(id=None, nombre="Reloj B", ip="10.0.0.2", puerto=4370)
    )
    assert disp_a.id is not None and disp_b.id is not None

    # Una sincronización: los raws deben tener sincronizacion_id válido
    # (FK). El test controla `dispositivo_id` y `zkteco_user_id` por raw.
    sincronizacion_repo = SincronizacionRepositorySQLite(db)
    sync = sincronizacion_repo.create(
        Sincronizacion(
            id=None,
            dispositivo_id=disp_a.id,
            iniciada_por_user_id=None,
            inicio="2026-04-24T10:00:00",
            rango_desde="2026-04-01",
            rango_hasta="2026-04-30",
        )
    )
    assert sync.id is not None

    # Turnos: uno diurno L–V 08–17 (tolerancia 10/0) y uno nocturno TODO
    # los días 22:00–06:00 (cruza medianoche).
    turno_repo = TurnoRepositorySQLite(db)
    turno_diurno = turno_repo.create(
        Turno(
            id=None,
            nombre="Administrativo 8-5",
            hora_entrada="08:00",
            hora_salida="17:00",
            dias_semana=DIAS_LABORALES,
            cruza_medianoche=False,
            minutos_tolerancia_entrada=10,
            minutos_tolerancia_salida=0,
        )
    )
    turno_nocturno = turno_repo.create(
        Turno(
            id=None,
            nombre="Vigilancia Nocturna",
            hora_entrada="22:00",
            hora_salida="06:00",
            dias_semana=DIAS_TODA_LA_SEMANA,
            cruza_medianoche=True,
            minutos_tolerancia_entrada=10,
            minutos_tolerancia_salida=0,
        )
    )
    assert turno_diurno.id is not None and turno_nocturno.id is not None

    # Repos restantes.
    empleado_repo = EmpleadoRepositorySQLite(db)
    empleado_turno_repo = EmpleadoTurnoRepositorySQLite(db)
    feriado_repo = FeriadoRepositorySQLite(db)
    registro_raw_repo = RegistroRawRepositorySQLite(db)
    asistencia_repo = AsistenciaRepositorySQLite(db)

    # Audit logger con repo en memoria (import reutilizado del test de
    # AuditLogger — mantiene consistencia con el patrón de Fase 1).
    audit_repo = FakeAuditRepo()
    from core.services.audit_logger import AuditLogger

    audit_logger = AuditLogger(audit_repo, machine_name="PC-TEST")

    service = ConsolidacionService(
        empleado_read=empleado_repo,
        empleado_turno_read=empleado_turno_repo,
        turno_read=turno_repo,
        feriado_read=feriado_repo,
        registro_raw_read=registro_raw_repo,
        asistencia_write=asistencia_repo,
        audit_logger=audit_logger,
    )

    return _Ctx(
        db=db,
        service=service,
        audit_repo=audit_repo,
        asistencia_repo=asistencia_repo,
        empleado_repo=empleado_repo,
        empleado_turno_repo=empleado_turno_repo,
        turno_repo=turno_repo,
        feriado_repo=feriado_repo,
        registro_raw_repo=registro_raw_repo,
        sincronizacion_repo=sincronizacion_repo,
        dispositivo_ids=(disp_a.id, disp_b.id),
        sincronizacion_id=sync.id,
        turno_diurno_id=turno_diurno.id,
        turno_nocturno_id=turno_nocturno.id,
    )


# ── Helpers de seed ───────────────────────────────────────────────────────────


def _dni_para(i: int) -> str:
    """Genera un DNI único hondureño válido por índice.

    Formato exigido por el validador: ``XXXX-XXXX-XXXXX``.
    """
    base = f"{i:05d}"
    return f"0501-1990-{base}"


def _crear_empleado(
    ctx: _Ctx,
    nombres: str,
    apellidos: str,
    zkteco_id: int | None,
    indice: int,
) -> int:
    """Crea un empleado activo con DNI único y devuelve su id."""
    depto = DepartamentoRepositorySQLite(ctx.db).get_by_nombre("Administración")
    cargo = CargoRepositorySQLite(ctx.db).get_by_nombre("Secretaria")
    assert depto is not None and depto.id is not None
    assert cargo is not None and cargo.id is not None
    emp = ctx.empleado_repo.create(
        Empleado(
            id=None,
            dni=_dni_para(indice),
            nombres=nombres,
            apellidos=apellidos,
            departamento_id=depto.id,
            cargo_id=cargo.id,
            fecha_ingreso="2020-01-15",
            zkteco_id=zkteco_id,
        )
    )
    assert emp.id is not None
    return emp.id


def _asignar_turno(
    ctx: _Ctx, empleado_id: int, turno_id: int, fecha_inicio: str = "2020-01-15"
) -> None:
    """Asigna el turno como vigente (``fecha_fin = None``)."""
    ctx.empleado_turno_repo.asignar(empleado_id, turno_id, fecha_inicio)


def _insertar_raws(ctx: _Ctx, registros: List[RegistroRaw]) -> None:
    """Atajo para ``create_bulk`` con la fixture de sincronización/dispositivo."""
    assert ctx.registro_raw_repo.create_bulk(registros) == len(registros)


def _raw(
    ctx: _Ctx,
    zkteco_user_id: int,
    timestamp: str,
    tipo: str = TipoMarcada.CHECK_IN.value,
    dispositivo_idx: int = 0,
) -> RegistroRaw:
    """Factory de RegistroRaw con ``id=None`` y sync/dispositivo del ctx."""
    return RegistroRaw(
        id=None,
        dispositivo_id=ctx.dispositivo_ids[dispositivo_idx],
        sincronizacion_id=ctx.sincronizacion_id,
        zkteco_user_id=zkteco_user_id,
        timestamp=timestamp,
        tipo_marcada=tipo,
    )


# ── Tests: happy paths por estado ─────────────────────────────────────────────


def test_consolidar_rango_vacio_sin_empleados_igual_emite_auditoria(
    setup: _Ctx,
) -> None:
    """Rango sin empleados activos: el servicio corre sin errores.

    ``asistencias_upsertadas == 0`` pero la entrada de auditoría se
    emite igual (la UI la necesita como marca temporal).
    """
    resultado = setup.service.consolidar_rango("2026-04-15", "2026-04-15")
    assert resultado.asistencias_upsertadas == 0
    assert resultado.empleados_procesados == 0
    assert resultado.dias_procesados == 1
    assert len(setup.audit_repo.entradas) == 1
    assert setup.audit_repo.entradas[0].action == "consolidacion.rango"


def test_consolida_presente_con_entrada_y_salida_dentro_de_tolerancia(
    setup: _Ctx,
) -> None:
    """Caso feliz: entrada 08:05 (tolerancia 10), salida 17:00 → PRESENTE."""
    emp_id = _crear_empleado(setup, "Juan", "Pérez", zkteco_id=101, indice=1)
    _asignar_turno(setup, emp_id, setup.turno_diurno_id)
    # 2026-04-15 es miércoles (día laboral).
    _insertar_raws(
        setup,
        [
            _raw(setup, 101, "2026-04-15T08:05:00", TipoMarcada.CHECK_IN.value),
            _raw(setup, 101, "2026-04-15T17:00:00", TipoMarcada.CHECK_OUT.value),
        ],
    )

    resultado = setup.service.consolidar_rango("2026-04-15", "2026-04-15")

    assert resultado.asistencias_upsertadas == 1
    assert resultado.empleados_procesados == 1
    asistencia = setup.asistencia_repo.get_by_empleado_y_fecha(emp_id, "2026-04-15")
    assert asistencia is not None
    assert asistencia.estado == EstadoAsistencia.PRESENTE.value
    assert asistencia.hora_entrada_real == "08:05:00"
    assert asistencia.hora_salida_real == "17:00:00"
    assert asistencia.minutos_tarde == 5
    assert asistencia.minutos_salida_temprana == 0


def test_consolida_tarde_cuando_entrada_fuera_de_tolerancia(setup: _Ctx) -> None:
    """Entrada 08:20 (tolerancia 10) + salida 17:00 → TARDE."""
    emp_id = _crear_empleado(setup, "Ana", "López", zkteco_id=102, indice=2)
    _asignar_turno(setup, emp_id, setup.turno_diurno_id)
    _insertar_raws(
        setup,
        [
            _raw(setup, 102, "2026-04-15T08:20:00", TipoMarcada.CHECK_IN.value),
            _raw(setup, 102, "2026-04-15T17:00:00", TipoMarcada.CHECK_OUT.value),
        ],
    )

    setup.service.consolidar_rango("2026-04-15", "2026-04-15")

    asistencia = setup.asistencia_repo.get_by_empleado_y_fecha(emp_id, "2026-04-15")
    assert asistencia is not None
    assert asistencia.estado == EstadoAsistencia.TARDE.value
    assert asistencia.minutos_tarde == 20


def test_consolida_ausente_cuando_turno_aplica_pero_no_hay_marcadas(
    setup: _Ctx,
) -> None:
    """Día laboral + turno vigente + cero marcadas → AUSENTE."""
    emp_id = _crear_empleado(setup, "Carlos", "Gómez", zkteco_id=103, indice=3)
    _asignar_turno(setup, emp_id, setup.turno_diurno_id)

    setup.service.consolidar_rango("2026-04-15", "2026-04-15")

    asistencia = setup.asistencia_repo.get_by_empleado_y_fecha(emp_id, "2026-04-15")
    assert asistencia is not None
    assert asistencia.estado == EstadoAsistencia.AUSENTE.value
    assert asistencia.turno_id_aplicado == setup.turno_diurno_id
    assert asistencia.hora_entrada_real is None
    assert asistencia.hora_salida_real is None


def test_consolida_dia_pasado_solo_con_entrada_asume_salida_segun_turno(
    setup: _Ctx,
) -> None:
    """Sub-2.7e: si la fecha de la asistencia es anterior a hoy, el día
    está cerrado → se asume salida = hora_salida del turno y se anota
    una observación auditable. Así no queda como INCOMPLETO una jornada
    que el operador no marcó al irse."""
    emp_id = _crear_empleado(setup, "Laura", "Díaz", zkteco_id=104, indice=4)
    _asignar_turno(setup, emp_id, setup.turno_diurno_id)
    _insertar_raws(
        setup,
        [_raw(setup, 104, "2026-04-15T08:00:00", TipoMarcada.CHECK_IN.value)],
    )

    setup.service.consolidar_rango("2026-04-15", "2026-04-15")

    asistencia = setup.asistencia_repo.get_by_empleado_y_fecha(emp_id, "2026-04-15")
    assert asistencia is not None
    assert asistencia.estado == EstadoAsistencia.PRESENTE.value
    assert asistencia.hora_entrada_real == "08:00:00"
    assert asistencia.hora_salida_real == "17:00:00"
    assert asistencia.observaciones is not None
    assert "Salida asumida" in asistencia.observaciones


def test_consolida_dia_abierto_solo_con_entrada_queda_incompleto(
    setup: _Ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sub-2.7e: si la fecha de la asistencia es la del día actual y aún
    no son las 20:00, el empleado todavía puede marcar la salida → la
    asistencia se deja en INCOMPLETO sin asumir nada.

    Forzamos ``_ahora_local`` con monkeypatch para que el algoritmo lo
    perciba como "mismo día a las 14:00" — sin acoplar el test al reloj
    de pared del runner.
    """
    from datetime import datetime as _dt

    from core.services import consolidacion_service as cs_mod

    emp_id = _crear_empleado(setup, "Laura", "Díaz", zkteco_id=104, indice=4)
    _asignar_turno(setup, emp_id, setup.turno_diurno_id)
    _insertar_raws(
        setup,
        [_raw(setup, 104, "2026-04-15T08:00:00", TipoMarcada.CHECK_IN.value)],
    )
    monkeypatch.setattr(cs_mod, "_ahora_local", lambda: _dt(2026, 4, 15, 14, 0))

    setup.service.consolidar_rango("2026-04-15", "2026-04-15")

    asistencia = setup.asistencia_repo.get_by_empleado_y_fecha(emp_id, "2026-04-15")
    assert asistencia is not None
    assert asistencia.estado == EstadoAsistencia.INCOMPLETO.value
    assert asistencia.hora_entrada_real == "08:00:00"
    assert asistencia.hora_salida_real is None
    # Sin observación: el operador todavía puede marcar.
    assert asistencia.observaciones is None


# ── Tests: SIN_TURNO / FERIADO ────────────────────────────────────────────────


def test_sin_turno_cuando_dia_no_esta_en_bitmask(setup: _Ctx) -> None:
    """Turno L-V + consolidar un domingo → SIN_TURNO aunque haya marcadas.

    Las marcadas del domingo se conservan en ``registros_raw`` (auditoría)
    pero NO cambian el estado — el día sigue siendo no laborable.
    """
    emp_id = _crear_empleado(setup, "Mario", "Ruiz", zkteco_id=105, indice=5)
    _asignar_turno(setup, emp_id, setup.turno_diurno_id)
    # 2026-04-19 es domingo.
    _insertar_raws(
        setup,
        [_raw(setup, 105, "2026-04-19T08:00:00", TipoMarcada.CHECK_IN.value)],
    )

    setup.service.consolidar_rango("2026-04-19", "2026-04-19")

    asistencia = setup.asistencia_repo.get_by_empleado_y_fecha(emp_id, "2026-04-19")
    assert asistencia is not None
    assert asistencia.estado == EstadoAsistencia.SIN_TURNO.value
    assert asistencia.turno_id_aplicado is None


def test_sin_turno_cuando_empleado_sin_historial(setup: _Ctx) -> None:
    """Empleado activo SIN asignación de turno → SIN_TURNO todos los días."""
    emp_id = _crear_empleado(setup, "Pedro", "Mendez", zkteco_id=106, indice=6)
    # No se llama a _asignar_turno a propósito.

    setup.service.consolidar_rango("2026-04-15", "2026-04-15")

    asistencia = setup.asistencia_repo.get_by_empleado_y_fecha(emp_id, "2026-04-15")
    assert asistencia is not None
    assert asistencia.estado == EstadoAsistencia.SIN_TURNO.value


def test_feriado_tiene_precedencia_sobre_sin_turno_y_presente(setup: _Ctx) -> None:
    """FERIADO gana sobre SIN_TURNO y sobre PRESENTE/AUSENTE.

    Se corre con dos empleados: uno con turno laboral y marcadas
    completas, otro sin turno. Ambos deben salir FERIADO.
    """
    emp_activo = _crear_empleado(setup, "Elena", "Hernández", zkteco_id=107, indice=7)
    emp_sin_turno = _crear_empleado(setup, "Jorge", "Ramos", zkteco_id=108, indice=8)
    _asignar_turno(setup, emp_activo, setup.turno_diurno_id)
    setup.feriado_repo.create(Feriado(id=None, fecha="2026-04-15", descripcion="Día de Prueba"))
    _insertar_raws(
        setup,
        [
            _raw(setup, 107, "2026-04-15T08:00:00", TipoMarcada.CHECK_IN.value),
            _raw(setup, 107, "2026-04-15T17:00:00", TipoMarcada.CHECK_OUT.value),
        ],
    )

    setup.service.consolidar_rango("2026-04-15", "2026-04-15")

    a1 = setup.asistencia_repo.get_by_empleado_y_fecha(emp_activo, "2026-04-15")
    a2 = setup.asistencia_repo.get_by_empleado_y_fecha(emp_sin_turno, "2026-04-15")
    assert a1 is not None and a2 is not None
    assert a1.estado == EstadoAsistencia.FERIADO.value
    assert a2.estado == EstadoAsistencia.FERIADO.value
    # ``turno_id_aplicado`` se deja en None para ambos (regla "no trabaja").
    assert a1.turno_id_aplicado is None
    assert a2.turno_id_aplicado is None


# ── Tests: turno nocturno ─────────────────────────────────────────────────────


def test_turno_nocturno_consolida_entrada_d_salida_d_mas_1(setup: _Ctx) -> None:
    """Vigilancia 22:00–06:00: entrada del 15 + salida del 16 → PRESENTE del 15.

    Verifica dos cosas:
        1. La ventana ±1 día del cargado de raws captura la marcada
           del 16.
        2. La asistencia se registra con fecha = 2026-04-15 (día de
           entrada), no con fecha del día de salida.
    """
    emp_id = _crear_empleado(setup, "Raúl", "Vigilante", zkteco_id=109, indice=9)
    _asignar_turno(setup, emp_id, setup.turno_nocturno_id)
    _insertar_raws(
        setup,
        [
            _raw(setup, 109, "2026-04-15T22:00:00", TipoMarcada.CHECK_IN.value),
            _raw(setup, 109, "2026-04-16T06:00:00", TipoMarcada.CHECK_OUT.value),
        ],
    )

    # Consolidamos solo el día 15 — la salida cae el 16 pero el servicio
    # extiende +1 día al cargar raws, por eso debe encontrarla.
    setup.service.consolidar_rango("2026-04-15", "2026-04-15")

    asistencia = setup.asistencia_repo.get_by_empleado_y_fecha(emp_id, "2026-04-15")
    assert asistencia is not None
    assert asistencia.estado == EstadoAsistencia.PRESENTE.value
    assert asistencia.hora_entrada_real == "22:00:00"
    assert asistencia.hora_salida_real == "06:00:00"


# ── Tests: marcadas de múltiples dispositivos ─────────────────────────────────


def test_marcadas_de_multiples_dispositivos_se_consolidan_juntas(
    setup: _Ctx,
) -> None:
    """Un empleado puede marcar entrada en reloj A y salida en reloj B.

    El servicio debe agrupar por ``zkteco_user_id`` (no filtrar por
    dispositivo) y consolidar ambas marcadas como un solo día PRESENTE.
    """
    emp_id = _crear_empleado(setup, "Sofía", "Mobile", zkteco_id=110, indice=10)
    _asignar_turno(setup, emp_id, setup.turno_diurno_id)
    _insertar_raws(
        setup,
        [
            _raw(setup, 110, "2026-04-15T08:00:00", TipoMarcada.CHECK_IN.value, dispositivo_idx=0),
            _raw(setup, 110, "2026-04-15T17:00:00", TipoMarcada.CHECK_OUT.value, dispositivo_idx=1),
        ],
    )

    setup.service.consolidar_rango("2026-04-15", "2026-04-15")

    asistencia = setup.asistencia_repo.get_by_empleado_y_fecha(emp_id, "2026-04-15")
    assert asistencia is not None
    assert asistencia.estado == EstadoAsistencia.PRESENTE.value


# ── Tests: marcadas desconocidas ──────────────────────────────────────────────


def test_marcadas_de_zkteco_id_sin_empleado_generan_warning(setup: _Ctx) -> None:
    """``zkteco_user_id`` sin empleado mapeado → ``MarcadaDesconocida``.

    El servicio debe reportarlo en el resultado (para la UI), pero NO
    crear ninguna fila en ``asistencias``. Las marcadas quedan en
    ``registros_raw`` (ya estaban, el servicio no las borra).
    """
    # Creamos 1 empleado con zkteco_id=201 pero insertamos raws de
    # zkteco_id=999 (no mapeado).
    _crear_empleado(setup, "Juan", "Mapped", zkteco_id=201, indice=11)
    _insertar_raws(
        setup,
        [
            _raw(setup, 999, "2026-04-15T08:00:00", TipoMarcada.CHECK_IN.value),
            _raw(setup, 999, "2026-04-15T17:00:00", TipoMarcada.CHECK_OUT.value),
        ],
    )

    resultado = setup.service.consolidar_rango("2026-04-15", "2026-04-15")

    assert len(resultado.marcadas_desconocidas) == 1
    desconocido = resultado.marcadas_desconocidas[0]
    assert desconocido.zkteco_user_id == 999
    assert desconocido.cantidad_marcadas == 2


def test_empleado_sin_zkteco_id_no_falla_y_queda_sin_turno(setup: _Ctx) -> None:
    """Empleado activo con ``zkteco_id=None``: se consolida sin marcadas.

    No hay forma de asociar raws con él, así que aunque tenga turno
    asignado y el día aplique, saldrá AUSENTE (sin marcadas). Lo clave
    es que el servicio NO explote.
    """
    emp_id = _crear_empleado(setup, "NoReloj", "Nuevo", zkteco_id=None, indice=12)
    _asignar_turno(setup, emp_id, setup.turno_diurno_id)

    setup.service.consolidar_rango("2026-04-15", "2026-04-15")

    asistencia = setup.asistencia_repo.get_by_empleado_y_fecha(emp_id, "2026-04-15")
    assert asistencia is not None
    assert asistencia.estado == EstadoAsistencia.AUSENTE.value


# ── Tests: idempotencia + observaciones ───────────────────────────────────────


def test_reconsolidar_preserva_observaciones_manuales(setup: _Ctx) -> None:
    """UPSERT idempotente: re-consolidar el mismo rango NO pisa ``observaciones``.

    Flujo:
        1. Consolidación inicial crea la fila (observaciones = NULL).
        2. Operador anota manualmente "Justificación médica".
        3. Se re-consolida tras corregir marcadas → la anotación sobrevive.
    """
    emp_id = _crear_empleado(setup, "Pablo", "Anotado", zkteco_id=111, indice=13)
    _asignar_turno(setup, emp_id, setup.turno_diurno_id)
    _insertar_raws(
        setup,
        [
            _raw(setup, 111, "2026-04-15T08:00:00", TipoMarcada.CHECK_IN.value),
            _raw(setup, 111, "2026-04-15T17:00:00", TipoMarcada.CHECK_OUT.value),
        ],
    )

    setup.service.consolidar_rango("2026-04-15", "2026-04-15")
    creada = setup.asistencia_repo.get_by_empleado_y_fecha(emp_id, "2026-04-15")
    assert creada is not None and creada.id is not None
    setup.asistencia_repo.update_observaciones(creada.id, "Justificación médica")

    # Re-consolidación — la fila calculada sería la misma, pero aunque
    # difiriera, observaciones no se debe tocar.
    setup.service.consolidar_rango("2026-04-15", "2026-04-15")

    releída = setup.asistencia_repo.get_by_empleado_y_fecha(emp_id, "2026-04-15")
    assert releída is not None
    assert releída.id == creada.id
    assert releída.observaciones == "Justificación médica"


# ── Tests: filtro de empleados activos ────────────────────────────────────────


def test_empleados_archivados_no_entran_a_la_consolidacion(setup: _Ctx) -> None:
    """Solo ``list_active`` → archivados se excluyen.

    Incluso si un empleado archivado tiene ``zkteco_id`` válido y hay
    raws a su nombre, NO debe generar fila en ``asistencias`` (la
    consolidación solo procesa activos).
    """
    emp_activo = _crear_empleado(setup, "Activo", "A", zkteco_id=301, indice=14)
    emp_archivado = _crear_empleado(setup, "Archivado", "B", zkteco_id=302, indice=15)
    _asignar_turno(setup, emp_activo, setup.turno_diurno_id)
    _asignar_turno(setup, emp_archivado, setup.turno_diurno_id)
    # Cerramos la asignación vigente del archivado antes de deactivate
    # (exigido por el flujo normal — el repo no lo hace automáticamente).
    setup.empleado_turno_repo.cerrar_vigente(emp_archivado, "2026-04-10")
    setup.empleado_repo.deactivate(
        emp_archivado,
        fecha_baja="2026-04-10",
        motivo_baja="RENUNCIA",
        nota_baja=None,
    )
    _insertar_raws(
        setup,
        [
            _raw(setup, 301, "2026-04-15T08:00:00", TipoMarcada.CHECK_IN.value),
            _raw(setup, 301, "2026-04-15T17:00:00", TipoMarcada.CHECK_OUT.value),
            _raw(setup, 302, "2026-04-15T08:00:00", TipoMarcada.CHECK_IN.value),
            _raw(setup, 302, "2026-04-15T17:00:00", TipoMarcada.CHECK_OUT.value),
        ],
    )

    resultado = setup.service.consolidar_rango("2026-04-15", "2026-04-15")

    # Solo 1 empleado procesado (el activo).
    assert resultado.empleados_procesados == 1
    assert resultado.asistencias_upsertadas == 1
    # No hay fila para el archivado.
    assert setup.asistencia_repo.get_by_empleado_y_fecha(emp_archivado, "2026-04-15") is None


# ── Tests: auditoría ──────────────────────────────────────────────────────────


def test_emite_entrada_de_auditoria_con_detalles_del_rango(setup: _Ctx) -> None:
    """La auditoría recibe action ``consolidacion.rango`` + detalles JSON."""
    emp_id = _crear_empleado(setup, "Aud", "Itor", zkteco_id=401, indice=16)
    _asignar_turno(setup, emp_id, setup.turno_diurno_id)
    _insertar_raws(
        setup,
        [
            _raw(setup, 401, "2026-04-15T08:00:00", TipoMarcada.CHECK_IN.value),
            _raw(setup, 401, "2026-04-15T17:00:00", TipoMarcada.CHECK_OUT.value),
        ],
    )

    setup.service.consolidar_rango("2026-04-13", "2026-04-17")

    assert len(setup.audit_repo.entradas) == 1
    entrada = setup.audit_repo.entradas[0]
    assert entrada.action == "consolidacion.rango"
    assert entrada.user_id is None
    detalles = json.loads(entrada.details or "{}")
    assert detalles["desde"] == "2026-04-13"
    assert detalles["hasta"] == "2026-04-17"
    assert detalles["empleados_procesados"] == 1
    assert detalles["asistencias_upsertadas"] == 5  # lunes..viernes
    assert detalles["desconocidos"] == 0


# ── Tests: validación de rango ────────────────────────────────────────────────


def test_rango_invertido_levanta_invalid_rango_error(setup: _Ctx) -> None:
    """``desde > hasta`` → InvalidRangoError antes de tocar la BD."""
    with pytest.raises(InvalidRangoError):
        setup.service.consolidar_rango("2026-04-30", "2026-04-01")
    # No emite auditoría si falla la validación.
    assert setup.audit_repo.entradas == []


def test_fecha_malformada_levanta_invalid_date_error(setup: _Ctx) -> None:
    """Formato no ISO → InvalidDateError. No se toca la BD."""
    with pytest.raises(InvalidDateError):
        setup.service.consolidar_rango("no-es-fecha", "2026-04-30")
    assert setup.audit_repo.entradas == []


# ── Tests: robustez frente a datos sucios ─────────────────────────────────────


def test_historial_con_fecha_invalida_registra_error_y_continua(
    setup: _Ctx,
) -> None:
    """Un empleado con historial corrupto aporta a ``errores_empleado``.

    Insertamos una asignación con ``fecha_inicio`` inválida vía SQL raw
    (el repo normal validaría). El otro empleado debe consolidarse
    normalmente — el fallo de uno NO aborta el batch.
    """
    emp_bueno = _crear_empleado(setup, "Ok", "Uno", zkteco_id=501, indice=17)
    emp_roto = _crear_empleado(setup, "Roto", "Dos", zkteco_id=502, indice=18)
    _asignar_turno(setup, emp_bueno, setup.turno_diurno_id)
    # Insertamos historial corrupto con fecha inválida saltándonos el
    # repo (el schema no tiene CHECK de formato ISO sobre fecha_inicio).
    with setup.db.transaction() as conn:
        conn.execute(
            "INSERT INTO empleado_turnos "
            "(empleado_id, turno_id, fecha_inicio, fecha_fin) "
            "VALUES (?, ?, ?, NULL)",
            (emp_roto, setup.turno_diurno_id, "NO_ES_FECHA"),
        )
    _insertar_raws(
        setup,
        [
            _raw(setup, 501, "2026-04-15T08:00:00", TipoMarcada.CHECK_IN.value),
            _raw(setup, 501, "2026-04-15T17:00:00", TipoMarcada.CHECK_OUT.value),
        ],
    )

    resultado = setup.service.consolidar_rango("2026-04-15", "2026-04-15")

    # El empleado bueno se consolidó.
    a_bueno = setup.asistencia_repo.get_by_empleado_y_fecha(emp_bueno, "2026-04-15")
    assert a_bueno is not None
    assert a_bueno.estado == EstadoAsistencia.PRESENTE.value
    # El empleado roto aporta a errores_empleado.
    assert len(resultado.errores_empleado) == 1
    mensaje = resultado.errores_empleado[0]
    assert f"empleado={emp_roto}" in mensaje
    assert "fecha=2026-04-15" in mensaje
    # Y NO tiene fila en asistencias.
    assert setup.asistencia_repo.get_by_empleado_y_fecha(emp_roto, "2026-04-15") is None


# ── Tests: multi-día ──────────────────────────────────────────────────────────


def test_rango_multi_dia_genera_una_asistencia_por_dia(setup: _Ctx) -> None:
    """Rango de 3 días × 1 empleado → 3 filas (1 por día)."""
    emp_id = _crear_empleado(setup, "Semanal", "Empleado", zkteco_id=601, indice=19)
    _asignar_turno(setup, emp_id, setup.turno_diurno_id)
    # Lunes 13, martes 14, miércoles 15 — todos laborales.
    _insertar_raws(
        setup,
        [
            _raw(setup, 601, "2026-04-13T08:00:00", TipoMarcada.CHECK_IN.value),
            _raw(setup, 601, "2026-04-13T17:00:00", TipoMarcada.CHECK_OUT.value),
            _raw(setup, 601, "2026-04-14T08:00:00", TipoMarcada.CHECK_IN.value),
            _raw(setup, 601, "2026-04-14T17:00:00", TipoMarcada.CHECK_OUT.value),
            # El miércoles faltó → AUSENTE.
        ],
    )

    resultado = setup.service.consolidar_rango("2026-04-13", "2026-04-15")

    assert resultado.dias_procesados == 3
    assert resultado.asistencias_upsertadas == 3
    rango = setup.asistencia_repo.list_by_empleado_y_rango(emp_id, "2026-04-13", "2026-04-15")
    estados = [(a.fecha, a.estado) for a in rango]
    assert estados == [
        ("2026-04-13", EstadoAsistencia.PRESENTE.value),
        ("2026-04-14", EstadoAsistencia.PRESENTE.value),
        ("2026-04-15", EstadoAsistencia.AUSENTE.value),
    ]


# ── Tests: re-consolidación filtrada por empleado (Sub-3.4c) ──────────────────


def test_consolidar_rango_filtrado_por_empleado_solo_procesa_ese(
    setup: _Ctx,
) -> None:
    """Pasar ``empleado_id`` restringe la consolidación a ese empleado."""
    emp_a = _crear_empleado(setup, "Ana", "Zapata", zkteco_id=701, indice=20)
    emp_b = _crear_empleado(setup, "Bruno", "Alvarado", zkteco_id=702, indice=21)
    _asignar_turno(setup, emp_a, setup.turno_diurno_id)
    _asignar_turno(setup, emp_b, setup.turno_diurno_id)
    _insertar_raws(
        setup,
        [
            _raw(setup, 701, "2026-04-15T08:00:00", TipoMarcada.CHECK_IN.value),
            _raw(setup, 701, "2026-04-15T17:00:00", TipoMarcada.CHECK_OUT.value),
            _raw(setup, 702, "2026-04-15T08:00:00", TipoMarcada.CHECK_IN.value),
            _raw(setup, 702, "2026-04-15T17:00:00", TipoMarcada.CHECK_OUT.value),
        ],
    )

    resultado = setup.service.consolidar_rango("2026-04-15", "2026-04-15", empleado_id=emp_a)

    # Solo procesó 1 empleado (el A) y generó 1 asistencia (la de A).
    assert resultado.empleados_procesados == 1
    assert resultado.asistencias_upsertadas == 1
    # Emp A quedó consolidado.
    assert setup.asistencia_repo.get_by_empleado_y_fecha(emp_a, "2026-04-15") is not None
    # Emp B NO fue tocado (no hay fila consolidada para él).
    assert setup.asistencia_repo.get_by_empleado_y_fecha(emp_b, "2026-04-15") is None
    # Marcadas desconocidas se omiten cuando hay filtro por empleado
    # (sino las raws de emp B aparecerían como "desconocidas").
    assert resultado.marcadas_desconocidas == []
    # La auditoría registra el ``empleado_id`` en los detalles.
    detalles = json.loads(setup.audit_repo.entradas[-1].details or "{}")
    assert detalles["empleado_id"] == emp_a


def test_consolidar_rango_filtrado_empleado_archivado_levanta_not_found(
    setup: _Ctx,
) -> None:
    """Si el ``empleado_id`` no está activo → EmpleadoNotFoundError."""
    emp_archivado = _crear_empleado(setup, "Archivo", "X", zkteco_id=703, indice=22)
    _asignar_turno(setup, emp_archivado, setup.turno_diurno_id)
    setup.empleado_turno_repo.cerrar_vigente(emp_archivado, "2026-04-10")
    setup.empleado_repo.deactivate(
        emp_archivado,
        fecha_baja="2026-04-10",
        motivo_baja="RENUNCIA",
        nota_baja=None,
    )

    with pytest.raises(EmpleadoNotFoundError) as exc_info:
        setup.service.consolidar_rango("2026-04-15", "2026-04-15", empleado_id=emp_archivado)
    assert exc_info.value.empleado_id == emp_archivado
    # No emite auditoría si la validación falla antes de procesar.
    assert setup.audit_repo.entradas == []
