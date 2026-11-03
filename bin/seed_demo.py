"""Seed de DEMO con datos ficticios (estilo DIPROMA) para revisar la app en desarrollo.

Uso::

    python -m bin.seed_demo                 # 4 semanas de marcaciones hasta hoy
    python -m bin.seed_demo --semanas 6
    python -m bin.seed_demo --db-path /otra/ruta/zkteco_app.db

Qué crea (todo ficticio):

    - Usuarios (contraseña de todos: ``12345678``):
        · ``admin``     SUPERADMIN (ve todo)
        · ``rrhh``      ADMIN
        · ``reportes``  REPORTES
        · ``operador``  OPERADOR
    - 7 departamentos, cada uno con sus cargos.
    - 3 turnos: Administrativo (L-V 09-17 y sábado 09-12), Operativo
      (L-V 07-16 y sábado 07-11) y Gerencia (L-V 09-17).
    - 20 empleados: 18 enrolados en el reloj, 1 pendiente de enrolar
      (sin ZKTeco ID) y 1 dado de baja a mitad del período.
    - 5 vacaciones / permisos de ejemplo: unas vacaciones pasadas y otras
      programadas, un permiso, un día libre y una incapacidad en curso.
    - 1 dispositivo "Reloj Principal K40" (192.168.1.201:4370). Ese reloj
      NO existe: si se presiona "Sincronizar" desde la UI la conexión falla
      (esperado — no estamos conectando el biométrico).
    - N semanas de marcaciones simuladas, sincronizadas una por semana a
      través del flujo REAL (``SincronizacionService`` + consolidación),
      usando ``FakeZKTecoAdapter`` en lugar del reloj.
    - Marcaciones de un ZKTeco ID que no existe en el sistema (98), para ver
      el aviso de "marcadas desconocidas".
    - 1 reporte Excel exportado, para que el historial de descargas no
      aparezca vacío.

Seguridad:
    - La contraseña de demo NO cumple la política de producción (exige
      letras y números), por eso los usuarios se insertan directo por
      repositorio en vez de pasar por el Setup Wizard. La política sigue
      aplicando a cualquier usuario o cambio de contraseña hecho desde la UI.
    - Aborta si la BD ya tiene empleados: este script es solo para una BD
      de desarrollo vacía. Para empezar limpio, borrar ``data/zkteco_app.db*``.
    - DNIs con prefijo ``9999-`` y correos ``@diproma.test`` (TLD reservado)
      para que nunca se confundan con datos reales.

Las marcaciones son realistas a propósito (gente que llega 15-20 minutos
antes, que sale unos minutos después de la hora, que olvida marcar la
salida, rebotes de huella). Así lo que muestra la vista de Asistencia es
lo que mostraría con datos reales.
"""

from __future__ import annotations

import argparse
import logging
import random
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import config
from core.models.ausencia import TipoAusencia
from core.models.empleado import MotivoBaja
from core.models.turno import DIAS_LABORALES, SABADO, HorarioTurno
from core.models.usuario import Usuario
from core.repositories.asistencia_repository_sqlite import AsistenciaRepositorySQLite
from core.repositories.audit_log_repository_sqlite import AuditLogRepositorySQLite
from core.repositories.ausencia_repository_sqlite import AusenciaRepositorySQLite
from core.repositories.cargo_repository_sqlite import CargoRepositorySQLite
from core.repositories.departamento_repository_sqlite import (
    DepartamentoRepositorySQLite,
)
from core.repositories.descarga_reporte_repository_sqlite import (
    DescargaReporteRepositorySQLite,
)
from core.repositories.dispositivo_repository_sqlite import DispositivoRepositorySQLite
from core.repositories.empleado_repository_sqlite import EmpleadoRepositorySQLite
from core.repositories.empleado_turno_repository_sqlite import (
    EmpleadoTurnoRepositorySQLite,
)
from core.repositories.feriado_repository_sqlite import FeriadoRepositorySQLite
from core.repositories.registro_raw_repository_sqlite import (
    RegistroRawRepositorySQLite,
)
from core.repositories.rol_repository_sqlite import RolRepositorySQLite
from core.repositories.sincronizacion_repository_sqlite import (
    SincronizacionRepositorySQLite,
)
from core.repositories.turno_repository_sqlite import TurnoRepositorySQLite
from core.repositories.usuario_repository_sqlite import UsuarioRepositorySQLite
from core.services.asistencia_service import AsistenciaService
from core.services.audit_logger import AuditLogger
from core.services.ausencia_service import AusenciaService
from core.services.catalogo_service import CatalogoService
from core.services.consolidacion_service import ConsolidacionService
from core.services.dispositivo_config_service import DispositivoConfigService
from core.services.empleado_service import EmpleadoService
from core.services.reporte_service import ReporteService
from core.services.sincronizacion_service import SincronizacionService
from core.services.turno_service import TurnoService
from infrastructure import paths
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner
from infrastructure.exporters.xlsx_asistencia_exporter import XlsxAsistenciaExporter
from infrastructure.security.bcrypt_hasher import BcryptHasher
from infrastructure.zkteco.fake_adapter import FakeZKTecoAdapter

