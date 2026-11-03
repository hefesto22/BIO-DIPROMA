"""Servicio de Asistencia (Sub-3.4b).

Orquesta el caso de uso "vista de asistencia" de la UI:

    1. Listar asistencias por rango de fechas, opcionalmente filtradas
       por un empleado, enriquecidas con nombres de empleado y turno
       (DTO ``AsistenciaVista``) — un solo viaje por catálogo aunque la
       lista tenga 1 o 500 filas (evita N+1 contra los repos de
       ``empleados`` y ``turnos``).
    2. Listar empleados activos para el combo de filtro.
    3. Actualizar las ``observaciones`` de una asistencia específica,
       registrando la acción en ``audit_log``.

Reglas de consolidación idempotente:
    La UI **no** re-consolida al editar observaciones — llama al
    repositorio ``update_observaciones`` directamente. Esto respeta la
    invariante del UPSERT (una re-consolidación NO sobrescribe lo que
    anotó manualmente el operador).

Diseño (SOLID):
    - S: una sola responsabilidad — la vista de asistencia. El CRUD de
         empleados/turnos sigue viviendo en sus servicios.
    - D: recibe los 4 repositorios y el ``AuditLogger`` por inyección;
         ningún ``import`` de infraestructura concreta.
    - ISP: consume solo ``IAsistenciaReadRepository`` /
           ``IAsistenciaWriteRepository`` y las interfaces READ de los
           catálogos vecinos — no pide handles de escritura que no usa.

Truncamiento (``limit`` + ``truncado``):
    La UI pasa un tope grande (ej. 500) y el servicio pide ``limit+1``
    filas al repo. Si vuelven más de ``limit``, descarta la extra y
    marca ``truncado=True`` para que la vista muestre un aviso. Así el
    caller sabe si está viendo todo el rango o solo la cabeza.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

from core.models.asistencia import Asistencia
from core.repositories.asistencia_repository import (
    IAsistenciaReadRepository,
    IAsistenciaWriteRepository,
)
from core.repositories.empleado_repository import IEmpleadoReadRepository
from core.repositories.turno_repository import ITurnoReadRepository
from core.services.audit_logger import AuditLogger
from core.services.consolidacion_service import ConsolidacionService
from core.services.errors import AsistenciaNotFoundError
from core.services.sincronizacion_result import ResultadoConsolidacion


@dataclass
class AsistenciaVista:
    """DTO enriquecido para la vista de Asistencia (Sub-3.4b).

    Combina la fila base ``Asistencia`` con los nombres resueltos del
    empleado y el turno que se aplicó. La UI renderiza estos campos
    directamente — el servicio ya resolvió los ids.

    Atributos:
        asistencia: Fila base (se expone completa para que la vista
            pueda leer estado, horas, minutos y observaciones sin
            aplanar todo en el DTO).
        empleado_nombre_completo: ``"{apellidos} {nombres}"`` ya
            formateado. Si el empleado fue borrado físicamente (no
            debería pasar por las FKs ON DELETE RESTRICT, pero
            defensivo), queda ``"(empleado eliminado)"``.
        empleado_dni: DNI del empleado, si existe. ``None`` si no se
            resolvió (caso defensivo).
        turno_nombre: Nombre del turno aplicado, o ``None`` si la fila
            es SIN_TURNO / FERIADO (``turno_id_aplicado = NULL``) o si
            el turno fue borrado físicamente.
    """

    asistencia: Asistencia
    empleado_nombre_completo: str
    empleado_dni: Optional[str]
    turno_nombre: Optional[str]


@dataclass
class ResultadoBusquedaAsistencia:
    """Resultado de ``list_asistencias``.

    Atributos:
        items: Hasta ``limit`` filas enriquecidas.
        truncado: ``True`` si había más filas en el rango de las que
            se devolvieron — la vista muestra un aviso al usuario.
    """

    items: List[AsistenciaVista]
    truncado: bool


class AsistenciaService:
    """Servicio de la vista de Asistencia (Sub-3.4b)."""

    # Tope default de filas a pintar en la vista. La UI puede pedir
    # un valor distinto vía parámetro; 500 fue aprobado como balance
    # entre "muestra el mes entero de toda la muni" y "no explota el
    # frame scrollable de customtkinter".
    DEFAULT_LIMIT: int = 500

    def __init__(
        self,
        asistencia_read: IAsistenciaReadRepository,
        asistencia_write: IAsistenciaWriteRepository,
        empleado_read: IEmpleadoReadRepository,
        turno_read: ITurnoReadRepository,
        audit_logger: AuditLogger,
        consolidacion_service: ConsolidacionService,
    ) -> None:
        """Inicializa el servicio con sus 6 dependencias inyectadas."""
        self._asist_read = asistencia_read
        self._asist_write = asistencia_write
        self._emp_read = empleado_read
        self._turno_read = turno_read
        self._audit = audit_logger
        self._consolidacion = consolidacion_service
        self._log = logging.getLogger(self.__class__.__name__)

    # ── Reads ─────────────────────────────────────────────────────────────

    def list_asistencias(
        self,
        desde: str,
        hasta: str,
        empleado_id: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> ResultadoBusquedaAsistencia:
        """Lista asistencias en un rango, opcionalmente filtradas por empleado.

        Pide al repo ``limit+1`` filas para detectar truncamiento. Si
        el repo devuelve más de ``limit``, se descarta la extra y el
        resultado queda marcado como truncado.

        Args:
            desde: ISO ``YYYY-MM-DD``. Límite inferior inclusivo.
            hasta: ISO ``YYYY-MM-DD``. Límite superior inclusivo.
            empleado_id: Si se pasa, filtra al empleado indicado.
                ``None`` devuelve las asistencias de todos.
            limit: Tope de filas. ``None`` usa ``DEFAULT_LIMIT``.

        Returns:
            ``ResultadoBusquedaAsistencia`` con los DTOs enriquecidos.
        """
        tope = limit if limit is not None else self.DEFAULT_LIMIT
        # Pedimos N+1 para detectar truncamiento sin un segundo COUNT(*).
        filas_crudas = self._list_raw(desde, hasta, empleado_id, tope + 1)
        truncado = len(filas_crudas) > tope
        filas = filas_crudas[:tope] if truncado else filas_crudas

        nombres_empleados, dnis_empleados = self._mapear_empleados(filas)
        nombres_turnos = self._mapear_turnos(filas)

        items = [
            AsistenciaVista(
                asistencia=a,
                empleado_nombre_completo=nombres_empleados.get(
                    a.empleado_id, "(empleado eliminado)"
                ),
                empleado_dni=dnis_empleados.get(a.empleado_id),
                turno_nombre=(
                    nombres_turnos.get(a.turno_id_aplicado)
                    if a.turno_id_aplicado is not None
                    else None
                ),
            )
            for a in filas
        ]
        return ResultadoBusquedaAsistencia(items=items, truncado=truncado)

    def contar_asistencias_para_reporte(
        self,
        desde: str,
        hasta: str,
        empleado_id: Optional[int] = None,
    ) -> int:
        """Cuenta filas que devolverá ``list_asistencias_para_reporte``.

        Pensado para que la UI muestre una confirmación previa al export
        cuando el rango es grande, sin tener que materializar la lista.

        Args:
            desde: ISO ``YYYY-MM-DD``. Inclusivo.
            hasta: ISO ``YYYY-MM-DD``. Inclusivo.
            empleado_id: Si se pasa, cuenta solo filas de ese empleado.

        Returns:
            Conteo entero >= 0.
        """
        return self._asist_read.count_by_rango(desde=desde, hasta=hasta, empleado_id=empleado_id)

    def list_asistencias_para_reporte(
        self,
        desde: str,
        hasta: str,
        empleado_id: Optional[int] = None,
    ) -> List[AsistenciaVista]:
        """Lista TODAS las asistencias del rango (sin truncamiento).

        Igual a ``list_asistencias`` pero sin la lógica de ``limit+1``
        ni ``DEFAULT_LIMIT``: el caller (``ReporteService``) necesita
        materializar el rango completo para escribirlo a Excel. La UI
        presenta un conteo previo (``count_by_rango``) para que el
        usuario confirme exportes muy grandes.

        Args:
            desde: ISO ``YYYY-MM-DD``. Inclusivo.
            hasta: ISO ``YYYY-MM-DD``. Inclusivo.
            empleado_id: Si se pasa, filtra al empleado indicado.
                ``None`` devuelve las asistencias de todos.

        Returns:
            Lista de ``AsistenciaVista`` enriquecidas, ordenadas por
            (fecha asc, empleado_id asc) si no hay filtro o
            (fecha asc) si hay filtro de empleado.
        """
        if empleado_id is not None:
            filas = self._asist_read.list_by_empleado_y_rango(empleado_id, desde, hasta, limit=None)
        else:
            filas = self._asist_read.list_by_rango(desde, hasta, limit=None)

        nombres_empleados, dnis_empleados = self._mapear_empleados(filas)
        nombres_turnos = self._mapear_turnos(filas)

        return [
            AsistenciaVista(
                asistencia=a,
                empleado_nombre_completo=nombres_empleados.get(
                    a.empleado_id, "(empleado eliminado)"
                ),
                empleado_dni=dnis_empleados.get(a.empleado_id),
                turno_nombre=(
                    nombres_turnos.get(a.turno_id_aplicado)
                    if a.turno_id_aplicado is not None
                    else None
                ),
            )
            for a in filas
        ]

    def primera_fecha(self, empleado_id: Optional[int] = None) -> Optional[str]:
        """Fecha ISO de la asistencia más antigua calculada (``None`` si no hay)."""
        return self._asist_read.primera_fecha(empleado_id)

    def list_empleados_para_filtro(self) -> List[Tuple[int, str]]:
        """Devuelve ``[(empleado_id, "apellidos nombres")]`` para el combo.

        Solo empleados activos. Orden estable por apellidos + nombres.
        """
        empleados = self._emp_read.list_active()
        tuplas = [
            (emp.id, f"{emp.apellidos} {emp.nombres}".strip())
            for emp in empleados
            if emp.id is not None
        ]
        tuplas.sort(key=lambda t: t[1].lower())
        return tuplas

    # ── Writes ────────────────────────────────────────────────────────────

    def re_consolidar(
        self,
        desde: str,
        hasta: str,
        empleado_id: Optional[int],
        actor_user_id: int,
    ) -> ResultadoConsolidacion:
        """Re-consolida un rango (opcionalmente filtrado por empleado).

        Delega a ``ConsolidacionService.consolidar_rango`` y registra la
        acción del usuario en ``audit_log`` con el id del actor (el
        servicio de consolidación ya audita el hecho técnico, pero sin
        actor porque también lo dispara el scheduler — acá capturamos
        quién pidió el trigger manual desde la UI).

        Args:
            desde: ISO ``YYYY-MM-DD`` inclusivo.
            hasta: ISO ``YYYY-MM-DD`` inclusivo, >= desde.
            empleado_id: ``None`` = todos los activos; un id = solo ese
                empleado (debe estar activo).
            actor_user_id: Usuario que disparó la re-consolidación.

        Returns:
            ``ResultadoConsolidacion`` del servicio subyacente.

        Raises:
            InvalidRangoError, InvalidDateError, EmpleadoNotFoundError:
                propagadas desde ``ConsolidacionService``.
        """
        resultado = self._consolidacion.consolidar_rango(desde, hasta, empleado_id)
        self._audit.log(
            action="asistencia_re_consolidada",
            user_id=actor_user_id,
            details=(
                f'{{"desde": "{desde}", "hasta": "{hasta}", '
                f'"empleado_id": {empleado_id if empleado_id is not None else "null"}, '
                f'"asistencias_upsertadas": {resultado.asistencias_upsertadas}}}'
            ),
        )
        self._log.info(
            "Re-consolidación manual: desde=%s hasta=%s empleado_id=%s actor=%s upsertadas=%s",
            desde,
            hasta,
            empleado_id,
            actor_user_id,
            resultado.asistencias_upsertadas,
        )
        return resultado

    def update_observaciones(
        self,
        asistencia_id: int,
        observaciones: Optional[str],
        actor_user_id: int,
    ) -> None:
        """Actualiza ``observaciones`` y registra la acción en audit_log.

        El repo valida que el id exista (lanza ``ValueError`` si no);
        lo traducimos a ``AsistenciaNotFoundError`` — excepción
        específica de dominio más informativa para el caller.

        Args:
            asistencia_id: PK de la fila a actualizar.
            observaciones: Texto libre o ``None`` para limpiar. El
                servicio normaliza cadenas vacías / solo espacios a
                ``None`` para no ensuciar la BD.
            actor_user_id: Usuario que realiza la acción (se registra
                en ``audit_log``).

        Raises:
            AsistenciaNotFoundError: Si el id no existe.
        """
        valor_normalizado = self._normalizar_observaciones(observaciones)
        try:
            self._asist_write.update_observaciones(asistencia_id, valor_normalizado)
        except ValueError as exc:
            raise AsistenciaNotFoundError(asistencia_id) from exc
        self._audit.log(
            action="asistencia_observaciones_updated",
            user_id=actor_user_id,
            details=f'{{"asistencia_id": {asistencia_id}}}',
        )
        self._log.info(
            "Observaciones actualizadas: asistencia_id=%s actor=%s",
            asistencia_id,
            actor_user_id,
        )

    # ── Helpers privados ──────────────────────────────────────────────────

    def _list_raw(
        self,
        desde: str,
        hasta: str,
        empleado_id: Optional[int],
        limit: int,
    ) -> List[Asistencia]:
        """Delega al read-repo con o sin filtro de empleado."""
        if empleado_id is not None:
            return self._asist_read.list_by_empleado_y_rango(empleado_id, desde, hasta, limit=limit)
        return self._asist_read.list_by_rango(desde, hasta, limit=limit)

    def _mapear_empleados(
        self, filas: List[Asistencia]
    ) -> Tuple[dict[int, str], dict[int, Optional[str]]]:
        """Un solo viaje al repo de empleados para resolver nombres + DNIs.

        Devuelve dos dicts indexados por ``empleado_id``:
            - id -> "apellidos nombres"
            - id -> dni (o None si el empleado fue borrado físicamente)
        """
        ids_unicos = {a.empleado_id for a in filas}
        nombres: dict[int, str] = {}
        dnis: dict[int, Optional[str]] = {}
        for emp_id in ids_unicos:
            emp = self._emp_read.get_by_id(emp_id)
            if emp is None:
                continue
            nombres[emp_id] = f"{emp.apellidos} {emp.nombres}".strip()
            dnis[emp_id] = emp.dni
        return nombres, dnis

    def _mapear_turnos(self, filas: List[Asistencia]) -> dict[int, str]:
        """Un solo viaje al repo de turnos para resolver nombres."""
        ids_unicos = {a.turno_id_aplicado for a in filas if a.turno_id_aplicado is not None}
        nombres: dict[int, str] = {}
        for turno_id in ids_unicos:
            turno = self._turno_read.get_by_id(turno_id)
            if turno is None:
                continue
            nombres[turno_id] = turno.nombre
        return nombres

    @staticmethod
    def _normalizar_observaciones(valor: Optional[str]) -> Optional[str]:
        """``None`` / vacío / solo espacios → ``None``. Resto → ``strip()``."""
        if valor is None:
            return None
        limpio = valor.strip()
        return limpio if limpio else None
