"""Servicio de feriados: días no laborables para todos los empleados.

Al consolidar, una fecha feriada genera ``FERIADO`` en vez de ``AUSENTE``
y no cuenta como día de vacaciones. Si se registra, cambia o borra un
feriado de una fecha que ya pasó (o de hoy), se recalcula la asistencia
de ese día para que la lista lo refleje al momento.

Reglas:
    - Fecha ISO válida y única (``FeriadoDuplicadoError``).
    - Descripción obligatoria (trim + espacios colapsados), hasta 120 caracteres.

Diseño (SOLID): solo gestiona feriados; la consolidación la hace
``ConsolidacionService`` (inyectado, opcional). Auditado.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Callable, List, Optional, Tuple

from core.models.feriado import Feriado
from core.repositories.asistencia_repository import IAsistenciaReadRepository
from core.repositories.feriado_repository import (
    IFeriadoReadRepository,
    IFeriadoWriteRepository,
)
from core.services.audit_logger import AuditLogger
from core.services.consolidacion_service import ConsolidacionService
from core.services.errors import FeriadoDuplicadoError, FeriadoNotFoundError
from core.services.validators import normalize_nombre, validate_fecha_iso

_MAX_DESCRIPCION = 120


class FeriadoService:
    """CRUD de feriados con auditoría y recálculo de asistencia."""

    def __init__(
        self,
        feriado_read: IFeriadoReadRepository,
        feriado_write: IFeriadoWriteRepository,
        audit_logger: AuditLogger,
        consolidacion_service: Optional[ConsolidacionService] = None,
        hoy: Callable[[], date] = date.today,
        asistencia_read: Optional[IAsistenciaReadRepository] = None,
    ) -> None:
        """Inyecta dependencias.

        Args:
            consolidacion_service: Si se pasa, al cambiar un feriado de una
                fecha pasada se recalcula la asistencia de ese día.
            asistencia_read: Si se pasa, solo se recalculan los días que ya
                tienen asistencia (no se crean filas en meses sin datos).
        """
        self._asist_read = asistencia_read
        self._read = feriado_read
        self._write = feriado_write
        self._audit = audit_logger
        self._consolidacion = consolidacion_service
        self._hoy = hoy
        self._log = logging.getLogger(self.__class__.__name__)

    # ── Lecturas ──────────────────────────────────────────────────────────

    def listar(self, anio: Optional[int] = None) -> List[Feriado]:
        """Feriados del año (o todos), en orden de fecha."""
        if anio is None:
            return self._read.list_all()
        return self._read.list_by_rango(f"{anio:04d}-01-01", f"{anio:04d}-12-31")

    def get(self, feriado_id: int) -> Feriado:
        feriado = self._read.get_by_id(feriado_id)
        if feriado is None:
            raise FeriadoNotFoundError(feriado_id)
        return feriado

    # ── Escrituras ────────────────────────────────────────────────────────

    def crear(self, fecha: str, descripcion: str, actor_user_id: int) -> Feriado:
        """Registra un feriado.

        Raises:
            MissingRequiredFieldError / InvalidDateError: datos inválidos.
            FeriadoDuplicadoError: ya hay un feriado esa fecha.
        """
        fecha_iso = validate_fecha_iso(fecha, "fecha").isoformat()
        texto = _normalizar_descripcion(descripcion)
        existente = self._read.get_by_fecha(fecha_iso)
        if existente is not None:
            raise FeriadoDuplicadoError(fecha_iso, existente.descripcion)
        creado = self._write.create(Feriado(id=None, fecha=fecha_iso, descripcion=texto))
        self._auditar("feriado_creado", actor_user_id, creado)
        self._reconsolidar([fecha_iso])
        return creado

    def actualizar(
        self, feriado_id: int, fecha: str, descripcion: str, actor_user_id: int
    ) -> Feriado:
        """Cambia la descripción y, si hace falta, la fecha del feriado."""
        actual = self.get(feriado_id)
        fecha_iso = validate_fecha_iso(fecha, "fecha").isoformat()
        texto = _normalizar_descripcion(descripcion)
        if fecha_iso == actual.fecha:
            self._write.update_descripcion(feriado_id, texto)
            nuevo = Feriado(id=feriado_id, fecha=fecha_iso, descripcion=texto)
            self._auditar("feriado_actualizado", actor_user_id, nuevo)
            return nuevo
        otro = self._read.get_by_fecha(fecha_iso)
        if otro is not None:
            raise FeriadoDuplicadoError(fecha_iso, otro.descripcion)
        # La fecha es la "clave" del feriado: se reemplaza el registro.
        self._write.delete(feriado_id)
        nuevo = self._write.create(Feriado(id=None, fecha=fecha_iso, descripcion=texto))
        self._auditar("feriado_actualizado", actor_user_id, nuevo)
        self._reconsolidar([actual.fecha, fecha_iso])
        return nuevo

    def cargar_nacionales(self, anio: int, actor_user_id: int) -> List[Feriado]:
        """Agrega los feriados nacionales de fecha fija del año (omite los que ya están).

        Returns:
            Los feriados que se agregaron (vacío si ya estaban todos).
        """
        agregados: List[Feriado] = []
        for fecha, descripcion in feriados_nacionales(anio):
            if self._read.get_by_fecha(fecha) is not None:
                continue
            creado = self._write.create(Feriado(id=None, fecha=fecha, descripcion=descripcion))
            self._auditar("feriado_creado", actor_user_id, creado)
            agregados.append(creado)
        self._reconsolidar([f.fecha for f in agregados])
        return agregados

    def eliminar(self, feriado_id: int, actor_user_id: int) -> None:
        actual = self.get(feriado_id)
        self._write.delete(feriado_id)
        self._auditar("feriado_eliminado", actor_user_id, actual)
        self._reconsolidar([actual.fecha])

    # ── Helpers ───────────────────────────────────────────────────────────

    def _auditar(self, accion: str, actor_user_id: int, f: Feriado) -> None:
        self._audit.log(
            action=accion,
            user_id=actor_user_id,
            details=json.dumps(
                {"id": f.id, "fecha": f.fecha, "descripcion": f.descripcion}, ensure_ascii=False
            ),
        )
        self._log.info("%s %s %s", accion, f.fecha, f.descripcion)

    def _reconsolidar(self, fechas: List[str]) -> None:
        """Recalcula la asistencia de las fechas que ya pasaron (o son hoy)."""
        if self._consolidacion is None:
            return
        hoy = self._hoy().isoformat()
        for fecha in sorted(set(fechas)):
            if fecha > hoy:
                continue
            if self._asist_read is not None and self._asist_read.count_by_rango(fecha, fecha) == 0:
                continue  # día todavía sin calcular: lo hará la próxima sincronización
            self._consolidacion.consolidar_rango(fecha, fecha)


def _normalizar_descripcion(descripcion: str) -> str:
    return normalize_nombre(descripcion, "descripcion")[:_MAX_DESCRIPCION]


def domingo_de_pascua(anio: int) -> date:
    """Domingo de Resurrección (algoritmo gregoriano anónimo)."""
    a = anio % 19
    b, c = divmod(anio, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    mes, dia = divmod(h + l_ - 7 * m + 114, 31)
    return date(anio, mes, dia + 1)


def feriados_nacionales(anio: int) -> List[Tuple[str, str]]:
    """Feriados del art. 339 del Código del Trabajo que NO se trasladan.

    El 14 de abril y los del 3, 12 y 21 de octubre (Semana Morazánica) se
    mueven cada año por decreto (Decreto 50-2003): no se incluyen, hay que
    agregarlos con la fecha que publique el Gobierno.
    """
    pascua = domingo_de_pascua(anio)
    return sorted(
        [
            (date(anio, 1, 1).isoformat(), "Año Nuevo"),
            ((pascua - timedelta(days=3)).isoformat(), "Jueves Santo"),
            ((pascua - timedelta(days=2)).isoformat(), "Viernes Santo"),
            ((pascua - timedelta(days=1)).isoformat(), "Sábado Santo"),
            (date(anio, 5, 1).isoformat(), "Día del Trabajo"),
            (date(anio, 9, 15).isoformat(), "Día de la Independencia"),
            (date(anio, 12, 25).isoformat(), "Navidad"),
        ]
    )