# ── Datos de la demo ──────────────────────────────────────────────────────────

PASSWORD_DEMO = "12345678"

_SUPERADMIN = ("admin", "Administrador del Sistema")
# (username, nombre completo, código de rol)
_USUARIOS_EXTRA = (
    ("rrhh", "Recursos Humanos", "ADMIN"),
    ("reportes", "Usuario de Reportes", "REPORTES"),
    ("operador", "Operador del Reloj", "OPERADOR"),
)

_DISPOSITIVO = ("Reloj Principal K40", "192.168.1.201", 4370)

# ZKTeco ID enrolado en el reloj que NO corresponde a ningún empleado.
_ZKTECO_ID_DESCONOCIDO = 98

_DEPARTAMENTOS: Dict[str, Tuple[str, ...]] = {
    "Gerencia": ("Gerente General",),
    "Administración": ("Asistente Administrativo", "Recepcionista"),
    "Contabilidad": ("Contador General", "Auxiliar Contable"),
    "Ventas": ("Jefe de Ventas", "Vendedor", "Cajero"),
    "Bodega": ("Encargado de Bodega", "Auxiliar de Bodega"),
    "Logística": ("Motorista", "Ayudante de Reparto"),
    "Informática": ("Soporte Técnico",),
}


@dataclass(frozen=True)
class _TurnoSpec:
    clave: str
    nombre: str
    entrada: str
    salida: str
    descanso: int
    dias: int
    # Horario distinto el sábado dentro del MISMO turno: (entrada, salida).
    sabado: Optional[Tuple[str, str]] = None


_TURNOS = (
    _TurnoSpec("ADM", "Administrativo", "09:00", "17:00", 60, DIAS_LABORALES, ("09:00", "12:00")),
    _TurnoSpec("OPE", "Operativo", "07:00", "16:00", 60, DIAS_LABORALES, ("07:00", "11:00")),
    _TurnoSpec("GER", "Gerencia", "09:00", "17:00", 60, DIAS_LABORALES),
)

# Jornada → claves de turno asignadas (gerencia no marca sábados).
_JORNADAS: Dict[str, Tuple[str, ...]] = {
    "ADM": ("ADM",),
    "OPE": ("OPE",),
    "GER": ("GER",),
}


@dataclass(frozen=True)
class _Perfil:
    """Comportamiento de marcación (minutos respecto a la hora oficial)."""

    llegada_media: float  # negativo = llega antes
    llegada_desv: float
    p_ausencia: float
    p_olvida_salida: float
    p_sale_temprano: float


_PERFILES: Dict[str, _Perfil] = {
    "puntual": _Perfil(-15, 6, 0.01, 0.02, 0.01),
    "normal": _Perfil(-4, 7, 0.03, 0.04, 0.04),
    "impuntual": _Perfil(9, 9, 0.07, 0.06, 0.08),
}


@dataclass(frozen=True)
class _EmpleadoSpec:
    nombres: str
    apellidos: str
    departamento: str
    cargo: str
    jornada: str
    perfil: str
    ingreso: str
    zkteco_id: Optional[int]
    baja: bool = False


