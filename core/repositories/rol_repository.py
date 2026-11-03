"""Interfaz del repositorio de Rol.

Solo ``IRolReadRepository``: los 4 roles canónicos se insertan por el
seed de ``002_auth.sql`` y la aplicación no crea/edita/borra roles en
runtime. Si en el futuro se requiere CRUD de roles, se agregará una
interfaz ``IRolWriteRepository`` separada (ISP).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from core.models.rol import Rol


class IRolReadRepository(ABC):
    """Operaciones de solo lectura sobre la tabla ``roles``."""

    @abstractmethod
    def get_by_id(self, role_id: int) -> Optional[Rol]:
        """Devuelve el rol con ese id, o ``None`` si no existe."""

    @abstractmethod
    def get_by_code(self, code: str) -> Optional[Rol]:
        """Devuelve el rol con ese código canónico.

        ``code`` es case-sensitive (SUPERADMIN, ADMIN, REPORTES, OPERADOR).
        """

    @abstractmethod
    def list_all(self) -> List[Rol]:
        """Devuelve todos los roles ordenados por ``id`` ascendente.

        El orden coincide con el orden de creación en el seed
        (SUPERADMIN, ADMIN, REPORTES, OPERADOR).
        """
