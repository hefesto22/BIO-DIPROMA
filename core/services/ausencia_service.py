"""Servicio de ausencias: vacaciones, días libres, permisos e incapacidades.

Registra rangos de fechas en que un empleado no debe marcar, y mantiene
la asistencia al día: al registrar, editar o borrar una ausencia que toca
fechas pasadas (o de hoy), re-consolida ese tramo del empleado para que
la lista de asistencia muestre "Vacaciones" en vez de "Ausente" (o al
revés, si se borró).

Reglas de negocio:
    - Tipo dentro de ``TipoAusencia``.
    - ``fecha_hasta >= fecha_desde`` y un registro no pasa de un año.
    - Solo empleados activos pueden recibir ausencias nuevas.
    - Las ausencias de un mismo empleado no se cruzan → ``AusenciaSolapadaError``.
    - Los "días laborables" de una ausencia son los que su turno cubre y no
      son feriado: es lo que realmente se descuenta (un domingo dentro de
      unas vacaciones no cuenta).

Diseño (SOLID):
    - S: solo gestiona ausencias. La consolidación la hace
      ``ConsolidacionService`` (inyectado, opcional).
    - D: repos + audit logger + reloj inyectados.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List, Optional, Set

from core.models.ausencia import ALL_TIPOS_AUSENCIA, TIPO_AUSENCIA_TEXTO, Ausencia
from core.models.empleado_turno import EmpleadoTurno
from core.models.turno import Turno
from core.repositories.ausencia_repository import (
    IAusenciaReadRepository,
    IAusenciaWriteRepository,
)
from core.repositories.empleado_repository import IEmpleadoReadRepository
from core.repositories.empleado_turno_repository import IEmpleadoTurnoReadRepository
from core.repositories.feriado_repository import IFeriadoReadRepository
from core.repositories.turno_repository import ITurnoReadRepository
from core.services.audit_logger import AuditLogger
from core.services.consolidacion_algorithm import resolver_turno_en_fecha
from core.services.consolidacion_service import ConsolidacionService
from core.services.errors import (
    AusenciaEmpleadoInactivoError,
    AusenciaNotFoundError,
    AusenciaSolapadaError,
    EmpleadoNotFoundError,
    RangoAusenciaInvalidoError,
    TipoAusenciaInvalidoError,
)
from core.services.validators import validate_fecha_iso

# Un registro de más de un año casi siempre es un error de tipeo en el año.
_MAX_DIAS_RANGO = 366
_MAX_LARGO_NOTA = 300


class AusenciaService:
    """CRUD de ausencias con validaciones, auditoría y re-consolidación."""

    def __init__(
        self,
        ausencia_read: IAusenciaReadRepository,
        ausencia_write: IAusenciaWriteRepository,
        empleado_read: IEmpleadoReadRepository,
        empleado_turno_read: IEmpleadoTurnoReadRepository,
        turno_read: ITurnoReadRepository,
        feriado_read: IFeriadoReadRepository,
        audit_logger: AuditLogger,
        consolidacion_service: Optional[ConsolidacionService] = None,
        hoy: Callable[[], date] = date.today,
    ) -> None:
        """Inyecta dependencias.

        Args:
            consolidacion_service: Si se pasa, al guardar o borrar se
                re-consolida el tramo pasado del empleado.
            hoy: Reloj inyectable (tests).
        """
        self._read = ausencia_read
        self._write = ausencia_write
        self._emp_read = empleado_read
        self._et_read = empleado_turno_read
        self._turno_read = turno_read
        self._feriado_read = feriado_read
        self._audit = audit_logger
        self._consolidacion = consolidacion_service
        self._hoy = hoy
        self._log = logging.getLogger(self.__class__.__name__)

    # ── Lecturas ──────────────────────────────────────────────────────────

    def listar(self) -> List[Ausencia]:
        """Todas las ausencias (lo más reciente primero)."""
        return self._read.list_all()

    def listar_por_empleado(self, empleado_id: int) -> List[Ausencia]:
        """Las ausencias de un empleado (lo más reciente primero)."""
        return self._read.list_by_empleado(empleado_id)

    def get(self, ausencia_id: int) -> Ausencia:
        """Devuelve la ausencia o lanza ``AusenciaNotFoundError``."""
        ausencia = self._read.get_by_id(ausencia_id)
        if ausencia is None:
            raise AusenciaNotFoundError(ausencia_id)
        return ausencia

    def dias_laborables(self, empleado_id: int, desde: str, hasta: str) -> int:
        """Días del rango en que el empleado debía trabajar (turno y no feriado)."""
        d = validate_fecha_iso(desde, "desde")
        h = validate_fecha_iso(hasta, "hasta")
        if h < d:
            return 0
        return self._calendario().contar(empleado_id, d, h)

    def dias_laborables_de(self, ausencias: Iterable[Ausencia]) -> Dict[int, int]:
        """``{ausencia_id: días laborables}`` para varias ausencias de una vez."""
        calendario = self._calendario()
        resultado: Dict[int, int] = {}
        for a in ausencias:
            if a.id is None:
                continue
            resultado[a.id] = calendario.contar(
                a.empleado_id,
                date.fromisoformat(a.fecha_desde),
                date.fromisoformat(a.fecha_hasta),
            )
        return resultado

    def resumen_anual(self, empleado_id: int, anio: int) -> Dict[str, int]:
        """``{tipo: días laborables}`` del empleado dentro del año dado."""
        inicio, fin = date(anio, 1, 1), date(anio, 12, 31)
        calendario = self._calendario()
        resumen = {tipo: 0 for tipo in TIPO_AUSENCIA_TEXTO}
        for a in self._read.list_by_empleado(empleado_id):
            desde = max(date.fromisoformat(a.fecha_desde), inicio)
            hasta = min(date.fromisoformat(a.fecha_hasta), fin)
            if desde <= hasta:
                resumen[a.tipo] = resumen.get(a.tipo, 0) + calendario.contar(
                    empleado_id, desde, hasta
                )
        return resumen

    # ── Escrituras ────────────────────────────────────────────────────────

    def registrar(
        self,
        empleado_id: int,
        tipo: str,
        fecha_desde: str,
        fecha_hasta: str,
        nota: Optional[str],
        actor_user_id: int,
    ) -> Ausencia:
        """Registra una ausencia nueva.

        Raises:
            EmpleadoNotFoundError: el empleado no existe.
            AusenciaEmpleadoInactivoError: el empleado está de baja.
            TipoAusenciaInvalidoError, RangoAusenciaInvalidoError,
            InvalidDateError, AusenciaSolapadaError.
        """
        self._validar_empleado_activo(empleado_id)
        self._validar(empleado_id, tipo, fecha_desde, fecha_hasta, excluir_id=None)
        creada = self._write.create(
            Ausencia(
                id=None,
                empleado_id=empleado_id,
                tipo=tipo,
                fecha_desde=fecha_desde,
                fecha_hasta=fecha_hasta,
                nota=_normalizar_nota(nota),
                creado_por_user_id=actor_user_id,
                creado_en=_ahora_iso_utc(),
            )
        )
        self._auditar("ausencia_registrada", actor_user_id, creada)
        self._reconsolidar(empleado_id, [(fecha_desde, fecha_hasta)])
        return creada

    def actualizar(
        self,
        ausencia_id: int,
        tipo: str,
        fecha_desde: str,
        fecha_hasta: str,
        nota: Optional[str],
        actor_user_id: int,
    ) -> Ausencia:
        """Cambia tipo, fechas o nota. El empleado no se cambia (se borra y crea otra)."""
        actual = self.get(ausencia_id)
        self._validar(actual.empleado_id, tipo, fecha_desde, fecha_hasta, excluir_id=ausencia_id)
        nueva = Ausencia(
            id=ausencia_id,
            empleado_id=actual.empleado_id,
            tipo=tipo,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
            nota=_normalizar_nota(nota),
            creado_por_user_id=actual.creado_por_user_id,
            creado_en=actual.creado_en,
        )
        self._write.update(nueva)
        self._auditar("ausencia_actualizada", actor_user_id, nueva)
        self._reconsolidar(
            actual.empleado_id,
            [(actual.fecha_desde, actual.fecha_hasta), (fecha_desde, fecha_hasta)],
        )
        return nueva

    def eliminar(self, ausencia_id: int, actor_user_id: int) -> None:
        """Borra la ausencia y recalcula esos días (vuelven a ser normales)."""
        actual = self.get(ausencia_id)
        self._write.delete(ausencia_id)
        self._auditar("ausencia_eliminada", actor_user_id, actual)
        self._reconsolidar(actual.empleado_id, [(actual.fecha_desde, actual.fecha_hasta)])

    # ── Helpers privados ──────────────────────────────────────────────────

    def _calendario(self) -> "_CalendarioLaboral":
        return _CalendarioLaboral(self._turno_read, self._feriado_read, self._et_read)

    def _validar_empleado_activo(self, empleado_id: int) -> None:
        empleado = self._emp_read.get_by_id(empleado_id)
        if empleado is None:
            raise EmpleadoNotFoundError(empleado_id)
        if not empleado.is_active:
            raise AusenciaEmpleadoInactivoError(empleado_id)

    def _validar(
        self,
        empleado_id: int,
        tipo: str,
        fecha_desde: str,
        fecha_hasta: str,
        excluir_id: Optional[int],
    ) -> None:
        if tipo not in ALL_TIPOS_AUSENCIA:
            raise TipoAusenciaInvalidoError(tipo)
        desde = validate_fecha_iso(fecha_desde, "fecha_desde")
        hasta = validate_fecha_iso(fecha_hasta, "fecha_hasta")
        if hasta < desde:
            raise RangoAusenciaInvalidoError("La fecha final no puede ser anterior a la inicial.")
        if (hasta - desde).days + 1 > _MAX_DIAS_RANGO:
            raise RangoAusenciaInvalidoError(
                "Un mismo registro no puede pasar de un año. Revise el año de las fechas."
            )
        for otra in self._read.list_by_empleado(empleado_id):
            if otra.id != excluir_id and otra.se_cruza_con(fecha_desde, fecha_hasta):
                raise AusenciaSolapadaError(otra.tipo_texto, otra.fecha_desde, otra.fecha_hasta)

    def _auditar(self, accion: str, actor_user_id: int, a: Ausencia) -> None:
        self._audit.log(
            action=accion,
            user_id=actor_user_id,
            details=json.dumps(
                {
                    "id": a.id,
                    "empleado_id": a.empleado_id,
                    "tipo": a.tipo,
                    "desde": a.fecha_desde,
                    "hasta": a.fecha_hasta,
                },
                ensure_ascii=False,
            ),
        )
        self._log.info("%s id=%s empleado=%s", accion, a.id, a.empleado_id)

    def _reconsolidar(self, empleado_id: int, rangos: List[tuple[str, str]]) -> None:
        """Recalcula la asistencia del tramo que ya pasó (o es hoy).

        Los días futuros se consolidan solos con la próxima sincronización.
        """
        if self._consolidacion is None:
            return
        empleado = self._emp_read.get_by_id(empleado_id)
        if empleado is None or not empleado.is_active:
            return
        hoy = self._hoy().isoformat()
        desde = min(r[0] for r in rangos)
        hasta = min(max(r[1] for r in rangos), hoy)
        if desde > hasta:
            return
        try:
            self._consolidacion.consolidar_rango(desde, hasta, empleado_id=empleado_id)
        except EmpleadoNotFoundError:
            self._log.warning("No se re-consolidó: empleado %s no está activo.", empleado_id)


class _CalendarioLaboral:
    """Cuenta días laborables con caché de turnos, historiales y feriados."""

    def __init__(
        self,
        turno_read: ITurnoReadRepository,
        feriado_read: IFeriadoReadRepository,
        empleado_turno_read: IEmpleadoTurnoReadRepository,
    ) -> None:
        self._turno_read = turno_read
        self._feriado_read = feriado_read
        self._et_read = empleado_turno_read
        self._turnos: Optional[Dict[int, Turno]] = None
        self._historiales: Dict[int, List[EmpleadoTurno]] = {}
        self._feriados: Optional[Set[str]] = None

    def contar(self, empleado_id: int, desde: date, hasta: date) -> int:
        turnos = self._catalogo()
        feriados = self._lista_feriados()
        historial = self._historial(empleado_id)
        total = 0
        dia = desde
        while dia <= hasta:
            iso = dia.isoformat()
            if iso not in feriados and resolver_turno_en_fecha(historial, iso, turnos) is not None:
                total += 1
            dia += timedelta(days=1)
        return total

    def _catalogo(self) -> Dict[int, Turno]:
        if self._turnos is None:
            self._turnos = {t.id: t for t in self._turno_read.list_all() if t.id is not None}
        return self._turnos

    def _lista_feriados(self) -> Set[str]:
        if self._feriados is None:
            self._feriados = {f.fecha for f in self._feriado_read.list_all()}
        return self._feriados

    def _historial(self, empleado_id: int) -> List[EmpleadoTurno]:
        if empleado_id not in self._historiales:
            self._historiales[empleado_id] = self._et_read.list_historial(empleado_id)
        return self._historiales[empleado_id]


def _normalizar_nota(nota: Optional[str]) -> Optional[str]:
    """Recorta espacios; vacío → ``None``; corta en 300 caracteres."""
    if nota is None:
        return None
    limpia = " ".join(nota.split())
    return limpia[:_MAX_LARGO_NOTA] or None


def _ahora_iso_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
