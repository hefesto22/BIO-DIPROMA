"""Servicio de consolidación raw → asistencias.

Toma las marcadas crudas de ``registros_raw`` en un rango, las cruza con
la asignación de turno vigente de cada empleado para cada día, aplica
las tolerancias configuradas por turno, y emite una fila consolidada en
``asistencias`` por cada (empleado, fecha) del rango.

Entradas críticas del algoritmo (todas pre-cargadas por el servicio):

    - Empleados activos (``empleados.is_active = 1``).
    - Mapeo ``zkteco_id → empleado``: solo empleados con reloj asignado.
    - Historial completo de ``empleado_turnos`` por empleado (para
      resolver qué turno aplicaba en cada día del rango).
    - Catálogo de turnos (dict ``turno_id → Turno``).
    - Feriados del rango (set de fechas).
    - Ausencias del rango (vacaciones, días libres, permisos,
      incapacidades) agrupadas por empleado.
    - Marcadas del rango extendido (+1 día al final para salidas de
      turnos nocturnos).

Decisiones aprobadas que el servicio honra:

    - D2 (consolidación inline): este servicio es llamado automáticamente
      por ``SincronizacionService`` tras una sync exitosa, pero también
      puede invocarse manualmente para re-consolidar un mes después de
      editar turnos.
    - D4 (zkteco_user_ids desconocidos): las marcadas de IDs sin empleado
      mapeado se conservan en ``registros_raw`` (auditoría) pero NO
      entran a la consolidación; en su lugar se reportan como warnings
      en ``ResultadoConsolidacion.marcadas_desconocidas``.
    - D5 (algoritmo híbrido turno nocturno): el detalle vive en
      ``consolidacion_algorithm.py`` (funciones puras).

Diseño (SOLID):
    - S: consolida raw → asistencias. Nada más.
    - D: recibe 6 repos + audit_logger por inyección.
    - Reglas de excepción: errores por empleado se capturan y continúan
      con el resto (robustez frente a datos sucios de un solo empleado);
      errores estructurales (no hay empleados, rango inválido) se relanzan.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, date, timedelta, timezone
from typing import Dict, Iterator, List, Optional

from core.models.asistencia import Asistencia
from core.models.ausencia import Ausencia
from core.models.empleado import Empleado
from core.models.empleado_turno import EmpleadoTurno
from core.models.registro_raw import RegistroRaw
from core.models.turno import Turno
from core.repositories.asistencia_repository import IAsistenciaWriteRepository
from core.repositories.ausencia_repository import IAusenciaReadRepository
from core.repositories.empleado_repository import IEmpleadoReadRepository
from core.repositories.empleado_turno_repository import IEmpleadoTurnoReadRepository
from core.repositories.feriado_repository import IFeriadoReadRepository
from core.repositories.registro_raw_repository import IRegistroRawReadRepository
from core.repositories.turno_repository import ITurnoReadRepository
from core.services.audit_logger import AuditLogger
from core.services.consolidacion_algorithm import (
    derivar_estado,
    estado_no_trabaja,
    parear_marcadas,
    resolver_turno_en_fecha,
    ventana_del_dia,
)
from core.services.errors import EmpleadoNotFoundError, InvalidRangoError
from core.services.sincronizacion_result import MarcadaDesconocida, ResultadoConsolidacion
from core.services.validators import validate_fecha_iso

logger = logging.getLogger(__name__)


class ConsolidacionService:
    """Genera filas ``asistencias`` a partir de ``registros_raw``."""

    def __init__(
        self,
        empleado_read: IEmpleadoReadRepository,
        empleado_turno_read: IEmpleadoTurnoReadRepository,
        turno_read: ITurnoReadRepository,
        feriado_read: IFeriadoReadRepository,
        registro_raw_read: IRegistroRawReadRepository,
        asistencia_write: IAsistenciaWriteRepository,
        audit_logger: AuditLogger,
        ausencia_read: Optional[IAusenciaReadRepository] = None,
    ) -> None:
        """Construye el servicio.

        Args:
            empleado_read: Lista de empleados activos + resolución por
                ``zkteco_id``.
            empleado_turno_read: Historial completo por empleado para
                resolver turno vigente en cada fecha histórica.
            turno_read: Catálogo de turnos (se pre-carga completo).
            feriado_read: ``list_by_rango`` para pre-cargar el rango.
            registro_raw_read: ``list_by_rango`` (todas las marcadas, sin
                filtrar por dispositivo — un empleado puede marcar en
                múltiples relojes).
            asistencia_write: UPSERT idempotente por (empleado, fecha).
            audit_logger: Traza de qué rango se consolidó y cuándo.
            ausencia_read: ``list_en_rango`` para marcar vacaciones, días
                libres, permisos e incapacidades. ``None`` = no hay módulo
                de ausencias (los días sin marca quedan ``AUSENTE``).
        """
        self._empleado_read = empleado_read
        self._empleado_turno_read = empleado_turno_read
        self._turno_read = turno_read
        self._feriado_read = feriado_read
        self._registro_raw_read = registro_raw_read
        self._asistencia_write = asistencia_write
        self._audit = audit_logger
        self._ausencia_read = ausencia_read

    # ── API pública ──────────────────────────────────────────────────────────

    def consolidar_rango(
        self,
        desde: str,
        hasta: str,
        empleado_id: Optional[int] = None,
    ) -> ResultadoConsolidacion:
        """Consolida raw → asistencias en el rango, opcionalmente por empleado.

        Proceso:

            1. Valida el rango y lo convierte a ``date``.
            2. Pre-carga: empleados activos, turnos, feriados, raws del
               rango extendido (+1 día para salidas nocturnas).
            3. Si se pasa ``empleado_id``, restringe el set a ese
               empleado (debe estar activo) — re-consolidación manual.
            4. Construye el mapeo ``zkteco_id → empleado`` y agrupa raws
               por ``zkteco_user_id``.
            5. Detecta raws de IDs no mapeados → ``marcadas_desconocidas``
               (solo en consolidación global; omitido cuando hay filtro
               por empleado porque sería una métrica engañosa).
            6. Para cada empleado × cada día del rango: consolida y upserta.
            7. Errores a nivel empleado-día se capturan en
               ``errores_empleado`` y se continúa con el resto.
            8. Audita el rango consolidado (incluye ``empleado_id``).

        Args:
            desde: ISO ``"YYYY-MM-DD"``.
            hasta: ISO ``"YYYY-MM-DD"`` (inclusivo, >= desde).
            empleado_id: Si se pasa, re-consolida solo a ese empleado
                (debe estar activo). ``None`` = todos los activos.

        Returns:
            ``ResultadoConsolidacion`` con el resumen numérico + warnings.

        Raises:
            InvalidRangoError: rango invertido.
            InvalidDateError: formato inválido.
            EmpleadoNotFoundError: ``empleado_id`` no está entre los
                empleados activos (no existe o está archivado).
        """
        desde_date, hasta_date = self._validar_y_parsear_rango(desde, hasta)

        empleados_activos = self._empleado_read.list_active()
        if empleado_id is not None:
            empleados_activos = self._filtrar_empleado(empleados_activos, empleado_id)
        turnos_por_id = self._cargar_turnos()
        feriados_set = self._cargar_feriados(desde, hasta)
        ausencias_por_empleado = self._cargar_ausencias(desde, hasta)
        raws_por_zkteco = self._cargar_raws_agrupados(desde_date, hasta_date)

        marcadas_desconocidas = self._calcular_desconocidas(
            raws_por_zkteco, empleados_activos, filtrado=empleado_id is not None
        )

        resultado = ResultadoConsolidacion(
            empleados_procesados=len(empleados_activos),
            marcadas_desconocidas=marcadas_desconocidas,
            dias_procesados=_contar_dias(desde_date, hasta_date),
        )

        for empleado in empleados_activos:
            self._consolidar_empleado_en_rango(
                empleado=empleado,
                desde_date=desde_date,
                hasta_date=hasta_date,
                turnos_por_id=turnos_por_id,
                feriados_set=feriados_set,
                raws_del_empleado=_raws_de_empleado(empleado, raws_por_zkteco),
                resultado=resultado,
                ausencias=ausencias_por_empleado.get(empleado.id or 0, []),
            )

        self._audit.log(
            action="consolidacion.rango",
            user_id=None,
            details=json.dumps(
                {
                    "desde": desde,
                    "hasta": hasta,
                    "empleado_id": empleado_id,
                    "asistencias_upsertadas": resultado.asistencias_upsertadas,
                    "empleados_procesados": resultado.empleados_procesados,
                    "desconocidos": len(resultado.marcadas_desconocidas),
                }
            ),
        )
        return resultado

    # ── Helpers de pre-carga ─────────────────────────────────────────────────

    def _cargar_turnos(self) -> Dict[int, Turno]:
        """Pre-carga todo el catálogo de turnos (activos + archivados).

        Incluye archivados porque un empleado puede tener historial con un
        turno que ya fue archivado — si el rango a consolidar cae en esa
        época, ese turno debe resolverse correctamente.
        """
        turnos: Dict[int, Turno] = {}
        for turno in self._turno_read.list_all():
            assert turno.id is not None
            turnos[turno.id] = turno
        return turnos

    def _cargar_feriados(self, desde: str, hasta: str) -> set[str]:
        """Pre-carga las fechas feriadas del rango como ``set`` para lookup O(1)."""
        return {feriado.fecha for feriado in self._feriado_read.list_by_rango(desde, hasta)}

    def _cargar_ausencias(self, desde: str, hasta: str) -> Dict[int, List[Ausencia]]:
        """Pre-carga las ausencias que tocan el rango, agrupadas por empleado."""
        if self._ausencia_read is None:
            return {}
        agrupadas: Dict[int, List[Ausencia]] = defaultdict(list)
        for ausencia in self._ausencia_read.list_en_rango(desde, hasta):
            agrupadas[ausencia.empleado_id].append(ausencia)
        return agrupadas

    def _cargar_raws_agrupados(
        self, desde_date: date, hasta_date: date
    ) -> Dict[int, List[RegistroRaw]]:
        """Carga raws del rango extendido +1 día, agrupados por ``zkteco_user_id``.

        El +1 día es para capturar salidas de turnos nocturnos que inician
        el último día del rango. Si se pidió consolidar ``desde=2026-04-01,
        hasta=2026-04-30``, una marcada de salida el 2026-05-01 02:00 para
        un turno nocturno que entró el 30 aún debe contarse.

        También se extiende -1 día al inicio, por simetría ante llamadas
        "solo un día" en turnos nocturnos: si consolido solo el 2026-04-30,
        podría haber un raw del 2026-04-29 00:30 correspondiente a un
        turno que entró el 29 — pero ese turno se consolida el 29, no el
        30, así que ese raw no me sirve acá. El -1 día conceptualmente es
        para permitir tolerancias de entrada que caigan al día anterior
        (p. ej. entrada oficial 00:05 con tolerancia 10 min → ventana
        arranca 23:55 del día previo). Con tolerancias típicas <30min esto
        es raro, pero es barato protegerse.
        """
        ts_desde = f"{(desde_date - timedelta(days=1)).isoformat()}T00:00:00"
        ts_hasta = f"{(hasta_date + timedelta(days=1)).isoformat()}T23:59:59"
        raws = self._registro_raw_read.list_by_rango(ts_desde, ts_hasta)
        agrupados: Dict[int, List[RegistroRaw]] = defaultdict(list)
        for raw in raws:
            agrupados[raw.zkteco_user_id].append(raw)
        return agrupados

    def _calcular_desconocidas(
        self,
        raws_por_zkteco: Dict[int, List[RegistroRaw]],
        empleados_activos: List[Empleado],
        filtrado: bool,
    ) -> List[MarcadaDesconocida]:
        """Calcula ``marcadas_desconocidas`` considerando si hay filtro.

        Cuando la consolidación se filtra a un solo empleado (caso
        re-consolidación manual), esta métrica se omite: devolverla
        con los raws de "todos los demás empleados" sería engañoso
        para la UI — el usuario solo quiere saber qué pasó con el
        empleado filtrado.
        """
        if filtrado:
            return []
        empleados_por_zkteco = {
            emp.zkteco_id: emp for emp in empleados_activos if emp.zkteco_id is not None
        }
        return self._detectar_desconocidos(raws_por_zkteco, empleados_por_zkteco)

    def _detectar_desconocidos(
        self,
        raws_por_zkteco: Dict[int, List[RegistroRaw]],
        empleados_por_zkteco: Dict[int, Empleado],
    ) -> List[MarcadaDesconocida]:
        """Identifica los ``zkteco_user_id`` sin empleado mapeado en nuestra BD."""
        desconocidos: List[MarcadaDesconocida] = []
        for zkteco_user_id, lista_raws in raws_por_zkteco.items():
            if zkteco_user_id in empleados_por_zkteco:
                continue
            desconocidos.append(
                MarcadaDesconocida(
                    zkteco_user_id=zkteco_user_id,
                    cantidad_marcadas=len(lista_raws),
                )
            )
        desconocidos.sort(key=lambda m: m.zkteco_user_id)
        return desconocidos

    @staticmethod
    def _filtrar_empleado(empleados: List[Empleado], empleado_id: int) -> List[Empleado]:
        """Reduce la lista al empleado indicado (debe estar activo).

        Raises:
            EmpleadoNotFoundError: si el id no está entre los activos.
        """
        filtrados = [emp for emp in empleados if emp.id == empleado_id]
        if not filtrados:
            raise EmpleadoNotFoundError(empleado_id)
        return filtrados

    # ── Helpers de consolidación por empleado ────────────────────────────────

    def _consolidar_empleado_en_rango(
        self,
        empleado: Empleado,
        desde_date: date,
        hasta_date: date,
        turnos_por_id: Dict[int, Turno],
        feriados_set: set[str],
        raws_del_empleado: List[RegistroRaw],
        resultado: ResultadoConsolidacion,
        ausencias: Optional[List[Ausencia]] = None,
    ) -> None:
        """Consolida todos los días del rango para un empleado."""
        assert empleado.id is not None
        historial = self._empleado_turno_read.list_historial(empleado.id)

        for fecha_iso in _iterar_fechas_iso(desde_date, hasta_date):
            try:
                asistencia = self._consolidar_dia_de_empleado(
                    empleado=empleado,
                    fecha_iso=fecha_iso,
                    historial=historial,
                    turnos_por_id=turnos_por_id,
                    feriados_set=feriados_set,
                    raws_del_empleado=raws_del_empleado,
                    ausencias=ausencias or [],
                )
                self._asistencia_write.upsert(asistencia)
                resultado.asistencias_upsertadas += 1
            except ValueError as exc:
                # Datos inconsistentes del empleado (historial mal formateado,
                # turno asignado que ya no existe, fechas inválidas). Se
                # loguea y se continúa con los demás — un empleado con
                # datos sucios NO debe frenar todo el batch.
                mensaje = f"empleado={empleado.id} fecha={fecha_iso}: {exc}"
                resultado.errores_empleado.append(mensaje)
                logger.warning("consolidacion.dia_fallido %s", mensaje)

    def _consolidar_dia_de_empleado(
        self,
        empleado: Empleado,
        fecha_iso: str,
        historial: List[EmpleadoTurno],
        turnos_por_id: Dict[int, Turno],
        feriados_set: set[str],
        raws_del_empleado: List[RegistroRaw],
        ausencias: Optional[List[Ausencia]] = None,
    ) -> Asistencia:
        """Calcula la ``Asistencia`` de un empleado para un día.

        Puro en el sentido de dominio: no persiste (el caller hace upsert),
        solo construye la instancia.
        """
        assert empleado.id is not None
        # Sub-3.2.B: se pasa el catálogo para filtrar por bitmask
        # ``dias_semana`` cuando hay múltiples asignaciones vigentes.
        turno_id = resolver_turno_en_fecha(historial, fecha_iso, turnos_por_id)
        turno: Optional[Turno] = turnos_por_id.get(turno_id) if turno_id is not None else None
        if turno is not None:
            # Un turno puede tener otro horario para ciertos días (p. ej. el
            # sábado de 09:00 a 12:00): el algoritmo trabaja con las horas
            # de ESE día.
            del_dia = turno.horario_para(date.fromisoformat(fecha_iso).weekday())
            turno = del_dia if del_dia is not None else turno
        es_feriado = fecha_iso in feriados_set

        estado_override = estado_no_trabaja(turno, fecha_iso, es_feriado)
        if estado_override is not None:
            return self._asistencia_no_trabaja(
                empleado_id=empleado.id,
                fecha_iso=fecha_iso,
                estado=estado_override,
                turno_id=turno_id,
            )

        # El empleado debía trabajar, pero tiene vacaciones / día libre /
        # permiso / incapacidad registrados ese día: no es AUSENTE.
        ausencia = next((a for a in ausencias or [] if a.cubre(fecha_iso)), None)
        if ausencia is not None:
            return Asistencia(
                id=None,
                empleado_id=empleado.id,
                fecha=fecha_iso,
                estado=ausencia.tipo,
                turno_id_aplicado=turno_id,
                hora_entrada_real=None,
                hora_salida_real=None,
                minutos_tarde=0,
                minutos_salida_temprana=0,
                consolidada_en=_now_iso_utc(),
            )

        # Turno aplica ese día: buscamos marcadas dentro de la ventana.
        assert turno is not None  # garantizado por estado_no_trabaja
        inicio_ventana, fin_ventana = ventana_del_dia(turno, fecha_iso)
        # Sub-2.7e: el algoritmo "ventana dinámica" usa la primera
        # marcada como ancla y asume salida según turno cuando el día
        # ya cerró sin segunda marcada (>= 20:00 hora local). La
        # decisión depende de "ahora" — lo inyectamos para que el
        # algoritmo siga puro y testeable.
        marcadas = parear_marcadas(
            raws_del_empleado,
            inicio_ventana,
            fin_ventana,
            turno,
            fecha_iso,
            ahora=_ahora_local(),
        )
        derivado = derivar_estado(marcadas, turno, fecha_iso)

        return Asistencia(
            id=None,
            empleado_id=empleado.id,
            fecha=fecha_iso,
            estado=derivado.estado,
            turno_id_aplicado=turno_id,
            hora_entrada_real=derivado.hora_entrada_real,
            hora_salida_real=derivado.hora_salida_real,
            minutos_tarde=derivado.minutos_tarde,
            minutos_salida_temprana=derivado.minutos_salida_temprana,
            observaciones=marcadas.observacion,
            consolidada_en=_now_iso_utc(),
        )

    def _asistencia_no_trabaja(
        self,
        empleado_id: int,
        fecha_iso: str,
        estado: str,
        turno_id: Optional[int],
    ) -> Asistencia:
        """Construye una ``Asistencia`` para estados FERIADO / SIN_TURNO.

        ``turno_id_aplicado`` se deja en ``None`` en ambos casos (el turno
        no aplicó ese día, por feriado o porque el empleado no debía
        trabajar según el bitmask de días).
        """
        del turno_id  # no se persiste — los estados "no trabaja" no lo necesitan
        return Asistencia(
            id=None,
            empleado_id=empleado_id,
            fecha=fecha_iso,
            estado=estado,
            turno_id_aplicado=None,
            hora_entrada_real=None,
            hora_salida_real=None,
            minutos_tarde=0,
            minutos_salida_temprana=0,
            consolidada_en=_now_iso_utc(),
        )

    def _validar_y_parsear_rango(self, desde: str, hasta: str) -> tuple[date, date]:
        """Valida formato ISO y orden, y retorna tupla de ``date``."""
        desde_date = validate_fecha_iso(desde, campo="desde")
        hasta_date = validate_fecha_iso(hasta, campo="hasta")
        if desde_date > hasta_date:
            raise InvalidRangoError(desde=desde, hasta=hasta)
        return desde_date, hasta_date


# ── Helpers módulo ────────────────────────────────────────────────────────────


def _raws_de_empleado(
    empleado: Empleado, raws_por_zkteco: Dict[int, List[RegistroRaw]]
) -> List[RegistroRaw]:
    """Devuelve las marcadas del empleado, o ``[]`` si no tiene ``zkteco_id`` mapeado."""
    if empleado.zkteco_id is None:
        return []
    return raws_por_zkteco.get(empleado.zkteco_id, [])


def _iterar_fechas_iso(desde: date, hasta: date) -> Iterator[str]:
    """Itera inclusive desde ``desde`` hasta ``hasta`` emitiendo strings ISO."""
    actual = desde
    while actual <= hasta:
        yield actual.isoformat()
        actual = actual + timedelta(days=1)


def _contar_dias(desde: date, hasta: date) -> int:
    """Cantidad de días en el rango inclusive."""
    return (hasta - desde).days + 1


def _now_iso_utc() -> str:
    """Momento actual en ISO-8601 UTC con segundos (sin microsegundos)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _ahora_local() -> datetime:
    """Devuelve ``datetime.now()`` naive en hora local.

    Se usa para decidir si el día de la asistencia ya cerró (regla de
    "salida asumida"). Lo extrajimos a un helper de módulo para que sea
    fácilmente monkeypatch-able desde tests del service si hace falta;
    el algoritmo puro recibe el datetime explícito y no toca el reloj.
    """
    return datetime.now()
