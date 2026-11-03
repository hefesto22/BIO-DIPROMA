"""Interfaces del repositorio de Usuario.

Se separan en ``IUsuarioReadRepository`` e ``IUsuarioWriteRepository``
siguiendo ISP (regla SOLID-I del proyecto): un consumidor que solo
necesita lectura (p. ej. la UI que lista usuarios) no debe depender
del contrato de escritura.

Ambas interfaces son ``abc.ABC`` y no ``typing.Protocol`` porque:
    - El proyecto usa inyección explícita en constructores (SOLID-D).
    - Es trivial crear mocks heredando y sobreescribiendo métodos.
    - mypy --strict valida firma exacta en las implementaciones.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from core.models.usuario import Usuario


class IUsuarioReadRepository(ABC):
    """Operaciones de solo lectura sobre la tabla ``usuarios``."""

    @abstractmethod
    def get_by_id(self, user_id: int) -> Optional[Usuario]:
        """Devuelve el usuario con ese id, o ``None`` si no existe."""

    @abstractmethod
    def get_by_username(self, username: str) -> Optional[Usuario]:
        """Devuelve el usuario con ese username, o ``None`` si no existe.

        La búsqueda es case-sensitive (consistente con el UNIQUE de la tabla).
        """

    @abstractmethod
    def list_all(self) -> List[Usuario]:
        """Devuelve todos los usuarios ordenados por ``username`` ascendente."""

    @abstractmethod
    def count_by_role(self, role_id: int) -> int:
        """Devuelve cuántos usuarios tienen asignado ese rol.

        Útil para validar invariantes antes de mutar (p. ej. "¿ya hay un
        SUPERADMIN?" sin golpear el trigger SQL).
        """


class IUsuarioWriteRepository(ABC):
    """Operaciones de escritura sobre la tabla ``usuarios``."""

    @abstractmethod
    def create(self, usuario: Usuario) -> Usuario:
        """Inserta un usuario nuevo.

        Args:
            usuario: Instancia con ``id=None``. ``created_at`` / ``updated_at``
                pueden ser ``None``; si lo son, el repo los rellena con el
                timestamp UTC actual.

        Returns:
            Nueva instancia ``Usuario`` con ``id``, ``created_at`` y
            ``updated_at`` ya asignados.

        Raises:
            sqlite3.IntegrityError: Si ``username`` ya existe o el trigger
                de SUPERADMIN único aborta la inserción.
        """

    @abstractmethod
    def update_login_state(
        self,
        user_id: int,
        failed_attempts: int,
        locked_until: Optional[str],
    ) -> None:
        """Actualiza solo los campos mutables del flujo de login.

        Se exponen únicamente estos dos campos (no un ``update`` genérico)
        para que AuthService no pueda, por error, escribir otros campos
        sensibles como ``password_hash`` o ``role_id``.
        """

    @abstractmethod
    def update_password_hash(self, user_id: int, new_hash: str) -> None:
        """Cambia el hash bcrypt del usuario.

        El llamador es responsable de haber validado la complejidad y
        generado el hash — el repo solo persiste.
        """

    @abstractmethod
    def delete(self, user_id: int) -> None:
        """Borra físicamente el usuario.

        Nota: para desactivar temporalmente sin perder historial, usar el
        flag ``is_active`` vía ``update_profile``. ``delete`` queda solo
        como herramienta de mantenimiento (ej. limpiar usuarios de prueba
        antes de poner en producción) — la UI usa ``update_profile`` con
        ``is_active=False`` para soft-delete.
        """

    @abstractmethod
    def update_profile(
        self,
        user_id: int,
        full_name: str,
        role_id: int,
        is_active: bool,
    ) -> None:
        """Actualiza los campos editables desde la UI de administración.

        Expone EXACTAMENTE estos 3 campos para que el módulo de admin
        no pueda, por error, escribir ``password_hash``, ``failed_attempts``
        o ``locked_until``. Esos campos tienen métodos dedicados
        (``update_password_hash``, ``update_login_state``, ``unlock_account``).

        Args:
            user_id: PK del usuario a actualizar.
            full_name: Nuevo nombre completo (no vacío — service valida).
            role_id: Nuevo rol asignado. Debe existir en ``roles``.
            is_active: ``True`` activa la cuenta, ``False`` la desactiva.

        Raises:
            sqlite3.IntegrityError: Si ``role_id`` viola la FK o si el
                trigger ``trg_enforce_single_superadmin_update`` aborta
                la operación (ya hay un SUPERADMIN distinto).
        """

    @abstractmethod
    def unlock_account(self, user_id: int) -> None:
        """Desbloquea una cuenta que se bloqueó por intentos fallidos.

        Resetea ``failed_attempts = 0`` y ``locked_until = NULL``. Útil
        cuando el operador llama por teléfono al admin diciendo
        "olvidé mi contraseña, me bloqueé tras varios intentos".
        Idempotente: invocarlo sobre una cuenta ya desbloqueada es no-op.
        """