_EMPLEADOS = (
    _EmpleadoSpec(
        "Ricardo Alberto",
        "Paz Mejía",
        "Gerencia",
        "Gerente General",
        "GER",
        "normal",
        "2018-02-01",
        1,
    ),
    _EmpleadoSpec(
        "Karla Yessenia",
        "Villeda Romero",
        "Administración",
        "Asistente Administrativo",
        "ADM",
        "puntual",
        "2020-06-15",
        2,
    ),
    _EmpleadoSpec(
        "Daniela María",
        "Pineda Sorto",
        "Administración",
        "Recepcionista",
        "ADM",
        "normal",
        "2023-03-01",
        3,
    ),
    _EmpleadoSpec(
        "José Luis",
        "Hernández Alvarado",
        "Contabilidad",
        "Contador General",
        "ADM",
        "puntual",
        "2019-01-07",
        4,
    ),
    _EmpleadoSpec(
        "Ana Gabriela",
        "Interiano Lara",
        "Contabilidad",
        "Auxiliar Contable",
        "ADM",
        "normal",
        "2022-08-01",
        5,
    ),
    _EmpleadoSpec(
        "Marvin Josué",
        "Rodríguez Erazo",
        "Ventas",
        "Jefe de Ventas",
        "ADM",
        "normal",
        "2019-09-02",
        6,
    ),
    _EmpleadoSpec(
        "Wendy Carolina",
        "Guerra Tábora",
        "Ventas",
        "Vendedor",
        "ADM",
        "puntual",
        "2021-04-12",
        7,
    ),
    _EmpleadoSpec(
        "Luis Fernando",
        "Chinchilla Ramos",
        "Ventas",
        "Vendedor",
        "ADM",
        "impuntual",
        "2024-01-15",
        8,
    ),
    _EmpleadoSpec(
        "Gabriela Alejandra",
        "Madrid Leiva",
        "Ventas",
        "Cajero",
        "ADM",
        "normal",
        "2022-11-01",
        9,
    ),
    _EmpleadoSpec(
        "Kevin Adonay",
        "Orellana Bueso",
        "Ventas",
        "Vendedor",
        "ADM",
        "impuntual",
        "2025-02-03",
        10,
    ),
    _EmpleadoSpec(
        "Héctor Manuel",
        "Castellanos Rivera",
        "Bodega",
        "Encargado de Bodega",
        "OPE",
        "puntual",
        "2017-05-02",
        11,
    ),
    _EmpleadoSpec(
        "Brayan Josué",
        "Sánchez Pérez",
        "Bodega",
        "Auxiliar de Bodega",
        "OPE",
        "normal",
        "2023-07-10",
        12,
    ),
    _EmpleadoSpec(
        "Óscar Danilo",
        "Portillo Muñoz",
        "Bodega",
        "Auxiliar de Bodega",
        "OPE",
        "impuntual",
        "2024-09-02",
        13,
    ),
    _EmpleadoSpec(
        "Carlos Roberto",
        "Fajardo Mejía",
        "Logística",
        "Motorista",
        "OPE",
        "normal",
        "2020-02-17",
        14,
    ),
    _EmpleadoSpec(
        "Elvin Ariel",
        "Tejada Cruz",
        "Logística",
        "Motorista",
        "OPE",
        "puntual",
        "2021-10-04",
        15,
    ),
    _EmpleadoSpec(
        "Jonathan Isaí",
        "López Ayala",
        "Logística",
        "Ayudante de Reparto",
        "OPE",
        "impuntual",
        "2025-05-05",
        16,
    ),
    _EmpleadoSpec(
        "Nelson Geovany",
        "Amaya Díaz",
        "Logística",
        "Ayudante de Reparto",
        "OPE",
        "normal",
        "2024-03-11",
        17,
    ),
    _EmpleadoSpec(
        "Andrea Sofía",
        "Moreno Lemus",
        "Informática",
        "Soporte Técnico",
        "ADM",
        "normal",
        "2023-01-09",
        18,
    ),
    # Recién contratada: todavía no la enrolan en el reloj.
    _EmpleadoSpec(
        "Fernanda Lucía",
        "Ruiz Galeas",
        "Ventas",
        "Vendedor",
        "ADM",
        "normal",
        "2026-09-01",
        None,
    ),
    # Renuncia a mitad del período de la demo.
    _EmpleadoSpec(
        "Mario Enrique",
        "Duarte Saravia",
        "Bodega",
        "Auxiliar de Bodega",
        "OPE",
        "normal",
        "2022-04-18",
        19,
        baja=True,
    ),
)

# Status crudos del firmware ZKTeco (ver infrastructure/zkteco/status_mapping.py).
_STATUS_ENTRADA = 0
_STATUS_SALIDA = 1


