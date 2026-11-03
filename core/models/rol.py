"""Modelo de dominio: Rol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet


@dataclass(frozen=True)
class Rol:
    """Rol del sistema con su set inmutable de permisos.

    Se construye a partir de una fila de la tabla ``roles``. El campo
    ``permissions`` se obtiene deserializando el JSON de ``permissions_json``
    y envolviéndolo en un ``frozenset`` para que no pueda mutarse en tiempo
    de ejecución (los repositorios devuelven instancias ``frozen``).

    Atributos:
        id: Clave primaria en la tabla ``roles``.
        code: Código canónico (SUPERADMIN, ADMIN, REPORTES, OPERADOR).
        name: Nombre legible para mostrar en UI.
        description: Descripción del propósito del rol.
        permissions: Set inmutable de códigos de permiso que este rol posee.
    """

    id: int
    code: str
    name: str
    description: str
    permissions: FrozenSet[str]
