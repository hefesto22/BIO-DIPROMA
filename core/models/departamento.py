"""Modelo de dominio: Departamento."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Departamento:
    """Departamento de la municipalidad (catálogo).

    Diseño: catálogo simple con archivado vía ``is_active`` (Decisión 6 del
    PRD de Fase 2 — sin DELETE físico). El nombre es único a nivel BD.

    ``Departamento`` NO es ``frozen`` porque la UI de edición muta el nombre
    antes de persistir. Si en el futuro se necesita pasar una instancia
    inmutable a otro servicio, crear un nuevo objeto en vez de mutarlo.

    Atributos:
        id: PK en la tabla ``departamentos``. ``None`` si aún no fue persistido.
        nombre: Texto único, case-sensitive. Visible al usuario.
        is_active: ``False`` archiva el departamento — deja de aparecer en
            dropdowns de alta/edición pero preserva FKs históricas.
    """

    id: Optional[int]
    nombre: str
    is_active: bool = True