# ── Main ──────────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    """Ejecuta el seed. Devuelve el exit code."""
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format=config.LOG_FORMAT,
    )

    if args.db_path is None:
        paths.ensure_runtime_dirs()
        db_path = config.DATABASE_PATH
    else:
        db_path = Path(args.db_path).resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)

    database = Database(db_path)
    MigrationsRunner(database, config.MIGRATIONS_DIR).run()

    if _contar(database, "empleados") > 0:
        print(
            f"✗ La BD {db_path} ya tiene empleados. Este seed es solo para una BD vacía."
            "\n  Para empezar limpio, cerrá la app y borrá data/zkteco_app.db*"
        )
        return 1

    hoy = date.today()
    ahora = datetime.now()
    lunes_actual = hoy - timedelta(days=hoy.weekday())
    desde = lunes_actual - timedelta(weeks=args.semanas - 1)
    rng = random.Random(args.semilla)

    _titulo(db_path, desde, hoy)
    s = _Servicios(database)

    actor_id = _crear_usuarios(s)
    turnos = _crear_turnos(s, actor_id)
    cargos = _crear_catalogos(s, actor_id)
    dispositivo_id = s.dispositivos.create_dispositivo(
        nombre=_DISPOSITIVO[0], ip=_DISPOSITIVO[1], puerto=_DISPOSITIVO[2], actor_user_id=actor_id
    ).id
    assert dispositivo_id is not None

    empleados = _crear_empleados(s, actor_id, cargos, turnos)
    ausencias = _crear_ausencias(s, empleados, desde, hoy, actor_id)

    # La baja ocurre el viernes previo a la penúltima semana (o la última si
    # la demo es corta). Desde esa semana el empleado ya no se consolida.
    # Con --semanas 1 no hay semana siguiente: el empleado sigue activo.
    semana_baja = max(1, args.semanas - 2)
    lunes_baja = desde + timedelta(weeks=semana_baja)
    fecha_baja = lunes_baja - timedelta(days=3) if lunes_baja <= hoy else date.max

    adapter = s.adapter
    total_marcas = _generar_marcaciones(
        adapter, dispositivo_id, empleados, turnos, desde, ahora, fecha_baja, rng, ausencias
    )
    print(f"  · Marcaciones simuladas en el reloj: {total_marcas}")

    # Una sincronización por semana, como lo haría el operador.
    lunes = desde
    while lunes <= hoy:
        if lunes == lunes_baja:
            _dar_de_baja(s, empleados, fecha_baja, actor_id)
        domingo = min(lunes + timedelta(days=6), hoy)
        resultado = s.sincronizacion.ejecutar(
            dispositivo_id=dispositivo_id,
            rango_desde=lunes.isoformat(),
            rango_hasta=domingo.isoformat(),
            iniciada_por_user_id=actor_id,
        )
        cierre = datetime.combine(lunes + timedelta(days=5), time(17, 30))
        if cierre < ahora:
            _fechar_sync(database, resultado.sincronizacion_id, cierre)
        desconocidas = (
            len(resultado.consolidacion.marcadas_desconocidas) if resultado.consolidacion else 0
        )
        print(
            f"  · Sync {lunes:%d/%m}–{domingo:%d/%m}: "
            f"{resultado.registros_recibidos} marcadas, "
            f"{desconocidas} ID(s) desconocido(s)"
            + (
                f", ERROR consolidación: {resultado.error_consolidacion}"
                if resultado.error_consolidacion
                else ""
            )
        )
        lunes += timedelta(weeks=1)

    _exportar_reporte(s, db_path, desde, hoy, actor_id)
    _resumen(database)
    return 0


# ── Argumentos ────────────────────────────────────────────────────────────────


def _parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m bin.seed_demo",
        description="Siembra datos ficticios de demostración en una BD de desarrollo vacía.",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Ruta a la BD SQLite. Por defecto: data/zkteco_app.db del proyecto.",
    )
    parser.add_argument(
        "--semanas",
        type=int,
        default=4,
        choices=range(1, 27),
        metavar="N",
        help="Semanas de marcaciones a generar, terminando hoy (1-26, default 4).",
    )
    parser.add_argument(
        "--semilla",
        type=int,
        default=2026,
        help="Semilla aleatoria; misma semilla = mismos datos (default 2026).",
    )
    parser.add_argument("--verbose", action="store_true", help="Logs de nivel DEBUG.")
    return parser.parse_args(argv)


# ── Composición de servicios (mismo cableado que ui/app.py, con reloj falso) ──


