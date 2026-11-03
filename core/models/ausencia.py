"""Modelo de dominio: Ausencia (vacaciones, día libre, permiso, incapacidad).

Una ausencia es un rango de fechas (inclusivo) en el que el empleado NO
debe marcar porque tiene una razón aprobada. La consolidación pone en
esos días el estado de asistencia del mismo nombre en vez de ``AUSENTE``.

Reglas que aplica ``AusenciaService``:
    - ``fecha_hasta >= fecha_desde`` (un solo día: ambas iguales).
    - Las ausencias de un mismo empleado no se cruzan entre sí.
    - Los feriados y los días que el turno no cubre (p. ej. domingo) NO
      cuentan como días de ausencia: ganan ``FERIADO`` / ``SIN_TURNO``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Final, FrozenSet, Optional


class TipoAusencia(str, Enum):
    """Catálogo cerrado de motivos de ausencia.

    Cada valor coincide con un estado de ``EstadoAsistencia`` para que la
    consolidación pueda copiarlo tal cual.
    """

    VACACIONES = "VACACIONES"
    DIA_LIBRE = "DIA_LIBRE"
    PERMISO = "PERMISO"
    INCAPACIDAD = "INCAPACIDAD"


ALL_TIPOS_AUSENCIA: Final[FrozenSet[str]] = frozenset(t.value for t in TipoAusencia)

# Nombre para mostrar de cada tipo (también se usa en mensajes de error).
TIPO_AUSENCIA_TEXTO: Final[Dict[str, str]] = {
    TipoAusencia.VACACIONES.value: "Vacaciones",
    TipoAusencia.DIA_LIBRE.value: "Día libre",
    TipoAusencia.PERMISO.value: "Permiso",
    TipoAusencia.INCAPACIDAD.value: "Incapacidad",
}


@dataclass(frozen=True)
class Ausencia:
    """Rango de días en que un empleado no trabaja por una razón aprobada.

    Atributos:
        id: PK en ``ausencias``. ``None`` antes de persistir.
        empleado_id: FK a ``empleados``.
        tipo: Uno de ``TipoAusencia.*.value``.
        fecha_desde: Primer día ``"YYYY-MM-DD"``.
        fecha_hasta: Último día ``"YYYY-MM-DD"`` (inclusivo).
        nota: Texto libre opcional (motivo, número de constancia…).
        creado_por_user_id: Usuario que la registró (``None`` si se borró).
        creado_en: Timestamp ISO-8601 UTC del registro.
    """

    id: Optional[int]
    empleado_id: int
    tipo: str
    fecha_desde: str
    fecha_hasta: str
    nota: Optional[str] = None
    creado_por_user_id: Optional[int] = None
    creado_en: Optional[str] = None

    def cubre(self, fecha_iso: str) -> bool:
        """``True`` si la fecha cae dentro del rango (comparación ISO)."""
        return self.fecha_desde <= fecha_iso <= self.fecha_hasta

    def se_cruza_con(self, desde: str, hasta: str) -> bool:
        """``True`` si el rango comparte al menos un día con ``[desde, hasta]``."""
        return self.fecha_desde <= hasta and desde <= self.fecha_hasta

    @property
    def tipo_texto(self) -> str:
        """Nombre para mostrar (``"Vacaciones"``, ``"Día libre"``…)."""
        return TIPO_AUSENCIA_TEXTO.get(self.tipo, self.tipo)
