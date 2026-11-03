"""Controller de la vista de configuración maestra (Sub-2.4 + Sub-2.4b).

Orquesta las llamadas al ``CatalogoService`` (departamentos + cargos) y
al ``DispositivoConfigService`` (Plan B / Sub-2.4b: catálogo de
dispositivos ZKTeco). Cada método público está decorado con
``@require_permission(MANAGE_SETTINGS)`` — si un botón aparece en la UI
por un bug de ``filter_visible``, el click terminará en
``PermissionDeniedError`` antes de tocar el servicio.

El controller NO conoce Tk/customtkinter. Expone métodos sincrónicos que
devuelven modelos de dominio o None; la vista decide cómo renderizarlos.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from core.models import permissions as perms
from core.models.cargo import Cargo
from core.models.departamento import Departamento
from core.models.dispositivo import MODO_DIRECTO, Dispositivo
from core.models.empleado import Empleado
from core.models.feriado import Feriado
from core.models.sincronizacion import Sincronizacion
from core.repositories.sincronizacion_repository import ISincronizacionReadRepository
from core.services.catalogo_service import CatalogoService, ResultadoMovimiento
from core.services.diagnostico_reloj_service import DiagnosticoReloj, DiagnosticoRelojService
from core.services.dispositivo_config_service import DispositivoConfigService
from core.services.empleado_service import EmpleadoService
from core.services.feriado_service import FeriadoService
from core.services.permission_service import PermissionService, require_permission
from core.services.recepcion_push_service import RecepcionPushService, RelojDetectado
from core.services.session import Session
from infrastructure.zkteco.servidor_push import ServidorPush, direcciones_de_esta_pc


@dataclass(frozen=True)
class EstadoPush:
    """Si la app está escuchando a los relojes push y cómo configurarlos."""

    disponible: bool  # la app tiene el receptor
    activo: bool
    puerto: int
    error: Optional[str]
    direcciones: List[str]  # IPs de esta computadora para poner en el reloj


class ConfiguracionController:
    """Controller de Configuración (departamentos + cargos + dispositivos)."""

    def __init__(
        self,
        session: Session,
        permission_service: PermissionService,
        catalogo_service: CatalogoService,
        dispositivo_service: DispositivoConfigService,
        empleado_service: Optional[EmpleadoService] = None,
        sincronizacion_read: Optional[ISincronizacionReadRepository] = None,
        feriado_service: Optional[FeriadoService] = None,
        diagnostico_reloj: Optional[DiagnosticoRelojService] = None,
        recepcion_push: Optional[RecepcionPushService] = None,
        servidor_push: Optional[ServidorPush] = None,
    ) -> None:
        """Inicializa el controller.

        Args:
            session: Sesión activa del usuario. El decorador
                ``@require_permission`` la lee desde ``self.session``.
            permission_service: Verificador de permisos usado por el
                decorador (lee desde ``self.permission_service``).
            catalogo_service: Servicio con el CRUD de departamentos y
                cargos.
            dispositivo_service: Servicio con el CRUD del catálogo de
                dispositivos ZKTeco (Sub-2.4b).
            empleado_service: Para contar empleados por departamento y
                cargo (opcional; sin él los conteos salen en cero).
            sincronizacion_read: Para mostrar la última sincronización de
                cada reloj (opcional).
            feriado_service: CRUD de feriados (opcional; sin él la
                pestaña de feriados queda vacía).
            diagnostico_reloj: "Probar conexión" y "Poner en hora" de
                los relojes (opcional).
            recepcion_push: Relojes que envían las marcas solos (opcional).
            servidor_push: El servidor HTTP que los escucha (opcional).
        """
        # Nombres obligatorios para que ``@require_permission`` funcione.
        self.session = session
        self.permission_service = permission_service
        self._catalogo = catalogo_service
        self._dispositivo = dispositivo_service
        self._empleado = empleado_service
        self._sync_read = sincronizacion_read
        self._feriado = feriado_service
        self._diagnostico = diagnostico_reloj
        self._recepcion = recepcion_push
        self._servidor = servidor_push
        self._log = logging.getLogger(self.__class__.__name__)

    # ── Departamentos ─────────────────────────────────────────────────────

    @require_permission(perms.MANAGE_SETTINGS)
    def list_departamentos(self, solo_activos: bool = True) -> List[Departamento]:
        """Devuelve la lista de departamentos (activos por default)."""
        return self._catalogo.list_departamentos(solo_activos=solo_activos)

    @require_permission(perms.MANAGE_SETTINGS)
    def create_departamento(self, nombre: str) -> Departamento:
        """Crea un departamento nuevo y lo devuelve con su id asignado."""
        return self._catalogo.create_departamento(nombre, self.session.user_id)

    @require_permission(perms.MANAGE_SETTINGS)
    def rename_departamento(self, departamento_id: int, nuevo_nombre: str) -> None:
        """Renombra un departamento existente."""
        self._catalogo.rename_departamento(departamento_id, nuevo_nombre, self.session.user_id)

    @require_permission(perms.MANAGE_SETTINGS)
    def archive_departamento(self, departamento_id: int) -> None:
        """Archiva un departamento (falla si tiene empleados activos)."""
        self._catalogo.archive_departamento(departamento_id, self.session.user_id)

    @require_permission(perms.MANAGE_SETTINGS)
    def unarchive_departamento(self, departamento_id: int) -> None:
        """Reactiva un departamento archivado."""
        self._catalogo.unarchive_departamento(departamento_id, self.session.user_id)

    @require_permission(perms.MANAGE_SETTINGS)
    def archive_departamento_moviendo(
        self, departamento_id: int, destino_id: int
    ) -> ResultadoMovimiento:
        """Pasa empleados activos y cargos propios a ``destino_id`` y archiva."""
        return self._catalogo.archive_departamento_moviendo(
            departamento_id, destino_id, self.session.user_id
        )

    # ── Cargos ────────────────────────────────────────────────────────────

    @require_permission(perms.MANAGE_SETTINGS)
    def list_cargos(self, solo_activos: bool = True) -> List[Cargo]:
        """Devuelve la lista de cargos (activos por default)."""
        return self._catalogo.list_cargos(solo_activos=solo_activos)

    @require_permission(perms.MANAGE_SETTINGS)
    def create_cargo(
        self,
        nombre: str,
        departamento_id: Optional[int] = None,
    ) -> Cargo:
        """Crea un cargo nuevo y lo devuelve con su id asignado.

        Sub-3.2.A: ``departamento_id`` opcional restringe el cargo a un
        departamento concreto. Sin él (o con ``None``), el cargo es global.
        """
        return self._catalogo.create_cargo(
            nombre, self.session.user_id, departamento_id=departamento_id
        )

    @require_permission(perms.MANAGE_SETTINGS)
    def rename_cargo(self, cargo_id: int, nuevo_nombre: str) -> None:
        """Renombra un cargo existente."""
        self._catalogo.rename_cargo(cargo_id, nuevo_nombre, self.session.user_id)

    @require_permission(perms.MANAGE_SETTINGS)
    def set_cargo_departamento(self, cargo_id: int, departamento_id: Optional[int]) -> None:
        """Sub-3.2.A: cambia (o desasigna con ``None``) el departamento del cargo."""
        self._catalogo.set_cargo_departamento(cargo_id, departamento_id, self.session.user_id)

    @require_permission(perms.MANAGE_SETTINGS)
    def list_cargos_para_departamento(self, departamento_id: int) -> List[Cargo]:
        """Sub-3.2.A: cargos disponibles para un depto (globales + específicos)."""
        return self._catalogo.list_cargos_para_departamento(departamento_id)

    @require_permission(perms.MANAGE_SETTINGS)
    def archive_cargo(self, cargo_id: int) -> None:
        """Archiva un cargo (falla si tiene empleados activos)."""
        self._catalogo.archive_cargo(cargo_id, self.session.user_id)

    @require_permission(perms.MANAGE_SETTINGS)
    def cargos_destino_para(self, cargo_id: int) -> List[Cargo]:
        """Cargos a los que pueden pasar todos los empleados de ``cargo_id``."""
        return self._catalogo.cargos_destino_para(cargo_id)

    @require_permission(perms.MANAGE_SETTINGS)
    def archive_cargo_moviendo(self, cargo_id: int, destino_id: int) -> ResultadoMovimiento:
        """Pasa los empleados activos a ``destino_id`` y archiva el cargo."""
        return self._catalogo.archive_cargo_moviendo(cargo_id, destino_id, self.session.user_id)

    @require_permission(perms.MANAGE_SETTINGS)
    def unarchive_cargo(self, cargo_id: int) -> None:
        """Reactiva un cargo archivado."""
        self._catalogo.unarchive_cargo(cargo_id, self.session.user_id)

    # ── Dispositivos ZKTeco (Sub-2.4b / Plan B) ───────────────────────────

    @require_permission(perms.MANAGE_SETTINGS)
    def list_dispositivos(self, solo_activos: bool = True) -> List[Dispositivo]:
        """Devuelve la lista de dispositivos (activos por default)."""
        return self._dispositivo.list_dispositivos(solo_activos=solo_activos)

    @require_permission(perms.MANAGE_SETTINGS)
    def create_dispositivo(
        self,
        nombre: str,
        ip: str,
        puerto: int,
        modo: str = MODO_DIRECTO,
        clave_comunicacion: int = 0,
        usar_udp: bool = False,
        numero_serie: Optional[str] = None,
    ) -> Dispositivo:
        """Crea un dispositivo nuevo y lo devuelve con su id asignado."""
        return self._dispositivo.create_dispositivo(
            nombre=nombre,
            ip=ip,
            puerto=puerto,
            actor_user_id=self.session.user_id,
            modo=modo,
            clave_comunicacion=clave_comunicacion,
            usar_udp=usar_udp,
            numero_serie=numero_serie,
        )

    @require_permission(perms.MANAGE_SETTINGS)
    def update_dispositivo(
        self,
        dispositivo_id: int,
        nombre: str,
        ip: str,
        puerto: int,
        modo: Optional[str] = None,
        clave_comunicacion: Optional[int] = None,
        usar_udp: Optional[bool] = None,
        numero_serie: Optional[str] = None,
    ) -> None:
        """Actualiza los datos de conexión de un dispositivo existente."""
        self._dispositivo.update_dispositivo(
            dispositivo_id=dispositivo_id,
            nombre=nombre,
            ip=ip,
            puerto=puerto,
            actor_user_id=self.session.user_id,
            modo=modo,
            clave_comunicacion=clave_comunicacion,
            usar_udp=usar_udp,
            numero_serie=numero_serie,
        )

    @require_permission(perms.MANAGE_SETTINGS)
    def archive_dispositivo(self, dispositivo_id: int) -> None:
        """Archiva un dispositivo (soft-delete; preserva historial)."""
        self._dispositivo.archive_dispositivo(dispositivo_id, self.session.user_id)

    @require_permission(perms.MANAGE_SETTINGS)
    def unarchive_dispositivo(self, dispositivo_id: int) -> None:
        """Reactiva un dispositivo archivado."""
        self._dispositivo.unarchive_dispositivo(dispositivo_id, self.session.user_id)

    @require_permission(perms.MANAGE_SETTINGS)
    def ultimas_sincronizaciones(self) -> Dict[int, Sincronizacion]:
        """``{dispositivo_id: su sincronización más reciente}``."""
        if self._sync_read is None:
            return {}
        ultimas: Dict[int, Sincronizacion] = {}
        for s in self._sync_read.list_recientes(limit=500):
            ultimas.setdefault(s.dispositivo_id, s)
        return ultimas

    @require_permission(perms.MANAGE_SETTINGS)
    def probar_reloj(self, dispositivo_id: int) -> DiagnosticoReloj:
        """Conecta con el reloj y devuelve su diagnóstico (puede tardar segundos)."""
        return self._diagnosticos().probar(dispositivo_id)

    @require_permission(perms.MANAGE_SETTINGS)
    def poner_reloj_en_hora(self, dispositivo_id: int) -> datetime:
        """Pone el reloj con la hora de esta computadora."""
        return self._diagnosticos().poner_en_hora(dispositivo_id, self.session.user_id)

    # ── Relojes push ──────────────────────────────────────────────────────

    def estado_push(self) -> EstadoPush:
        """Estado del receptor de relojes push (para el panel de Relojes)."""
        if self._servidor is None:
            return EstadoPush(False, False, 0, None, [])
        return EstadoPush(
            disponible=True,
            activo=self._servidor.activo,
            puerto=self._servidor.puerto,
            error=self._servidor.error,
            direcciones=direcciones_de_esta_pc(),
        )

    @require_permission(perms.MANAGE_SETTINGS)
    def asegurar_recepcion_push(self) -> EstadoPush:
        """Empieza a escuchar a los relojes push si todavía no lo hacía."""
        if self._servidor is not None:
            self._servidor.iniciar()
        return self.estado_push()

    @require_permission(perms.MANAGE_SETTINGS)
    def relojes_detectados(self) -> List[RelojDetectado]:
        """Relojes que intentaron enviar marcas sin estar registrados."""
        return self._recepcion.detectados() if self._recepcion is not None else []

    @property
    def puede_probar_relojes(self) -> bool:
        return self._diagnostico is not None

    def _diagnosticos(self) -> DiagnosticoRelojService:
        if self._diagnostico is None:
            raise RuntimeError("La prueba de relojes no está disponible.")
        return self._diagnostico

    # ── Conteos para las tablas ───────────────────────────────────────────

    @require_permission(perms.MANAGE_SETTINGS)
    def conteo_empleados(self) -> Tuple[Dict[int, int], Dict[int, int]]:
        """Empleados ACTIVOS por departamento y por cargo."""
        por_depto: Dict[int, int] = {}
        por_cargo: Dict[int, int] = {}
        if self._empleado is None:
            return por_depto, por_cargo
        for e in self._empleado.list_empleados(solo_activos=True):
            por_depto[e.departamento_id] = por_depto.get(e.departamento_id, 0) + 1
            por_cargo[e.cargo_id] = por_cargo.get(e.cargo_id, 0) + 1
        return por_depto, por_cargo

    @require_permission(perms.MANAGE_SETTINGS)
    def empleados_activos(self) -> List[Empleado]:
        """Empleados activos (para mostrar quién está en cada departamento y cargo)."""
        if self._empleado is None:
            return []
        return self._empleado.list_empleados(solo_activos=True)

    # ── Feriados ──────────────────────────────────────────────────────────

    @require_permission(perms.MANAGE_SETTINGS)
    def list_feriados(self, anio: Optional[int] = None) -> List[Feriado]:
        return self._feriado.listar(anio) if self._feriado is not None else []

    @require_permission(perms.MANAGE_SETTINGS)
    def create_feriado(self, fecha: str, descripcion: str) -> Feriado:
        return self._feriados().crear(fecha, descripcion, self.session.user_id)

    @require_permission(perms.MANAGE_SETTINGS)
    def update_feriado(self, feriado_id: int, fecha: str, descripcion: str) -> Feriado:
        return self._feriados().actualizar(feriado_id, fecha, descripcion, self.session.user_id)

    @require_permission(perms.MANAGE_SETTINGS)
    def cargar_feriados_nacionales(self, anio: int) -> List[Feriado]:
        return self._feriados().cargar_nacionales(anio, self.session.user_id)

    @require_permission(perms.MANAGE_SETTINGS)
    def delete_feriado(self, feriado_id: int) -> None:
        self._feriados().eliminar(feriado_id, self.session.user_id)

    def _feriados(self) -> FeriadoService:
        if self._feriado is None:
            raise RuntimeError("El módulo de feriados no está disponible.")
        return self._feriado