class _Servicios:
    def __init__(self, database: Database) -> None:
        usuario_repo = UsuarioRepositorySQLite(database)
        rol_repo = RolRepositorySQLite(database)
        dep_repo = DepartamentoRepositorySQLite(database)
        cargo_repo = CargoRepositorySQLite(database)
        empleado_repo = EmpleadoRepositorySQLite(database)
        et_repo = EmpleadoTurnoRepositorySQLite(database)
        turno_repo = TurnoRepositorySQLite(database)
        dispositivo_repo = DispositivoRepositorySQLite(database)
        sync_repo = SincronizacionRepositorySQLite(database)
        raw_repo = RegistroRawRepositorySQLite(database)
        asistencia_repo = AsistenciaRepositorySQLite(database)
        descarga_repo = DescargaReporteRepositorySQLite(database)

        ausencia_repo = AusenciaRepositorySQLite(database)
        feriado_repo = FeriadoRepositorySQLite(database)
        audit = AuditLogger(AuditLogRepositorySQLite(database))
        self.hasher = BcryptHasher(config.BCRYPT_COST_FACTOR)
        self.usuario_repo = usuario_repo
        self.rol_repo = rol_repo
        self.adapter = FakeZKTecoAdapter()
        self.catalogo = CatalogoService(
            dep_read=dep_repo,
            dep_write=dep_repo,
            cargo_read=cargo_repo,
            cargo_write=cargo_repo,
            empleado_read=empleado_repo,
            audit_logger=audit,
        )
        self.turnos = TurnoService(
            turno_read=turno_repo, turno_write=turno_repo, audit_logger=audit
        )
        self.empleados = EmpleadoService(
            empleado_read=empleado_repo,
            empleado_write=empleado_repo,
            empleado_turno_read=et_repo,
            empleado_turno_write=et_repo,
            departamento_read=dep_repo,
            cargo_read=cargo_repo,
            turno_read=turno_repo,
            audit_logger=audit,
        )
        self.dispositivos = DispositivoConfigService(
            dispositivo_read=dispositivo_repo,
            dispositivo_write=dispositivo_repo,
            audit_logger=audit,
        )
        consolidacion = ConsolidacionService(
            empleado_read=empleado_repo,
            empleado_turno_read=et_repo,
            turno_read=turno_repo,
            feriado_read=feriado_repo,
            registro_raw_read=raw_repo,
            asistencia_write=asistencia_repo,
            audit_logger=audit,
            ausencia_read=ausencia_repo,
        )
        self.consolidacion = consolidacion
        self.feriados = feriado_repo
        self.audit = audit
        self.empleado_repo = empleado_repo
        self.turno_repo = turno_repo
        self.asistencia_repo = asistencia_repo
        self.ausencias = AusenciaService(
            ausencia_read=ausencia_repo,
            ausencia_write=ausencia_repo,
            empleado_read=empleado_repo,
            empleado_turno_read=et_repo,
            turno_read=turno_repo,
            feriado_read=feriado_repo,
            audit_logger=audit,
            consolidacion_service=consolidacion,
        )
        self.sincronizacion = SincronizacionService(
            dispositivo_read=dispositivo_repo,
            sincronizacion_read=sync_repo,
            sincronizacion_write=sync_repo,
            registro_raw_write=raw_repo,
            adapter=self.adapter,
            audit_logger=audit,
            consolidador=consolidacion,
        )
        asistencia = AsistenciaService(
            asistencia_read=asistencia_repo,
            asistencia_write=asistencia_repo,
            empleado_read=empleado_repo,
            turno_read=turno_repo,
            audit_logger=audit,
            consolidacion_service=consolidacion,
        )
        self.reportes = ReporteService(
            asistencia_service=asistencia,
            descarga_read=descarga_repo,
            descarga_write=descarga_repo,
            exporter=XlsxAsistenciaExporter(),
            audit_logger=audit,
        )


# ── Pasos del seed ────────────────────────────────────────────────────────────


def _crear_usuarios(s: _Servicios) -> int:
    """Crea el SUPERADMIN y los usuarios de ejemplo. Devuelve el id del admin.

    Se insertan por repositorio (no por SetupWizard/UsuarioAdmin) para poder
    usar la contraseña simple de desarrollo, que la política rechazaría.
    """
    roles = {rol.code: rol.id for rol in s.rol_repo.list_all()}
    password_hash = s.hasher.hash(PASSWORD_DEMO)
    todos = ((_SUPERADMIN[0], _SUPERADMIN[1], "SUPERADMIN"), *_USUARIOS_EXTRA)
    ids: Dict[str, int] = {}
    for username, full_name, rol_code in todos:
        role_id = roles[rol_code]
        assert role_id is not None
        creado = s.usuario_repo.create(
            Usuario(
                id=None,
                username=username,
                password_hash=password_hash,
                full_name=full_name,
                role_id=role_id,
                is_active=True,
            )
        )
        assert creado.id is not None
        ids[username] = creado.id
    print(
        f"  · Usuarios: admin (SUPERADMIN) + {len(_USUARIOS_EXTRA)} · contraseña: {PASSWORD_DEMO}"
    )
    return ids[_SUPERADMIN[0]]


