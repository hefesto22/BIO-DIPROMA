"""Controller de la vista de Empleados (Sub-2.6).

Orquesta las llamadas al ``EmpleadoService`` + ``CatalogoService`` +
``TurnoService`` para el CRUD de empleados. Cada método público está
decorado con ``@require_permission(MANAGE_EMPLOYEES)`` — mismo permiso
que usa el botón ``employees`` del sidebar (ver
``MainController.open_employees``).

Responsabilidad clave (Opción A' aprobada en Sub-2.6):
    El alta de un empleado incluye su turno inicial en un solo paso de
    UX. Como el backend expone ``create_empleado`` y ``asignar_turno``
    como operaciones separadas, el controller las orquesta en
    ``create_empleado_con_turno``:
        1. Valida que el turno exista y esté activo ANTES de crear.
        2. Crea el empleado.
        3. Asigna el turno inicial.
        4. Si el paso 3 falla, devuelve el empleado ya creado con un
           ``warning`` descriptivo — la vista compensa mostrando un
           mensaje y permitiendo completar la asignación desde la fila.

El controller NO conoce Tk/customtkinter. Expone métodos sincrónicos
que devuelven modelos de dominio; la vista decide cómo renderizarlos.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from core.models import permissions as perms
from core.models.cargo import Cargo
from core.models.departamento import Departamento
from core.models.empleado import Empleado
from core.models.empleado_turno import EmpleadoTurno
from core.models.turno import Turno
from core.services.catalogo_service import CatalogoService
from core.services.empleado_service import EmpleadoService
from core.services.errors import TurnoInactiveError
from core.services.permission_service import PermissionService, require_permission
from core.services.session import Session
from core.services.turno_service import TurnoService


@dataclass
class CreateEmpleadoResult:
    """Resultado del alta atómica de empleado + asignación inicial.

    ``empleado`` siempre está presente — si la creación falla, el
    controller propaga la excepción antes de construir este objeto.
    ``warning`` solo se llena si la asignación de turno falló DESPUÉS
    de que el empleado ya quedó persistido; la vista lo muestra como
    aviso al usuario para que complete la asignación manualmente.
    """

    empleado: Empleado
    warning: Optional[str] = None


class EmpleadosController:
    """Controller de la vista de Empleados."""

    def __init__(
        self,
        session: Session,
        permission_service: PermissionService,
        empleado_service: EmpleadoService,
        catalogo_service: CatalogoService,
        turno_service: TurnoService,
    ) -> None:
        """Inicializa el controller.

        Args:
            session: Sesión activa del usuario. El decorador
                ``@require_permission`` la lee desde ``self.session``.
            permission_service: Verificador de permisos usado por el
                decorador (lee desde ``self.permission_service``).
            empleado_service: Servicio con el CRUD de empleados y la
                gestión de asignaciones de turno.
            catalogo_service: Servicio para popular los combos de
                departamento y cargo en los diálogos.
            turno_service: Servicio para popular el combo de turno en
                el diálogo de alta y en el de asignación.
        """
        # Nombres obligatorios para que ``@require_permission`` funcione.
        self.session = session
        self.permission_service = permission_service
        self._empleado = empleado_service
        self._catalogo = catalogo_service
        self._turno = turno_service
        self._log = logging.getLogger(self.__class__.__name__)

    # ── Reads: listados ───────────────────────────────────────────────────

    @require_permission(perms.MANAGE_EMPLOYEES)
    def mapa_turnos_vigentes(self) -> Dict[int, List[EmpleadoTurno]]:
        """``{empleado_id: [asignaciones vigentes]}`` de todos los empleados."""
        ids = [t.id for t in self._turno.list_turnos(solo_activos=False) if t.id is not None]
        return self._empleado.turnos_vigentes_por_empleado(ids)

    @require_permission(perms.MANAGE_EMPLOYEES)
    def list_empleados(self, solo_activos: bool = True) -> List[Empleado]:
        """Devuelve la lista de empleados (activos por default)."""
        return self._empleado.list_empleados(solo_activos=solo_activos)

    @require_permission(perms.MANAGE_EMPLOYEES)
    def get_empleado(self, empleado_id: int) -> Empleado:
        """Devuelve un empleado por id.

        Raises:
            EmpleadoNotFoundError: Si no existe.
        """
        return self._empleado.get_empleado(empleado_id)

    @require_permission(perms.MANAGE_EMPLOYEES)
    def get_turno_vigente(self, empleado_id: int) -> Optional[EmpleadoTurno]:
        """Devuelve la asignación de turno vigente, o ``None``."""
        return self._empleado.get_turno_vigente(empleado_id)

    @require_permission(perms.MANAGE_EMPLOYEES)
    def list_turnos_vigentes(self, empleado_id: int) -> List[EmpleadoTurno]:
        """Sub-3.3: todas las asignaciones vigentes del empleado."""
        return self._empleado.list_turnos_vigentes(empleado_id)

    @require_permission(perms.MANAGE_EMPLOYEES)
    def cerrar_asignacion(self, asignacion_id: int, fecha_fin: str) -> None:
        """Sub-3.3: cierra una asignación específica."""
        self._empleado.cerrar_asignacion(asignacion_id, fecha_fin, self.session.user_id)

    @require_permission(perms.MANAGE_EMPLOYEES)
    def get_historial_turnos(self, empleado_id: int) -> List[EmpleadoTurno]:
        """Devuelve el historial de asignaciones ordenado desc."""
        return self._empleado.get_historial_turnos(empleado_id)

    # ── Reads: catálogos para combos de los diálogos ──────────────────────

    @require_permission(perms.MANAGE_EMPLOYEES)
    def list_departamentos(self, solo_activos: bool = True) -> List[Departamento]:
        """Devuelve departamentos (activos por default).

        La vista usa ``solo_activos=True`` para poblar combos (no se
        puede asignar un departamento archivado a un empleado nuevo) y
        ``False`` para construir el mapa id->nombre que resuelve filas
        de empleados que pertenecen a un departamento archivado.
        """
        return self._catalogo.list_departamentos(solo_activos=solo_activos)

    @require_permission(perms.MANAGE_EMPLOYEES)
    def list_cargos(self, solo_activos: bool = True) -> List[Cargo]:
        """Devuelve cargos (activos por default). Ver ``list_departamentos``."""
        return self._catalogo.list_cargos(solo_activos=solo_activos)

    @require_permission(perms.MANAGE_EMPLOYEES)
    def list_cargos_para_departamento(self, departamento_id: int) -> List[Cargo]:
        """Sub-3.2.A: cargos elegibles para un depto (globales + específicos).

        Pensado para refrescar el dropdown del formulario de empleado al
        cambiar el departamento.
        """
        return self._catalogo.list_cargos_para_departamento(departamento_id)

    @require_permission(perms.MANAGE_EMPLOYEES)
    def list_turnos(self, solo_activos: bool = True) -> List[Turno]:
        """Devuelve turnos (activos por default). Ver ``list_departamentos``."""
        return self._turno.list_turnos(solo_activos=solo_activos)

    # ── Writes: alta atómica (Opción A') ──────────────────────────────────

    @require_permission(perms.MANAGE_EMPLOYEES)
    def create_empleado_con_turno(
        self,
        dni: str,
        nombres: str,
        apellidos: str,
        departamento_id: int,
        cargo_id: int,
        fecha_ingreso: str,
        turno_id: int,
        fecha_inicio_turno: str,
        telefono: Optional[str] = None,
        email: Optional[str] = None,
        zkteco_id: Optional[int] = None,
    ) -> CreateEmpleadoResult:
        """Crea un empleado + le asigna su turno inicial en un solo paso.

        Flujo (Opción A' aprobada en Sub-2.6):
            1. Validar turno activo ANTES de crear — evita dejar al
               empleado huérfano si el turno no existe.
            2. Crear el empleado (delega a ``create_empleado``).
            3. Asignar el turno inicial.
            4. Si (3) falla, devolver el empleado creado con ``warning``.

        Raises:
            TurnoNotFoundError, TurnoInactiveError: Turno inválido —
                se valida antes del create, el empleado NO se crea.
            (Errores de ``create_empleado``): DNI inválido, DNI duplicado,
                zkteco duplicado, fecha inválida, etc. — el empleado NO
                se crea.
        """
        actor = self.session.user_id
        # (1) Validar turno activo — si falla aquí, no se crea nada.
        # get_turno propaga TurnoNotFoundError; is_active lo chequeamos acá
        # porque el service NO lo valida (devuelve también archivados).
        turno = self._turno.get_turno(turno_id)
        if not turno.is_active:
            raise TurnoInactiveError(turno_id)

        # (2) Crear empleado.
        empleado = self._empleado.create_empleado(
            dni=dni,
            nombres=nombres,
            apellidos=apellidos,
            departamento_id=departamento_id,
            cargo_id=cargo_id,
            fecha_ingreso=fecha_ingreso,
            actor_user_id=actor,
            telefono=telefono,
            email=email,
            zkteco_id=zkteco_id,
        )
        assert empleado.id is not None

        # (3) Asignar turno. Si falla, compensamos con warning.
        try:
            self._empleado.asignar_turno(
                empleado_id=empleado.id,
                turno_id=turno_id,
                fecha_inicio=fecha_inicio_turno,
                actor_user_id=actor,
            )
        except Exception as exc:  # noqa: BLE001 — compensation deliberado
            # La validación previa hace este caso improbable; si ocurre,
            # es por una condición de carrera (turno archivado justo
            # entre paso 1 y 3) o un fallo de BD. Registramos y dejamos
            # el empleado creado sin turno vigente.
            self._log.warning(
                "Empleado %s creado pero falló asignación de turno: %s",
                empleado.id,
                exc,
            )
            warning = (
                f"Empleado creado correctamente (id={empleado.id}), pero no "
                f"se pudo asignar el turno inicial: {exc}. Use 'Cambiar "
                f"turno' en la lista para completarlo."
            )
            return CreateEmpleadoResult(empleado=empleado, warning=warning)

        return CreateEmpleadoResult(empleado=empleado, warning=None)

    # ── Writes: edición ───────────────────────────────────────────────────

    @require_permission(perms.MANAGE_EMPLOYEES)
    def update_empleado(
        self,
        empleado_id: int,
        dni: str,
        nombres: str,
        apellidos: str,
        departamento_id: int,
        cargo_id: int,
        fecha_ingreso: str,
        telefono: Optional[str] = None,
        email: Optional[str] = None,
        zkteco_id: Optional[int] = None,
    ) -> None:
        """Actualiza los campos editables del empleado.

        No toca ``is_active`` ni los campos de baja — para eso están
        ``deactivate_empleado`` / ``reactivate_empleado``. Tampoco toca
        el turno vigente — para eso está ``cambiar_turno``.
        """
        self._empleado.update_empleado(
            empleado_id=empleado_id,
            dni=dni,
            nombres=nombres,
            apellidos=apellidos,
            departamento_id=departamento_id,
            cargo_id=cargo_id,
            fecha_ingreso=fecha_ingreso,
            actor_user_id=self.session.user_id,
            telefono=telefono,
            email=email,
            zkteco_id=zkteco_id,
        )

    # ── Writes: ciclo de vida ─────────────────────────────────────────────

    @require_permission(perms.MANAGE_EMPLOYEES)
    def deactivate_empleado(
        self,
        empleado_id: int,
        fecha_baja: str,
        motivo_baja: str,
        nota_baja: Optional[str] = None,
    ) -> None:
        """Da de baja al empleado cerrando también su turno vigente."""
        self._empleado.deactivate_empleado(
            empleado_id=empleado_id,
            fecha_baja=fecha_baja,
            motivo_baja=motivo_baja,
            actor_user_id=self.session.user_id,
            nota_baja=nota_baja,
        )

    @require_permission(perms.MANAGE_EMPLOYEES)
    def reactivate_empleado(self, empleado_id: int) -> None:
        """Reactiva un empleado archivado.

        Nota: NO restaura asignación de turno — tras reactivar hay que
        invocar ``asignar_turno`` explícitamente (el backend así lo
        documenta en ``EmpleadoService.reactivate_empleado``).
        """
        self._empleado.reactivate_empleado(
            empleado_id=empleado_id,
            actor_user_id=self.session.user_id,
        )

    # ── Writes: asignación de turnos ──────────────────────────────────────

    @require_permission(perms.MANAGE_EMPLOYEES)
    def asignar_turno(
        self,
        empleado_id: int,
        turno_id: int,
        fecha_inicio: str,
    ) -> EmpleadoTurno:
        """Asigna un turno a un empleado que no tiene vigente."""
        return self._empleado.asignar_turno(
            empleado_id=empleado_id,
            turno_id=turno_id,
            fecha_inicio=fecha_inicio,
            actor_user_id=self.session.user_id,
        )

    @require_permission(perms.MANAGE_EMPLOYEES)
    def cambiar_turno(
        self,
        empleado_id: int,
        turno_nuevo_id: int,
        fecha_inicio_nueva: str,
    ) -> EmpleadoTurno:
        """Cambia el turno vigente de un empleado de forma atómica."""
        return self._empleado.cambiar_turno(
            empleado_id=empleado_id,
            turno_nuevo_id=turno_nuevo_id,
            fecha_inicio_nueva=fecha_inicio_nueva,
            actor_user_id=self.session.user_id,
        )
