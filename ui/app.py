"""Composition root de la UI.

Este módulo es el único lugar donde se instancian los servicios,
controllers y vistas. El resto del árbol recibe sus dependencias por
inyección en constructor — cumpliendo la REGLA SOLID-D del proyecto.

Flujo (router):
    1. Abre la BD y corre migraciones pendientes.
    2. Construye los servicios una sola vez (auth, permisos, setup, etc.).
    3. Crea la ``CTk`` raíz.
    4. Si ``SetupWizardService.is_first_run()`` → monta ``SetupWizardFrame``.
       Tras crear el SUPERADMIN, cae al login.
    5. Si no es first-run → monta ``LoginFrame``.
    6. Tras login OK → monta ``MainFrame`` con la sesión.
    7. En logout → descarta la sesión y vuelve al login.

Threading:
    - La verificación bcrypt del login es I/O-bound ligera (~250ms).
      Se despacha al hilo daemon via ``run_async_ui`` inyectado en el
      ``LoginController``. La UI queda siempre responsiva.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Callable, Optional

import customtkinter as ctk

import config
from core.models.usuario import Usuario
from core.repositories.asistencia_repository_sqlite import AsistenciaRepositorySQLite
from core.repositories.audit_log_repository_sqlite import AuditLogRepositorySQLite
from core.repositories.cargo_repository_sqlite import CargoRepositorySQLite
from core.repositories.descarga_reporte_repository_sqlite import (
    DescargaReporteRepositorySQLite,
)
from core.repositories.departamento_repository_sqlite import (
    DepartamentoRepositorySQLite,
)
from core.repositories.dispositivo_repository import IDispositivoReadRepository
from core.repositories.dispositivo_repository_sqlite import DispositivoRepositorySQLite
from core.repositories.empleado_repository_sqlite import EmpleadoRepositorySQLite
from core.repositories.ausencia_repository_sqlite import AusenciaRepositorySQLite
from core.repositories.empleado_turno_repository_sqlite import (
    EmpleadoTurnoRepositorySQLite,
)
from core.repositories.feriado_repository_sqlite import FeriadoRepositorySQLite
from core.repositories.registro_raw_repository_sqlite import (
    RegistroRawRepositorySQLite,
)
from core.repositories.rol_repository import IRolReadRepository
from core.repositories.rol_repository_sqlite import RolRepositorySQLite
from core.repositories.sincronizacion_repository import (
    ISincronizacionReadRepository,
)
from core.repositories.sincronizacion_repository_sqlite import (
    SincronizacionRepositorySQLite,
)
from core.repositories.turno_repository_sqlite import TurnoRepositorySQLite
from core.repositories.usuario_repository_sqlite import UsuarioRepositorySQLite
from core.services.asistencia_service import AsistenciaService
from core.services.audit_logger import AuditLogger
from core.services.auth_service import AuthService
from core.services.catalogo_service import CatalogoService
from core.services.ausencia_service import AusenciaService
from core.services.consolidacion_service import ConsolidacionService
from core.services.contexto_reporte import ContextoReporte
from core.services.dispositivo_config_service import DispositivoConfigService
from core.services.empleado_service import EmpleadoService
from core.services.diagnostico_reloj_service import DiagnosticoRelojService
from core.services.feriado_service import FeriadoService
from core.services.instalacion_service import InstalacionService
from core.services.recepcion_push_service import RecepcionPushService
from core.services.password_policy import PasswordPolicy
from core.services.permission_service import PermissionService
from core.services.reporte_service import ReporteService
from core.services.session import Session
from core.services.setup_wizard_service import SetupWizardService
from core.services.sincronizacion_service import SincronizacionService
from core.services.turno_service import TurnoService
from core.services.usuario_actividad_service import UsuarioActividadService
from core.services.usuario_admin_service import UsuarioAdminService
from infrastructure import paths
from infrastructure.database.backup import run_daily_backup
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner
from infrastructure.exporters.xlsx_asistencia_exporter import XlsxAsistenciaExporter
from infrastructure.security.bcrypt_hasher import BcryptHasher
from infrastructure.zkteco.pyzk_adapter import PyzkAdapter
from infrastructure.zkteco.servidor_push import ServidorPush
from ui.async_util import run_async_ui
from ui.components.data_table import instalar_estilo_ttk
from ui.controllers.asistencia_controller import AsistenciaController
from ui.controllers.configuracion_controller import ConfiguracionController
from ui.controllers.empleados_controller import EmpleadosController
from ui.controllers.login_controller import LoginController
from ui.controllers.main_controller import MainController
from ui.controllers.reporte_controller import ReporteController
from ui.controllers.setup_controller import SetupController
from ui.controllers.sincronizacion_controller import SincronizacionController
from ui.controllers.ausencias_controller import AusenciasController
from ui.controllers.turnos_controller import TurnosController
from ui.controllers.usuarios_controller import UsuariosController
from ui.views.asistencia_view import AsistenciaView
from ui.views.configuracion_view import ConfiguracionView
from ui.views.empleados_view import EmpleadosView
from ui.views.login_window import LoginFrame
from ui.views.main_window import MainFrame, ViewFactory
from ui.views.reportes_view import ReportesView
from ui.views.setup_wizard_window import SetupWizardFrame
from ui.views.sincronizacion_view import SincronizacionView
from ui.views.turnos_view import TurnosView
from ui.views.usuarios_view import UsuariosView
from ui.views.welcome_view import ResumenSistema
from ui import rendimiento

# Segundos que espera "Probar conexión" antes de decir que el reloj no responde.
_TIMEOUT_PRUEBA_RELOJ = 5


class _Services:
    """Contenedor de servicios construidos una sola vez en el arranque.

    No es un "container" de DI; solo agrupa las instancias para que el
    router las pueda pasar cuando las necesite. Mantener esto como clase
    simple (no dict) permite que mypy --strict tipe cada campo.
    """

    def __init__(
        self,
        auth: AuthService,
        permission: PermissionService,
        setup: SetupWizardService,
        catalogo: CatalogoService,
        turno: TurnoService,
        empleado: EmpleadoService,
        sincronizacion: SincronizacionService,
        asistencia: AsistenciaService,
        reporte: ReporteService,
        dispositivo_config: DispositivoConfigService,
        usuario_admin: UsuarioAdminService,
        dispositivo_read: IDispositivoReadRepository,
        sincronizacion_read: ISincronizacionReadRepository,
        rol_read: IRolReadRepository,
        usuario_actividad: UsuarioActividadService,
        ausencia: AusenciaService,
        feriado: FeriadoService,
        diagnostico_reloj: DiagnosticoRelojService,
        recepcion_push: RecepcionPushService,
        servidor_push: ServidorPush,
    ) -> None:
        self.auth = auth
        self.permission = permission
        self.setup = setup
        self.catalogo = catalogo
        self.turno = turno
        self.empleado = empleado
        self.sincronizacion = sincronizacion
        self.asistencia = asistencia
        self.reporte = reporte
        self.dispositivo_config = dispositivo_config
        self.usuario_admin = usuario_admin
        self.dispositivo_read = dispositivo_read
        self.sincronizacion_read = sincronizacion_read
        self.rol_read = rol_read
        self.usuario_actividad = usuario_actividad
        self.ausencia = ausencia
        self.feriado = feriado
        self.diagnostico_reloj = diagnostico_reloj
        self.recepcion_push = recepcion_push
        self.servidor_push = servidor_push


def run() -> int:
    """Arranca la UI. Devuelve el exit code (0 = cierre normal).

    Returns:
        0 si la app cerró normalmente, 2 si falló la inicialización de BD.
    """
    log = logging.getLogger("ui.app")

    database = Database(config.DATABASE_PATH)
    if not _run_migrations(database, log):
        return 2

    # Instalación nueva: crea admin (Super Administrador) y administrador
    # (Administrador), los dos con 12345678, el turno Operativo, el
    # departamento Contenedor y el cargo Usuario. En una base que ya está
    # en uso no hace nada.
    _preparar_instalacion(database, log)

    # Sub-3.1 production-ready: snapshot diario de la BD ANTES de que el
    # operador toque nada. Si falla (disco lleno, permisos), loguea pero
    # no aborta — perder un backup ocasional es preferible a impedir el
    # arranque de la app.
    _crear_backup_diario(log)

    services = _build_services(database)

    # Recovery de syncs huérfanas antes de arrancar la UI (Decisión 1).
    # Silencioso si no hay pendientes; si las hay, el primer mount de la
    # vista de sincronización pinta un banner con el conteo.
    huerfanas_cerradas = _recover_huerfanas_al_arranque(services, log)
    _iniciar_recepcion_push(services, log)

    root = _crear_root()
    router = _Router(root, services, log, huerfanas_cerradas=huerfanas_cerradas)
    router.start()

    log.info("Entrando al mainloop de customtkinter.")
    root.mainloop()
    log.info("Mainloop finalizado. Saliendo.")
    return 0


# ── Helpers de arranque ──────────────────────────────────────────────────────


def _iniciar_recepcion_push(services: _Services, log: logging.Logger) -> None:
    """Si hay relojes en modo push, empieza a escucharlos desde ya.

    Corre aunque nadie haya iniciado sesión: las marcas llegan igual. Si el
    puerto está ocupado no se aborta; el aviso aparece en Configuración →
    Relojes.
    """
    try:
        hay_push = any(d.es_push for d in services.dispositivo_read.list_active())
    except sqlite3.Error:
        log.exception("No se pudo revisar si hay relojes push.")
        return
    if hay_push:
        services.servidor_push.iniciar()


def _preparar_instalacion(database: Database, log: logging.Logger) -> None:
    """Usuarios y turno por defecto de una instalación nueva.

    Si falla, la app sigue: sin usuarios se muestra el asistente de primer
    uso para crear el Super Administrador a mano.
    """
    try:
        usuarios = UsuarioRepositorySQLite(database)
        turnos = TurnoRepositorySQLite(database)
        departamentos = DepartamentoRepositorySQLite(database)
        cargos = CargoRepositorySQLite(database)
        resultado = InstalacionService(
            usuario_read=usuarios,
            usuario_write=usuarios,
            rol_read=RolRepositorySQLite(database),
            turno_read=turnos,
            turno_write=turnos,
            dep_read=departamentos,
            dep_write=departamentos,
            cargo_read=cargos,
            cargo_write=cargos,
            hasher=BcryptHasher(config.BCRYPT_COST_FACTOR),
            audit_logger=AuditLogger(AuditLogRepositorySQLite(database)),
        ).preparar()
    except Exception:  # noqa: BLE001 — no debe impedir abrir la app
        log.exception("No se pudo preparar la instalación nueva.")
        return
    if resultado.hizo_algo:
        log.info(
            "Instalación nueva preparada: usuarios=%s turno=%s departamento=%s cargo=%s",
            ", ".join(resultado.usuarios) or "—",
            resultado.turno or "—",
            resultado.departamento or "—",
            resultado.cargo or "—",
        )


def _run_migrations(database: Database, log: logging.Logger) -> bool:
    """Corre migraciones pendientes. Devuelve False ante error."""
    try:
        runner = MigrationsRunner(database, config.MIGRATIONS_DIR)
        applied = runner.run()
    except (OSError, sqlite3.Error):
        log.exception("No se pudo inicializar la base de datos.")
        return False
    if applied:
        log.info("Migraciones aplicadas: %s", ", ".join(applied))
    return True


def _crear_backup_diario(log: logging.Logger) -> None:
    """Crea snapshot del día de la BD + purga snapshots > 30 días.

    El error nunca se propaga: el backup es best-effort. Si falla, el
    operador todavía puede usar la app y la falla queda en el log para
    que mantenimiento la revise.
    """
    try:
        backup = run_daily_backup(
            db_path=config.DATABASE_PATH,
            backup_dir=paths.backups_dir(),
            max_age_days=config.BACKUP_MAX_AGE_DAYS,
        )
        if backup is not None:
            log.info("Backup diario creado: %s", backup)
        else:
            log.debug("Backup diario omitido (ya existía o no aplica).")
    except Exception:  # noqa: BLE001
        # Defensa de último recurso: cualquier excepción inesperada se
        # loguea pero NO debe abortar el arranque de la app.
        log.exception("Excepción inesperada en backup diario; se ignora.")


def _recover_huerfanas_al_arranque(
    services: _Services,
    log: logging.Logger,
) -> int:
    """Cierra syncs que quedaron ``EN_CURSO`` por un cierre previo.

    Se invoca una sola vez al arrancar (Decisión 1 aprobada). Nunca
    lanza: un fallo aquí no debe impedir arrancar la app — logueamos y
    devolvemos 0 para que el banner de aviso quede silencioso.
    """
    try:
        cantidad = services.sincronizacion.recover_huerfanas()
    except Exception:  # noqa: BLE001 — no impedimos arrancar la app
        log.exception("Error cerrando syncs huérfanas al arrancar.")
        return 0
    if cantidad:
        log.info("Se cerraron %s sync(s) huérfana(s) al arrancar.", cantidad)
    return cantidad


def _build_services(database: Database) -> _Services:
    """Instancia servicios con sus repos/hashers/loggers reales."""
    usuario_repo = UsuarioRepositorySQLite(database)
    rol_repo = RolRepositorySQLite(database)
    audit_repo = AuditLogRepositorySQLite(database)
    dep_repo = DepartamentoRepositorySQLite(database)
    cargo_repo = CargoRepositorySQLite(database)
    empleado_repo = EmpleadoRepositorySQLite(database)
    empleado_turno_repo = EmpleadoTurnoRepositorySQLite(database)
    turno_repo = TurnoRepositorySQLite(database)
    dispositivo_repo = DispositivoRepositorySQLite(database)
    sincronizacion_repo = SincronizacionRepositorySQLite(database)
    registro_raw_repo = RegistroRawRepositorySQLite(database)
    asistencia_repo = AsistenciaRepositorySQLite(database)
    feriado_repo = FeriadoRepositorySQLite(database)
    ausencia_repo = AusenciaRepositorySQLite(database)
    descarga_reporte_repo = DescargaReporteRepositorySQLite(database)

    hasher = BcryptHasher(config.BCRYPT_COST_FACTOR)
    audit_logger = AuditLogger(audit_repo)
    zkteco_adapter = PyzkAdapter()
    xlsx_exporter = XlsxAsistenciaExporter(
        organizacion=config.APP_VENDOR, sistema=f"{config.APP_TAGLINE} ({config.APP_NAME})"
    )

    password_policy = PasswordPolicy(
        min_length=config.MIN_PASSWORD_LENGTH,
        require_digit=config.PASSWORD_REQUIRE_DIGIT,
        require_letter=config.PASSWORD_REQUIRE_LETTER,
    )

    auth_service = AuthService(
        usuario_read=usuario_repo,
        usuario_write=usuario_repo,
        rol_read=rol_repo,
        hasher=hasher,
        audit_logger=audit_logger,
        max_failed_attempts=config.MAX_FAILED_LOGIN_ATTEMPTS,
        lockout_duration_minutes=config.LOCKOUT_DURATION_MINUTES,
    )

    setup_service = SetupWizardService(
        usuario_read=usuario_repo,
        usuario_write=usuario_repo,
        rol_read=rol_repo,
        hasher=hasher,
        audit_logger=audit_logger,
        password_policy=password_policy,
    )

    catalogo_service = CatalogoService(
        dep_read=dep_repo,
        dep_write=dep_repo,
        cargo_read=cargo_repo,
        cargo_write=cargo_repo,
        empleado_read=empleado_repo,
        audit_logger=audit_logger,
    )

    dispositivo_config_service = DispositivoConfigService(
        dispositivo_read=dispositivo_repo,
        dispositivo_write=dispositivo_repo,
        audit_logger=audit_logger,
    )

    usuario_admin_service = UsuarioAdminService(
        usuario_read=usuario_repo,
        usuario_write=usuario_repo,
        rol_read=rol_repo,
        hasher=hasher,
        password_policy=password_policy,
        audit_logger=audit_logger,
    )

    turno_service = TurnoService(
        turno_read=turno_repo,
        turno_write=turno_repo,
        audit_logger=audit_logger,
    )

    empleado_service = EmpleadoService(
        empleado_read=empleado_repo,
        empleado_write=empleado_repo,
        empleado_turno_read=empleado_turno_repo,
        empleado_turno_write=empleado_turno_repo,
        departamento_read=dep_repo,
        cargo_read=cargo_repo,
        turno_read=turno_repo,
        audit_logger=audit_logger,
    )

    consolidacion_service = ConsolidacionService(
        empleado_read=empleado_repo,
        empleado_turno_read=empleado_turno_repo,
        turno_read=turno_repo,
        feriado_read=feriado_repo,
        registro_raw_read=registro_raw_repo,
        asistencia_write=asistencia_repo,
        audit_logger=audit_logger,
        ausencia_read=ausencia_repo,
    )

    ausencia_service = AusenciaService(
        ausencia_read=ausencia_repo,
        ausencia_write=ausencia_repo,
        empleado_read=empleado_repo,
        empleado_turno_read=empleado_turno_repo,
        turno_read=turno_repo,
        feriado_read=feriado_repo,
        audit_logger=audit_logger,
        consolidacion_service=consolidacion_service,
    )

    sincronizacion_service = SincronizacionService(
        dispositivo_read=dispositivo_repo,
        sincronizacion_read=sincronizacion_repo,
        sincronizacion_write=sincronizacion_repo,
        registro_raw_write=registro_raw_repo,
        adapter=zkteco_adapter,
        audit_logger=audit_logger,
        consolidador=consolidacion_service,
    )

    asistencia_service = AsistenciaService(
        asistencia_read=asistencia_repo,
        asistencia_write=asistencia_repo,
        empleado_read=empleado_repo,
        turno_read=turno_repo,
        audit_logger=audit_logger,
        consolidacion_service=consolidacion_service,
    )

    reporte_service = ReporteService(
        asistencia_service=asistencia_service,
        descarga_read=descarga_reporte_repo,
        descarga_write=descarga_reporte_repo,
        exporter=xlsx_exporter,
        audit_logger=audit_logger,
        contexto=ContextoReporte(
            empleado_read=empleado_repo,
            departamento_read=dep_repo,
            cargo_read=cargo_repo,
            turno_read=turno_repo,
            registro_raw_read=registro_raw_repo,
            dispositivo_read=dispositivo_repo,
            ausencia_read=ausencia_repo,
            usuario_read=usuario_repo,
        ),
    )

    # Relojes que envían las marcas solos (push / ADMS).
    recepcion_push = RecepcionPushService(
        dispositivo_read=dispositivo_repo,
        dispositivo_write=dispositivo_repo,
        sincronizacion_write=sincronizacion_repo,
        registro_raw_write=registro_raw_repo,
        consolidador=consolidacion_service,
    )

    return _Services(
        auth=auth_service,
        permission=PermissionService(),
        setup=setup_service,
        catalogo=catalogo_service,
        turno=turno_service,
        empleado=empleado_service,
        sincronizacion=sincronizacion_service,
        asistencia=asistencia_service,
        reporte=reporte_service,
        dispositivo_config=dispositivo_config_service,
        usuario_admin=usuario_admin_service,
        dispositivo_read=dispositivo_repo,
        sincronizacion_read=sincronizacion_repo,
        rol_read=rol_repo,
        usuario_actividad=UsuarioActividadService(audit_repo),
        ausencia=ausencia_service,
        feriado=FeriadoService(
            feriado_read=feriado_repo,
            feriado_write=feriado_repo,
            audit_logger=audit_logger,
            consolidacion_service=consolidacion_service,
            asistencia_read=asistencia_repo,
        ),
        recepcion_push=recepcion_push,
        servidor_push=ServidorPush(recepcion_push, config.PUERTO_PUSH),
        # Timeout corto: "Probar conexión" debe contestar rápido si el reloj no está.
        diagnostico_reloj=DiagnosticoRelojService(
            adapter=PyzkAdapter(timeout_seconds=_TIMEOUT_PRUEBA_RELOJ),
            dispositivo_read=dispositivo_repo,
            empleado_read=empleado_repo,
            audit_logger=audit_logger,
        ),
    )


def _crear_root() -> ctk.CTk:
    """Crea la ventana raíz con tema y tamaño por defecto."""
    rendimiento.aplicar()
    ctk.set_appearance_mode("system")
    ctk.set_default_color_theme("blue")

    root = ctk.CTk()
    # Tablas y selectores de fecha (ttk) con la paleta de la app.
    instalar_estilo_ttk(root)
    root.title(f"{config.APP_NAME} — {config.APP_TAGLINE}")
    root.geometry("1000x620")
    root.minsize(880, 560)
    return root


# ── Router: transiciones entre wizard → login → main ────────────────────────


class _Router:
    """Gestiona las transiciones entre los tres frames principales.

    Mantiene referencia al frame actualmente montado para destruirlo
    antes de montar el siguiente. No guarda ``Session`` persistente: la
    sesión vive únicamente en el ``MainFrame`` activo.
    """

    def __init__(
        self,
        root: ctk.CTk,
        services: _Services,
        log: logging.Logger,
        huerfanas_cerradas: int = 0,
    ) -> None:
        self._root = root
        self._services = services
        self._log = log
        self._current_frame: Optional[ctk.CTkFrame] = None
        # Cantidad de syncs huérfanas cerradas al arrancar; el primer mount
        # de SincronizacionView la consume como banner de aviso y la
        # limpia. Un logout/login posterior no re-dispara el aviso.
        self._huerfanas_cerradas = huerfanas_cerradas

    def start(self) -> None:
        """Decide qué frame mostrar al arrancar la app."""
        if self._services.setup.is_first_run():
            self._log.info("First run detectado — mostrando Setup Wizard.")
            self._mostrar_wizard()
        else:
            self._log.info("First run ya completado — mostrando Login.")
            self._mostrar_login()

    # ── Transiciones ──────────────────────────────────────────────────────

    def _swap(self, nuevo: ctk.CTkFrame) -> None:
        """Destruye el frame actual y monta el nuevo ocupando toda la raíz."""
        if self._current_frame is not None:
            self._current_frame.destroy()
        nuevo.pack(expand=True, fill="both")
        self._current_frame = nuevo

    def _mostrar_wizard(self) -> None:
        setup_controller = SetupController(self._services.setup)
        frame = SetupWizardFrame(
            self._root,
            controller=setup_controller,
            on_success=self._on_wizard_ok,
        )
        self._swap(frame)

    def _mostrar_login(self) -> None:
        # El login_controller recibe un async_runner placeholder; la view
        # lo sustituye por uno ligado a su propio widget antes de disparar
        # el trabajo — así mantenemos al controller agnóstico de Tk.
        placeholder_runner: Callable[..., Any] = lambda work, ok, err: None  # noqa: E731
        login_controller = LoginController(self._services.auth, placeholder_runner)
        frame = LoginFrame(
            self._root,
            controller=login_controller,
            on_success=self._on_login_ok,
        )
        self._swap(frame)

    def _build_initial_zkteco_status(self) -> str:
        """Lee la última sincronización OK de BD para el status bar.

        Razón (Sub-2.7c): cuando el usuario hace logout/login, el
        ``MainFrame`` se reconstruye desde cero y el estado
        "última sync HH:MM" que dejó el callback de SincronizacionView
        se perdía. Acá lo recuperamos consultando BD: si hay una sync
        OK reciente, mostramos su timestamp; si no, "sin conexión".

        Returns:
            "sin conexión" / "última sync HH:MM" (si es de hoy) /
            "última sync DD/MM HH:MM" (si es de otro día).
        """
        from datetime import datetime  # local — evita import top-level

        try:
            recientes = self._services.sincronizacion_read.list_recientes(limit=5)
        except Exception:  # noqa: BLE001 — fallback silencioso
            self._log.exception("Error consultando ultima sync para status bar")
            return "sin conexión"
        for s in recientes:
            if s.estado != "OK" or not s.fin:
                continue
            try:
                dt_utc = datetime.fromisoformat(s.fin)
                dt_local = dt_utc.astimezone()
            except Exception:  # noqa: BLE001
                continue
            hoy_local = datetime.now().astimezone().date()
            if dt_local.date() == hoy_local:
                return f"última sync {dt_local.strftime('%H:%M')}"
            return f"última sync {dt_local.strftime('%d/%m %H:%M')}"
        return "sin conexión"

    def _build_resumen_sistema(self) -> ResumenSistema:
        """Sub-3.4: arma el ``ResumenSistema`` que la WelcomeView muestra.

        Best-effort: si alguna consulta falla, el campo correspondiente
        cae a un valor neutro y se loguea — la pantalla de bienvenida
        nunca debe romper el login.
        """
        try:
            empleados_activos = len(self._services.empleado.list_empleados(solo_activos=True))
        except Exception:  # noqa: BLE001
            self._log.exception("ResumenSistema: error contando empleados.")
            empleados_activos = 0

        try:
            dispositivos_activos = len(self._services.dispositivo_read.list_active())
        except Exception:  # noqa: BLE001
            self._log.exception("ResumenSistema: error contando dispositivos.")
            dispositivos_activos = 0

        sync_label = self._build_initial_zkteco_status()
        # Reusamos el helper que ya formatea "última sync HH:MM";
        # cuando no hay sync, lo dejamos como "sin sincronización" para
        # que se lea más natural en la card de bienvenida.
        if sync_label == "sin conexión":
            sync_label = "sin sincronización"

        return ResumenSistema(
            empleados_activos=empleados_activos,
            dispositivos_activos=dispositivos_activos,
            ultima_sync_label=sync_label,
            app_version=config.APP_VERSION,
        )

    def _mostrar_main(self, session: Session) -> None:
        # Creamos el MainFrame primero porque el controller necesita su
        # método ``open_view`` como callback. Inyectamos el controller
        # después llamando a un setter; mantenemos la inyección por
        # constructor cableando con un closure pre-hecho.
        main_frame_holder: dict[str, MainFrame] = {}

        def open_view_callback(code: str) -> None:
            main_frame_holder["frame"].open_view(code)

        controller = MainController(
            session=session,
            permission_service=self._services.permission,
            open_view=open_view_callback,
        )

        # Factories de vistas reales de Fase 2. Cada code ausente del
        # dict cae al PlaceholderView genérico del MainFrame.
        view_factories: dict[str, ViewFactory] = {
            "users": self._build_usuarios_factory(session),
            "settings": self._build_configuracion_factory(session),
            "shifts": self._build_turnos_factory(session),
            "employees": self._build_empleados_factory(session),
            "zkteco_sync": self._build_sincronizacion_factory(session, main_frame_holder),
            "attendance": self._build_asistencia_factory(session),
            "reports": self._build_reportes_factory(session),
        }

        frame = MainFrame(
            self._root,
            session=session,
            controller=controller,
            on_logout=self._on_logout,
            view_factories=view_factories,
            initial_zkteco_status=self._build_initial_zkteco_status(),
            resumen_sistema=self._build_resumen_sistema(),
            precargar=True,
        )
        main_frame_holder["frame"] = frame

        # Reemplazamos el async_runner del login: ya no aplica. Tras login,
        # el futuro despacho asíncrono lo gestionará cada controller con
        # el widget actual.
        self._swap(frame)

    def _build_usuarios_factory(self, session: Session) -> ViewFactory:
        """Devuelve una factory que construye la vista de Usuarios y roles.

        Mismo patrón que las demás factories. El controller se instancia
        una vez con la sesión; la factory puede invocarse múltiples veces
        y devuelve una vista nueva cada vez (reusando el mismo controller).
        """
        usuarios_controller = UsuariosController(
            session=session,
            permission_service=self._services.permission,
            usuario_admin_service=self._services.usuario_admin,
            rol_read=self._services.rol_read,
            actividad_service=self._services.usuario_actividad,
        )

        def factory(parent: ctk.CTkBaseClass) -> ctk.CTkBaseClass:
            return UsuariosView(parent, controller=usuarios_controller)

        return factory

    def _build_configuracion_factory(self, session: Session) -> ViewFactory:
        """Devuelve una factory que construye la vista de Configuración.

        El controller se instancia una sola vez con la sesión activa; la
        factory se puede invocar múltiples veces (cada vez que el usuario
        entra al módulo) y construye una vista nueva con el mismo
        controller.
        """
        configuracion_controller = ConfiguracionController(
            session=session,
            permission_service=self._services.permission,
            catalogo_service=self._services.catalogo,
            dispositivo_service=self._services.dispositivo_config,
            empleado_service=self._services.empleado,
            sincronizacion_read=self._services.sincronizacion_read,
            feriado_service=self._services.feriado,
            diagnostico_reloj=self._services.diagnostico_reloj,
            recepcion_push=self._services.recepcion_push,
            servidor_push=self._services.servidor_push,
        )

        def factory(parent: ctk.CTkBaseClass) -> ctk.CTkBaseClass:
            return ConfiguracionView(parent, controller=configuracion_controller)

        return factory

    def _build_turnos_factory(self, session: Session) -> ViewFactory:
        """Devuelve una factory que construye la vista de Turnos.

        Mismo patrón que ``_build_configuracion_factory``: el controller
        se instancia una sola vez con la sesión; la factory puede
        invocarse múltiples veces y devuelve una vista nueva cada vez.
        """
        turnos_controller = TurnosController(
            session=session,
            permission_service=self._services.permission,
            turno_service=self._services.turno,
            empleado_service=self._services.empleado,
        )
        ausencias_controller = self._ausencias_controller(session)

        def factory(parent: ctk.CTkBaseClass) -> ctk.CTkBaseClass:
            return TurnosView(parent, controller=turnos_controller, ausencias=ausencias_controller)

        return factory

    def _build_empleados_factory(self, session: Session) -> ViewFactory:
        """Devuelve una factory que construye la vista de Empleados.

        Inyecta 3 servicios en el controller (empleado + catálogo +
        turno): todos fueron construidos una sola vez en el composition
        root y se comparten entre vistas.
        """
        empleados_controller = EmpleadosController(
            session=session,
            permission_service=self._services.permission,
            empleado_service=self._services.empleado,
            catalogo_service=self._services.catalogo,
            turno_service=self._services.turno,
        )
        ausencias_controller = self._ausencias_controller(session)

        def factory(parent: ctk.CTkBaseClass) -> ctk.CTkBaseClass:
            return EmpleadosView(
                parent, controller=empleados_controller, ausencias=ausencias_controller
            )

        return factory

    def _ausencias_controller(self, session: Session) -> AusenciasController:
        """Controller de vacaciones y permisos (lo comparten Turnos y Empleados)."""
        return AusenciasController(
            session=session,
            permission_service=self._services.permission,
            ausencia_service=self._services.ausencia,
            empleado_service=self._services.empleado,
        )

    def _build_sincronizacion_factory(
        self,
        session: Session,
        main_frame_holder: dict[str, MainFrame],
    ) -> ViewFactory:
        """Devuelve una factory que construye la vista de Sincronización ZKTeco.

        El controller se instancia una sola vez con la sesión activa. Si
        hay syncs huérfanas cerradas al arrancar, las inyectamos como
        ``aviso_recuperacion`` — la primera construcción de la vista las
        consume y limpia el atributo, así un logout/login no re-dispara
        el aviso.

        Args:
            session: Sesión activa del usuario logueado.
            main_frame_holder: Mismo dict mutable que se usa para
                ``open_view_callback`` — permite resolver el ``MainFrame``
                de manera diferida cuando el callback de sync OK se
                dispara (la vista se construye después del MainFrame,
                pero la factory se arma antes — necesitamos el holder).
        """
        sincronizacion_controller = SincronizacionController(
            session=session,
            permission_service=self._services.permission,
            sincronizacion_service=self._services.sincronizacion,
            dispositivo_read=self._services.dispositivo_read,
            sincronizacion_read=self._services.sincronizacion_read,
        )
        if self._huerfanas_cerradas > 0:
            sincronizacion_controller.aviso_recuperacion = self._huerfanas_cerradas
            # Solo se inyecta una vez: el banner se muestra al primer mount.
            self._huerfanas_cerradas = 0

        def on_sync_completada(_resultado: Any) -> None:
            """Refresca la status bar global tras un sync exitoso.

            El texto "última sync HH:MM" usa la hora local del sistema.
            Lo decide acá (composition root) en vez de la vista para que
            la vista quede agnóstica del formato del label.
            """
            from datetime import datetime  # local — evita import top-level

            frame = main_frame_holder.get("frame")
            if frame is None:
                return  # MainFrame aún no montado (no debería ocurrir)
            etiqueta = f"última sync {datetime.now().strftime('%H:%M')}"
            frame.refresh_status_zkteco(etiqueta)

        def factory(parent: ctk.CTkBaseClass) -> ctk.CTkBaseClass:
            return SincronizacionView(
                parent,
                controller=sincronizacion_controller,
                on_sync_completada=on_sync_completada,
            )

        return factory

    def _build_asistencia_factory(self, session: Session) -> ViewFactory:
        """Devuelve una factory que construye la vista de Asistencia.

        Mismo patrón que las demás factories: el controller se instancia
        una sola vez con la sesión activa; la factory puede invocarse
        múltiples veces y devuelve una vista nueva cada vez (reusando el
        mismo controller).
        """
        asistencia_controller = AsistenciaController(
            session=session,
            permission_service=self._services.permission,
            asistencia_service=self._services.asistencia,
            turno_service=self._services.turno,
        )
        ausencias_controller = self._ausencias_controller(session)

        def factory(parent: ctk.CTkBaseClass) -> ctk.CTkBaseClass:
            return AsistenciaView(
                parent, controller=asistencia_controller, ausencias=ausencias_controller
            )

        return factory

    def _build_reportes_factory(self, session: Session) -> ViewFactory:
        """Devuelve una factory que construye la vista de Reportes.

        Misma forma que las demás factories — el ``ReporteController`` se
        instancia una sola vez con la sesión y se reusa en cada montaje
        de la vista. La vista usa pestañas internas para "Exportar" e
        "Historial de descargas"; el tab de Historial se renderiza solo
        si la sesión tiene ``VIEW_EXPORT_HISTORY``.
        """
        reportes_controller = ReporteController(
            session=session,
            permission_service=self._services.permission,
            reporte_service=self._services.reporte,
        )

        def factory(parent: ctk.CTkBaseClass) -> ctk.CTkBaseClass:
            return ReportesView(parent, controller=reportes_controller)

        return factory

    # ── Callbacks de cada frame ───────────────────────────────────────────

    def _on_wizard_ok(self, _usuario: Usuario) -> None:
        """Tras crear el SUPERADMIN, vamos al login."""
        self._mostrar_login()

    def _on_login_ok(self, session: Session) -> None:
        """Tras login válido, montamos la ventana principal."""
        # Aquí reemplazamos el async_runner que usa el login: el flujo
        # pasó — de aquí en adelante la sesión vive en el MainFrame.
        self._mostrar_main(session)

    def _on_logout(self) -> None:
        """El usuario cerró sesión — volvemos al login."""
        self._mostrar_login()


# Re-exportamos run_async_ui aquí simbólicamente, para dejar explícito en
# el composition root que existe — aunque hoy solo lo use LoginFrame.
__all__ = ["run", "run_async_ui"]