def _crear_turnos(s: _Servicios, actor_id: int) -> Dict[str, _TurnoCreado]:
    creados: Dict[str, _TurnoCreado] = {}
    for spec in _TURNOS:
        turno = s.turnos.create_turno(
            nombre=spec.nombre,
            hora_entrada=spec.entrada,
            hora_salida=spec.salida,
            minutos_descanso=spec.descanso,
            dias_semana=spec.dias,
            actor_user_id=actor_id,
            horarios_extra=(
                [HorarioTurno(SABADO, spec.sabado[0], spec.sabado[1])] if spec.sabado else []
            ),
        )
        assert turno.id is not None
        creados[spec.clave] = _TurnoCreado(turno.id, spec)
    print(f"  · Turnos: {len(creados)}")
    return creados


@dataclass(frozen=True)
class _TurnoCreado:
    id: int
    spec: _TurnoSpec


def _crear_catalogos(s: _Servicios, actor_id: int) -> Dict[str, Tuple[int, int]]:
    """Crea departamentos y cargos. Devuelve ``cargo → (departamento_id, cargo_id)``."""
    cargos: Dict[str, Tuple[int, int]] = {}
    for nombre_dep, nombres_cargo in _DEPARTAMENTOS.items():
        dep = s.catalogo.create_departamento(nombre_dep, actor_user_id=actor_id)
        assert dep.id is not None
        for nombre_cargo in nombres_cargo:
            cargo = s.catalogo.create_cargo(
                nombre_cargo, actor_user_id=actor_id, departamento_id=dep.id
            )
            assert cargo.id is not None
            cargos[nombre_cargo] = (dep.id, cargo.id)
    print(f"  · Departamentos: {len(_DEPARTAMENTOS)} · Cargos: {len(cargos)}")
    return cargos


@dataclass(frozen=True)
class _EmpleadoCreado:
    id: int
    spec: _EmpleadoSpec


def _crear_empleados(
    s: _Servicios,
    actor_id: int,
    cargos: Dict[str, Tuple[int, int]],
    turnos: Dict[str, _TurnoCreado],
) -> List[_EmpleadoCreado]:
    creados: List[_EmpleadoCreado] = []
    for n, spec in enumerate(_EMPLEADOS, start=1):
        dep_id, cargo_id = cargos[spec.cargo]
        nacimiento = 1975 + (n * 7) % 26
        primer_nombre = spec.nombres.split()[0].lower()
        primer_apellido = spec.apellidos.split()[0].lower()
        email = f"{_sin_tildes(primer_nombre)}.{_sin_tildes(primer_apellido)}@diproma.test"
        emp = s.empleados.create_empleado(
            dni=f"9999-{nacimiento}-{n:05d}",
            nombres=spec.nombres,
            apellidos=spec.apellidos,
            departamento_id=dep_id,
            cargo_id=cargo_id,
            fecha_ingreso=spec.ingreso,
            actor_user_id=actor_id,
            telefono=f"9999-{n:04d}",
            email=email,
            zkteco_id=spec.zkteco_id,
        )
        assert emp.id is not None
        for clave in _JORNADAS[spec.jornada]:
            s.empleados.asignar_turno(
                empleado_id=emp.id,
                turno_id=turnos[clave].id,
                fecha_inicio=spec.ingreso,
                actor_user_id=actor_id,
            )
        creados.append(_EmpleadoCreado(emp.id, spec))
    pendientes = sum(1 for e in creados if e.spec.zkteco_id is None)
    print(f"  · Empleados: {len(creados)} ({pendientes} pendiente de enrolar en el reloj)")
    return creados


