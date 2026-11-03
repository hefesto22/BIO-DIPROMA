"""Modelo de dominio: Feriado.

Día no laborable reconocido por la municipalidad. Al consolidar asistencia,
una fecha marcada como feriado genera filas con estado ``FERIADO`` — los
empleados no se reportan como AUSENTE ese día aunque no haya marcadas en
el reloj.

Los feriados se administran desde la UI de Configuración (permiso
``MANAGE_SETTINGS``) como una tabla plana editable. No se vincula con
turnos: un feriado aplica a TODOS los empleados, independientemente del
turno que tengan asignado. Si en el futuro la municipalidad necesita
feriados parciales (p.ej. "solo administrativos"), se migra el modelo
— por ahora YAGNI.

La fecha se almacena como ISO ``YYYY-MM-DD`` (texto) por simetría con el
resto del esquema; la unicidad por fecha la garantiza la BD.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Feriado:
    """Día feriado reconocido por la municipalidad.

    Atributos:
        id: PK en la tabla ``feriados``. ``None`` si aún no fue persistido.
        fecha: Fecha del feriado en formato ISO ``YYYY-MM-DD``. Única en BD.
        descripcion: Nombre legible del feriado. Ejemplos:
            "Día de la Independencia", "Semana Morazánica — Lunes".
    """

    id: Optional[int]
    fecha: str
    descripcion: str
