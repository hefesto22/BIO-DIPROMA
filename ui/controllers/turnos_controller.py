"""Controller de la vista de Turnos (Sub-2.5).

Orquesta las llamadas al ``TurnoService`` para el CRUD del catálogo de
turnos. Cada método público está decorado con
``@require_permission(MANAGE_EMPLOYEES)`` — mismo permiso que usa el
botón ``shifts`` del sidebar (ver ``MainController.open_shifts``).

El controller NO conoce Tk/customtkinter. Expone métodos sincrónicos
que devuelven modelos de dominio o None; la vista decide cómo
renderizarlos y qué validación in-form aplicar antes de invocarlos.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Dict, Iterable, List, Optional, Sequence

from core.models import permissions as perms
from core.models.empleado import Empleado
from core.models.turno import HorarioTurno, Turno, nombres_dias
from core.services.errors import TurnoConflictoEmpleadosError
from core.services.empleado_service import EmpleadoService
from core.services.permission_service import PermissionService, require_permission
from core.services.session import Session
from core.services.turno_service import TurnoService


class TurnosController:
    """Controller de la vista de Turnos."""

    def __init__(
        self,
        session: Session,
        permission_service: PermissionService,
        turno_service: TurnoService,
        empleado_service: Optional[EmpleadoService] = None,
    ) -> None:
        """Inicializa el controller.

        Args:
            session: Sesión activa del usuario. El decorador
                ``@require_permission`` la lee desde ``self.session``.
            permission_service: Verificador de permisos usado por el
                decorador (lee desde ``self.permission_service``).
            turno_service: Servicio con el CRUD del catálogo de turnos.
            empleado_service: Para mostrar qué empleados tiene asignado
                cada turno. Opcional: sin él la lista queda vacía.
        """
        # Nombres obligatorios para que ``@require_permission`` funcione.
        self.session = session
        self.permission_service = permission_service
        self._turno = turno_service
        self._empleado = empleado_service
        self._log = logging.getLogger(self.__class__.__name__)

    # ── Reads ─────────────────────────────────────────────────────────────

    @require_permission(perms.MANAGE_EMPLOYEES)
    def empleados_por_turno(self) -> Dict[int, List[Empleado]]:
        """``{turno_id: [empleados activos con ese turno vigente]}``."""
        if self._empleado is None:
            return {}
        activos = {e.id: e for e in self._empleado.list_empleados(solo_activos=True)}
        ids = [t.id for t in self._turno.list_turnos(solo_activos=False) if t.id is not None]
        resultado: Dict[int, List[Empleado]] = {}
        for empleado_id, asignaciones in self._empleado.turnos_vigentes_por_empleado(ids).items():
            empleado = activos.get(empleado_id)
            if empleado is None:
                continue
            for asignacion in asignaciones:
                resultado.setdefault(asignacion.turno_id, []).append(empleado)
        return resultado

    @require_permission(perms.MANAGE_EMPLOYEES)
    def list_turnos(self, solo_activos: bool = True) -> List[Turno]:
        """Devuelve la lista de turnos (activos por default)."""
        return self._turno.list_turnos(solo_activos=solo_activos)

    @require_permission(perms.MANAGE_EMPLOYEES)
    def get_turno(self, turno_id: int) -> Turno:
        """Devuelve un turno por id.

        Raises:
            TurnoNotFoundError: Si no existe.
        """
        return self._turno.get_turno(turno_id)

    # ── Writes ────────────────────────────────────────────────────────────

    @require_permission(perms.MANAGE_EMPLOYEES)
    def create_turno(
        self,
        nombre: str,
        hora_entrada: str,
        hora_salida: str,
        minutos_descanso: int,
        dias_semana: int,
        horarios_extra: Sequence[HorarioTurno] = (),
        almuerzo_inicio: Optional[str] = None,
        almuerzo_fin: Optional[str] = None,
    ) -> Turno:
        """Crea un turno con validaciones del service.

        Todas las reglas de negocio (nombre único, formato HH:MM,
        bitmask válido, descanso < duración) se validan en el service.
        El controller solo pasa el ``actor_user_id`` desde la sesión.
        """
        return self._turno.create_turno(
            nombre=nombre,
            hora_entrada=hora_entrada,
            hora_salida=hora_salida,
            minutos_descanso=minutos_descanso,
            dias_semana=dias_semana,
            actor_user_id=self.session.user_id,
            horarios_extra=list(horarios_extra),
            almuerzo_inicio=almuerzo_inicio,
            almuerzo_fin=almuerzo_fin,
        )

    @require_permission(perms.MANAGE_EMPLOYEES)
    def update_turno(
        self,
        turno_id: int,
        nombre: str,
        hora_entrada: str,
        hora_salida: str,
        minutos_descanso: int,
        dias_semana: int,
        horarios_extra: Sequence[HorarioTurno] = (),
        quitar_conflictos: bool = False,
        almuerzo_inicio: Optional[str] = None,
        almuerzo_fin: Optional[str] = None,
    ) -> int:
        """Actualiza todos los campos editables de un turno.

        No toca ``is_active`` — para eso están
        ``archive_turno`` / ``unarchive_turno``.

        Si el turno pasa a cubrir un día en que alguno de sus empleados ya
        tiene OTRO turno (p. ej. se le agrega el sábado y ellos tienen
        "Administrativo Sábado"), se lanza ``TurnoConflictoEmpleadosError``.
        Con ``quitar_conflictos=True`` se guarda y a esos empleados se les
        quita el turno que choca.

        Returns:
            Cuántas asignaciones que chocaban se cerraron (0 normalmente).
        """
        dias_nuevos = dias_semana
        for h in horarios_extra:
            dias_nuevos |= h.dias_semana
        conflictos = (
            self._empleado.conflictos_si_turno_cubre(turno_id, dias_nuevos)
            if self._empleado is not None
            else []
        )
        if conflictos and not quitar_conflictos:
            raise TurnoConflictoEmpleadosError(
                empleados=sorted(
                    {f"{c.empleado.nombres} {c.empleado.apellidos}" for c in conflictos}
                ),
                turnos=sorted({c.turno.nombre for c in conflictos}),
                dias=nombres_dias(_union(c.dias for c in conflictos)),
            )
        self._turno.update_turno(
            turno_id=turno_id,
            nombre=nombre,
            hora_entrada=hora_entrada,
            hora_salida=hora_salida,
            minutos_descanso=minutos_descanso,
            dias_semana=dias_semana,
            actor_user_id=self.session.user_id,
            horarios_extra=list(horarios_extra),
            almuerzo_inicio=almuerzo_inicio,
            almuerzo_fin=almuerzo_fin,
        )
        if not conflictos or self._empleado is None:
            return 0
        return self._empleado.quitar_turnos_en_conflicto(
            conflictos, self.session.user_id, hoy=date.today()
        )

    @require_permission(perms.MANAGE_EMPLOYEES)
    def archive_turno(self, turno_id: int) -> None:
        """Archiva un turno (soft-delete)."""
        self._turno.archive_turno(turno_id, self.session.user_id)

    @require_permission(perms.MANAGE_EMPLOYEES)
    def unarchive_turno(self, turno_id: int) -> None:
        """Reactiva un turno archivado."""
        self._turno.unarchive_turno(turno_id, self.session.user_id)


def _union(bitmasks: Iterable[int]) -> int:
    total = 0
    for b in bitmasks:
        total |= b
    return total