def _crear_ausencias(
    s: _Servicios,
    empleados: List[_EmpleadoCreado],
    desde: date,
    hoy: date,
    actor_id: int,
) -> Dict[int, List[Tuple[date, date]]]:
    """Registra vacaciones y permisos de ejemplo. Devuelve ``{empleado_id: [rangos]}``."""
    candidatos = [e for e in empleados if e.spec.zkteco_id is not None and not e.spec.baja]
    lunes_actual = hoy - timedelta(days=hoy.weekday())
    lunes_pasado = max(desde, lunes_actual - timedelta(weeks=2))
    proximo_lunes = lunes_actual + timedelta(weeks=1)
    ayer_habil = _dia_habil_anterior(hoy - timedelta(days=1))
    plan = (
        (
            TipoAusencia.VACACIONES,
            lunes_pasado,
            lunes_pasado + timedelta(days=4),
            "Vacaciones anuales (dato de demostración).",
        ),
        (
            TipoAusencia.PERMISO,
            _dia_habil_anterior(lunes_actual - timedelta(days=4)),
            _dia_habil_anterior(lunes_actual - timedelta(days=4)),
            "Cita médica por la mañana.",
        ),
        (TipoAusencia.DIA_LIBRE, ayer_habil, ayer_habil, "Día libre por cumpleaños."),
        (
            TipoAusencia.VACACIONES,
            proximo_lunes,
            proximo_lunes + timedelta(days=4),
            "Vacaciones programadas.",
        ),
        (
            TipoAusencia.INCAPACIDAD,
            hoy - timedelta(days=1),
            hoy + timedelta(days=2),
            "Constancia médica entregada en Recursos Humanos.",
        ),
    )
    rangos: Dict[int, List[Tuple[date, date]]] = {}
    for emp, (tipo, inicio, fin, nota) in zip(candidatos[2::3], plan):
        s.ausencias.registrar(
            empleado_id=emp.id,
            tipo=tipo.value,
            fecha_desde=inicio.isoformat(),
            fecha_hasta=fin.isoformat(),
            nota=nota,
            actor_user_id=actor_id,
        )
        rangos.setdefault(emp.id, []).append((inicio, fin))
    print(f"  · Vacaciones y permisos: {sum(len(r) for r in rangos.values())}")
    return rangos


def _dia_habil_anterior(dia: date) -> date:
    """El mismo día si es de lunes a viernes; si no, el viernes anterior."""
    while dia.weekday() >= 5:
        dia -= timedelta(days=1)
    return dia


def _generar_marcaciones(
    adapter: FakeZKTecoAdapter,
    dispositivo_id: int,
    empleados: List[_EmpleadoCreado],
    turnos: Dict[str, _TurnoCreado],
    desde: date,
    ahora: datetime,
    fecha_baja: date,
    rng: random.Random,
    ausencias: Optional[Dict[int, List[Tuple[date, date]]]] = None,
) -> int:
    """Carga en el reloj falso las marcaciones del período. Devuelve cuántas."""
    total = 0

    def marcar(zk_id: int, ts: datetime, status: int) -> None:
        nonlocal total
        if ts > ahora:
            return
        adapter.preload_attendance(
            dispositivo_id=dispositivo_id,
            zkteco_user_id=zk_id,
            timestamp=ts.replace(microsecond=0),
            status=status,
        )
        total += 1

    dia = desde
    while dia <= ahora.date():
        for emp in empleados:
            spec = emp.spec
            if spec.zkteco_id is None:
                continue
            if spec.baja and dia > fecha_baja:
                continue
            horario = _horario_del_dia(spec.jornada, dia, turnos)
            if horario is None:
                continue
            if any(ini <= dia <= fin for ini, fin in (ausencias or {}).get(emp.id, [])):
                continue  # de vacaciones / con permiso: no marca
            perfil = _PERFILES[spec.perfil]
            if rng.random() < perfil.p_ausencia:
                continue

            entrada_oficial = datetime.combine(dia, _hora(horario[0]))
            salida_oficial = datetime.combine(dia, _hora(horario[1]))

            atraso = max(-45.0, min(75.0, rng.gauss(perfil.llegada_media, perfil.llegada_desv)))
            entrada = entrada_oficial + timedelta(minutes=atraso, seconds=rng.randint(0, 59))
            marcar(spec.zkteco_id, entrada, _STATUS_ENTRADA)

            # Rebote: el dedo se apoya dos veces al entrar.
            if rng.random() < 0.08:
                marcar(spec.zkteco_id, entrada + timedelta(seconds=rng.randint(4, 90)), 0)

            if rng.random() < perfil.p_olvida_salida:
                continue
            if rng.random() < perfil.p_sale_temprano:
                extra = -rng.uniform(20, 90)
            else:
                extra = rng.uniform(0, 14)
            salida = salida_oficial + timedelta(minutes=extra, seconds=rng.randint(0, 59))
            marcar(spec.zkteco_id, salida, _STATUS_SALIDA)

        # Alguien enrolado en el reloj que nunca se registró en el sistema.
        if dia.weekday() < 5 and rng.random() < 0.25:
            marcar(_ZKTECO_ID_DESCONOCIDO, datetime.combine(dia, time(8, rng.randint(30, 59))), 0)

        dia += timedelta(days=1)
    return total


