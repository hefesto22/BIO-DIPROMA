"""Modelo de dominio: Empleado.

Incluye el enum ``MotivoBaja`` (catálogo cerrado de causas de desactivación)
porque está acoplado semánticamente al ciclo de vida del empleado: no hay
otro caso de uso para ``MotivoBaja`` fuera de ``Empleado``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final, FrozenSet, Optional


class MotivoBaja(str, Enum):
    """Catálogo cerrado de motivos para desactivar un empleado (Decisión 2).

    Se hereda de ``str`` para que las comparaciones directas contra el
    valor persistido en BD (TEXT) sean naturales:

        >>> MotivoBaja.DESPIDO == "DESPIDO"
        True
    """

    DESPIDO = "DESPIDO"
    RENUNCIA = "RENUNCIA"
    JUBILACION = "JUBILACION"
    FIN_CONTRATO = "FIN_CONTRATO"
    FALLECIMIENTO = "FALLECIMIENTO"
    OTRO = "OTRO"


ALL_MOTIVOS_BAJA: Final[FrozenSet[str]] = frozenset(motivo.value for motivo in MotivoBaja)


@dataclass
class Empleado:
    """Empleado de la municipalidad.

    Diseño (Decisiones 1, 2, 7 del PRD de Fase 2):
        - Identidad natural = ``dni`` (hondureño, formato ``XXXX-XXXX-XXXXX``,
          validado por el servicio).
        - PK estable = ``id`` autoincrementado (invisible al usuario).
        - ``zkteco_id`` separado y nullable: permite dar de alta antes de
          registrar al empleado en el reloj.
        - Archivado vía ``is_active`` + ``fecha_baja`` + ``motivo_baja`` +
          ``nota_baja`` (Decisión 2).

    ``Empleado`` NO es ``frozen`` — los flujos de edición (cambio de
    departamento, actualización de teléfono, archivado) lo mutan antes
    de persistir.

    Invariantes validadas por la BD (CHECK) y por el servicio:
        - Si ``is_active = True``, los tres campos de baja deben ser ``None``.
        - Si ``is_active = False``, ``fecha_baja`` y ``motivo_baja`` son
          obligatorios; ``nota_baja`` es obligatoria únicamente si
          ``motivo_baja == "OTRO"``.

    Atributos:
        id: PK en la tabla ``empleados``. ``None`` si aún no fue persistido.
        dni: Identidad natural hondureña. Formato ``XXXX-XXXX-XXXXX``, único.
        nombres: Nombres del empleado. Texto libre.
        apellidos: Apellidos del empleado. Texto libre.
        departamento_id: FK a ``departamentos(id)``.
        cargo_id: FK a ``cargos(id)``.
        fecha_ingreso: Fecha de alta en la municipalidad. ISO ``YYYY-MM-DD``.
        telefono: Opcional. Texto libre.
        email: Opcional. Texto libre.
        zkteco_id: Opcional. ID asignado en el reloj ZKTeco. Único si se
            asigna; múltiples empleados pueden tener ``None`` simultáneamente
            (semántica ``NULL != NULL`` de SQLite).
        is_active: ``False`` desactiva sin borrar.
        fecha_baja: ISO ``YYYY-MM-DD``. Se llena al desactivar.
        motivo_baja: Uno de ``MotivoBaja.*.value``. Se llena al desactivar.
        nota_baja: Detalle libre. Obligatoria si ``motivo_baja == "OTRO"``.
        created_at: ISO-8601 UTC con precisión de segundos.
        updated_at: ISO-8601 UTC con precisión de segundos.
    """

    id: Optional[int]
    dni: str
    nombres: str
    apellidos: str
    departamento_id: int
    cargo_id: int
    fecha_ingreso: str
    telefono: Optional[str] = None
    email: Optional[str] = None
    zkteco_id: Optional[int] = None
    is_active: bool = True
    fecha_baja: Optional[str] = None
    motivo_baja: Optional[str] = None
    nota_baja: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
