"""Modelo de dominio: EmpleadoTurno (asignación de turno con historial)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class EmpleadoTurno:
    """Asignación de un turno a un empleado durante un rango de fechas.

    Diseño (Decisión 5 del PRD de Fase 2): tabla intermedia con
    ``fecha_inicio`` y ``fecha_fin`` para preservar el historial de cambios
    de turno. Al cambiar el turno de un empleado, el servicio ejecuta una
    transacción atómica: cierra la asignación vigente (seteando
    ``fecha_fin``) y crea una nueva con la ``fecha_inicio`` correspondiente.

    Invariante de negocio (validada por el servicio y por un índice parcial
    único a nivel BD — ver ``003_empleados_turnos.sql``):

        Cada empleado tiene A LO SUMO una fila con ``fecha_fin = None``
        (su turno vigente).

    ``EmpleadoTurno`` NO es ``frozen``. El flujo típico es crear la fila
    vigente con ``fecha_fin = None`` y luego mutarla para cerrarla al
    momento del cambio.

    Atributos:
        id: PK en la tabla ``empleado_turnos``. ``None`` si aún no fue
            persistido.
        empleado_id: FK a ``empleados(id)``.
        turno_id: FK a ``turnos(id)``.
        fecha_inicio: Fecha efectiva de inicio de la asignación. ISO
            ``YYYY-MM-DD``.
        fecha_fin: Fecha efectiva de fin de la asignación (inclusive) o
            ``None`` si la asignación está vigente.
    """

    id: Optional[int]
    empleado_id: int
    turno_id: int
    fecha_inicio: str
    fecha_fin: Optional[str] = None