def _horario_del_dia(
    jornada: str, dia: date, turnos: Dict[str, _TurnoCreado]
) -> Optional[Tuple[str, str]]:
    """``(entrada, salida)`` que le toca a la jornada ese día, o ``None``."""
    for clave in _JORNADAS[jornada]:
        spec = turnos[clave].spec
        if spec.dias & _bit_dia(dia):
            return spec.entrada, spec.salida
        if spec.sabado and _bit_dia(dia) == SABADO:
            return spec.sabado
    return None


def _bit_dia(dia: date) -> int:
    """Bit del bitmask ``dias_semana`` (Lunes = 1<<6 … Domingo = 1<<0)."""
    return 1 << (6 - dia.weekday())


def _dar_de_baja(
    s: _Servicios, empleados: List[_EmpleadoCreado], fecha_baja: date, actor_id: int
) -> None:
    for emp in empleados:
        if emp.spec.baja:
            s.empleados.deactivate_empleado(
                empleado_id=emp.id,
                fecha_baja=fecha_baja.isoformat(),
                motivo_baja=MotivoBaja.RENUNCIA.value,
                actor_user_id=actor_id,
                nota_baja="Renuncia voluntaria (dato de demostración).",
            )
            print(f"  · Baja: {emp.spec.nombres} {emp.spec.apellidos} ({fecha_baja:%d/%m/%Y})")


def _fechar_sync(database: Database, sincronizacion_id: int, cuando_local: datetime) -> None:
    """Mueve inicio/fin de una sync al momento en que la habría corrido el operador.

    Solo cosmético para la demo: sin esto todas las syncs del historial
    aparecerían con la hora en que se corrió el seed.
    """
    inicio = cuando_local.astimezone(timezone.utc).replace(microsecond=0)
    fin = inicio + timedelta(seconds=42)
    with database.transaction() as conn:
        conn.execute(
            "UPDATE sincronizaciones SET inicio = ?, fin = ? WHERE id = ?",
            (inicio.isoformat(), fin.isoformat(), sincronizacion_id),
        )


def _exportar_reporte(
    s: _Servicios, db_path: Path, desde: date, hasta: date, actor_id: int
) -> None:
    exports = db_path.parent / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    archivo = exports / f"asistencia_{desde.isoformat()}_{hasta.isoformat()}.xlsx"
    descarga = s.reportes.exportar_asistencia(
        desde=desde.isoformat(),
        hasta=hasta.isoformat(),
        output_path=str(archivo),
        actor_user_id=actor_id,
        actor_username=_SUPERADMIN[0],
    )
    print(f"  · Reporte Excel: {archivo.name} ({descarga.filas_exportadas} filas)")


# ── Salida por consola ────────────────────────────────────────────────────────


def _titulo(db_path: Path, desde: date, hasta: date) -> None:
    print("═══════════════════════════════════════════════════════════════")
    print("  Seed de DEMO — datos ficticios")
    print(f"  BD: {db_path}")
    print(f"  Período: {desde:%d/%m/%Y} → {hasta:%d/%m/%Y}")
    print("═══════════════════════════════════════════════════════════════")


def _resumen(database: Database) -> None:
    conn = database.connect()
    try:
        estados = Counter(
            {
                row["estado"]: row["n"]
                for row in conn.execute(
                    "SELECT estado, COUNT(*) AS n FROM asistencias GROUP BY estado"
                )
            }
        )
    finally:
        conn.close()
    print("───────────────────────────────────────────────────────────────")
    print(f"  Marcadas guardadas:  {_contar(database, 'registros_raw')}")
    print(f"  Asistencias:         {_contar(database, 'asistencias')}")
    for estado, n in estados.most_common():
        print(f"      {estado:<26}{n:>5}")
    print("───────────────────────────────────────────────────────────────")
    print(f"  ✓ Listo. Usuario: admin · Contraseña: {PASSWORD_DEMO}")


# ── Utilidades ────────────────────────────────────────────────────────────────


def _contar(database: Database, tabla: str) -> int:
    conn = database.connect()
    try:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {tabla}").fetchone()  # noqa: S608
        return int(row["n"])
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def _hora(hhmm: str) -> time:
    h, m = hhmm.split(":")
    return time(int(h), int(m))


def _sin_tildes(texto: str) -> str:
    return texto.translate(str.maketrans("áéíóúñü", "aeiounu"))


if __name__ == "__main__":
    sys.exit(main())
