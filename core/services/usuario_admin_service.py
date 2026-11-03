"""Servicio de administración de usuarios (Sub-2.7a).

Orquesta el CRUD de usuarios + reset de password + desbloqueo de cuenta
desde la UI de "Usuarios y roles". NO se confunde con ``AuthService``
(que solo verifica credenciales en login) ni con ``SetupWizardService``
(que solo crea el primer SUPERADMIN en el first-run).

Reglas de seguridad obligatorias (R1-R6):
    R1. ADMIN no puede crear/editar a alguien con rol SUPERADMIN.
    R2. Un usuario NO puede desactivarse a sí mismo.
    R3. NO se puede desactivar al ÚNICO SUPERADMIN del sistema.
    R4. ADMIN puede resetear password de cualquiera EXCEPTO SUPERADMIN.
    R5. NO se puede cambiar el propio rol.
    R6. Cada mutación escribe en audit_log con timestamp + actor.

Diseño (SOLID):
    - **S**: solo gestiona el lifecycle administrativo de usuarios.
    - **D**: recibe repos + hasher + policy + audit_logger por inyección.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from typing import List, Optional

from core.models import permissions as perms
from core.models.rol import Rol
from core.models.usuario import Usuario
from core.repositories.rol_repository import IRolReadRepository
from core.repositories.usuario_repository import (
    IUsuarioReadRepository,
    IUsuarioWriteRepository,
)
from core.services.audit_logger import AuditLogger
from core.services.errors import (
    CannotAssignSuperadminRoleError,
    CannotChangeSelfRoleError,
    CannotDeactivateOnlySuperadminError,
    CannotDeactivateSelfError,
    CannotResetSuperadminPasswordError,
    DuplicateUsernameError,
    InvalidUsernameError,
    UsuarioNotFoundError,
)
from core.services.password_policy import PasswordPolicy
from infrastructure.security.bcrypt_hasher import BcryptHasher

# Username: 3-32 chars, ASCII alfanumérico + . _ -
# Restrictivo a propósito: usernames se loguean en audit y aparecen en UI.
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
# Full name: no vacío, máximo 100 chars (cabe en cualquier label).
_FULL_NAME_MAX = 100


@dataclass(frozen=True)
class UsuarioConRol:
    """Vista de lista — usuario + nombre del rol resuelto.

    Evita N+1 queries en la UI: el service hace un solo pase por roles,
    construye un mapa ``{role_id: nombre}`` y arma esta tupla. La UI
    consume directamente sin más lookups.
    """

    usuario: Usuario
    rol_code: str
    rol_name: str


class UsuarioAdminService:
    """CRUD de usuarios desde el módulo "Usuarios y roles"."""

    def __init__(
        self,
        usuario_read: IUsuarioReadRepository,
        usuario_write: IUsuarioWriteRepository,
        rol_read: IRolReadRepository,
        hasher: BcryptHasher,
        password_policy: PasswordPolicy,
        audit_logger: AuditLogger,
    ) -> None:
        """Inicializa el servicio con todas sus dependencias inyectadas."""
        self._usuario_read = usuario_read
        self._usuario_write = usuario_write
        self._rol_read = rol_read
        self._hasher = hasher
        self._policy = password_policy
        self._audit = audit_logger
        self._log = logging.getLogger(self.__class__.__name__)

    # ── Lectura ───────────────────────────────────────────────────────────

    def list_usuarios(self, solo_activos: bool = False) -> List[UsuarioConRol]:
        """Devuelve todos los usuarios con su rol resuelto.

        Args:
            solo_activos: Si ``True``, filtra ``is_active=False``.
                Default ``False`` porque el módulo de admin típicamente
                quiere ver TODO incluyendo desactivados (con un toggle).
        """
        roles_por_id = {rol.id: rol for rol in self._rol_read.list_all()}
        resultado: List[UsuarioConRol] = []
        for u in self._usuario_read.list_all():
            if solo_activos and not u.is_active:
                continue
            rol = roles_por_id.get(u.role_id)
            rol_code = rol.code if rol else "UNKNOWN"
            rol_name = rol.name if rol else "(rol inexistente)"
            resultado.append(UsuarioConRol(usuario=u, rol_code=rol_code, rol_name=rol_name))
        return resultado

    def get_usuario(self, user_id: int) -> Usuario:
        """Devuelve un usuario por id.

        Raises:
            UsuarioNotFoundError: Si no existe.
        """
        u = self._usuario_read.get_by_id(user_id)
        if u is None:
            raise UsuarioNotFoundError(user_id)
        return u

    # ── Mutaciones ────────────────────────────────────────────────────────

    def create_usuario(
        self,
        username: str,
        full_name: str,
        role_id: int,
        password: str,
        actor_user_id: Optional[int],
        actor_role_code: str,
    ) -> Usuario:
        """Crea un usuario nuevo.

        Args:
            username: Nombre único, formato validado por ``_USERNAME_RE``.
            full_name: Nombre completo, no vacío.
            role_id: Rol a asignar. Si actor no es SUPERADMIN, no puede
                asignar SUPERADMIN (R1).
            password: Texto plano. Se valida con ``PasswordPolicy`` y se
                hashea con bcrypt antes de persistir.
            actor_user_id: Usuario que ejecuta (audit log).
            actor_role_code: Código del rol del actor — usado para R1.

        Raises:
            InvalidUsernameError, MissingRequiredFieldError, WeakPasswordError,
            DuplicateUsernameError, CannotAssignSuperadminRoleError.
        """
        username_norm = self._validar_username(username)
        full_name_norm = self._validar_full_name(full_name)
        rol_objetivo = self._get_rol_o_fallar(role_id)
        self._enforce_no_assign_superadmin(rol_objetivo.code, actor_role_code)
        self._policy.validate(password)
        if self._usuario_read.get_by_username(username_norm) is not None:
            raise DuplicateUsernameError(username_norm)

        password_hash = self._hasher.hash(password)
        try:
            creado = self._usuario_write.create(
                Usuario(
                    id=None,
                    username=username_norm,
                    password_hash=password_hash,
                    full_name=full_name_norm,
                    role_id=role_id,
                    is_active=True,
                )
            )
        except sqlite3.IntegrityError as exc:
            # El trigger SQL "SUPERADMIN único" o el UNIQUE de username
            # cayó. Mapear al error de dominio en español.
            mensaje = str(exc).lower()
            if "username" in mensaje:
                raise DuplicateUsernameError(username_norm) from exc
            if "superadmin" in mensaje:
                raise CannotAssignSuperadminRoleError() from exc
            raise

        assert creado.id is not None
        self._audit.log(
            action="usuario_created",
            user_id=actor_user_id,
            details=json.dumps(
                {
                    "id": creado.id,
                    "username": username_norm,
                    "role_id": role_id,
                },
                ensure_ascii=False,
            ),
        )
        self._log.info(
            "Usuario creado: id=%s username=%s role_id=%s actor=%s",
            creado.id,
            username_norm,
            role_id,
            actor_user_id,
        )
        return creado

    def update_usuario(
        self,
        user_id: int,
        full_name: str,
        role_id: int,
        actor_user_id: int,
        actor_role_code: str,
    ) -> None:
        """Actualiza nombre y rol de un usuario existente.

        NO toca password ni is_active (cada uno tiene su método dedicado).

        Raises:
            UsuarioNotFoundError, MissingRequiredFieldError,
            CannotAssignSuperadminRoleError, CannotChangeSelfRoleError.
        """
        actual = self.get_usuario(user_id)
        full_name_norm = self._validar_full_name(full_name)
        rol_objetivo = self._get_rol_o_fallar(role_id)

        # R5: no permitir cambiarse el propio rol.
        if user_id == actor_user_id and role_id != actual.role_id:
            raise CannotChangeSelfRoleError()

        # R1: el actor no SUPERADMIN no puede asignar el rol SUPERADMIN.
        if role_id != actual.role_id:
            self._enforce_no_assign_superadmin(rol_objetivo.code, actor_role_code)

        # No-op si nada relevante cambió.
        if full_name_norm == actual.full_name and role_id == actual.role_id:
            return

        try:
            self._usuario_write.update_profile(
                user_id=user_id,
                full_name=full_name_norm,
                role_id=role_id,
                is_active=actual.is_active,
            )
        except sqlite3.IntegrityError as exc:
            if "superadmin" in str(exc).lower():
                raise CannotAssignSuperadminRoleError() from exc
            raise

        self._audit.log(
            action="usuario_updated",
            user_id=actor_user_id,
            details=json.dumps(
                {
                    "id": user_id,
                    "old": {"full_name": actual.full_name, "role_id": actual.role_id},
                    "new": {"full_name": full_name_norm, "role_id": role_id},
                },
                ensure_ascii=False,
            ),
        )
        self._log.info("Usuario actualizado: id=%s actor=%s", user_id, actor_user_id)

    def reset_password(
        self,
        user_id: int,
        new_password: str,
        actor_user_id: int,
        actor_role_code: str,
    ) -> None:
        """Resetea la password de otro usuario (o la propia).

        Raises:
            UsuarioNotFoundError, WeakPasswordError,
            CannotResetSuperadminPasswordError (R4).
        """
        target = self.get_usuario(user_id)
        rol_target = self._get_rol_o_fallar(target.role_id)
        # R4: ADMIN no puede tocar la password del SUPERADMIN. Pero el
        # SUPERADMIN sí puede tocar la suya propia (mismo método) o la
        # de cualquier otro.
        if rol_target.code == perms.ROLE_SUPERADMIN and actor_role_code != perms.ROLE_SUPERADMIN:
            raise CannotResetSuperadminPasswordError()

        self._policy.validate(new_password)
        password_hash = self._hasher.hash(new_password)
        self._usuario_write.update_password_hash(user_id, password_hash)
        self._audit.log(
            action="usuario_password_reset",
            user_id=actor_user_id,
            details=json.dumps({"target_id": user_id}, ensure_ascii=False),
        )
        self._log.info(
            "Password reseteada: target_id=%s actor=%s",
            user_id,
            actor_user_id,
        )

    def deactivate_usuario(self, user_id: int, actor_user_id: int) -> None:
        """Desactiva un usuario (soft-delete via ``is_active=False``).

        Raises:
            UsuarioNotFoundError, CannotDeactivateSelfError (R2),
            CannotDeactivateOnlySuperadminError (R3).
        """
        target = self.get_usuario(user_id)
        if user_id == actor_user_id:
            raise CannotDeactivateSelfError()  # R2

        # R3: si target es el SUPERADMIN activo y único, no permitir.
        rol_target = self._get_rol_o_fallar(target.role_id)
        if rol_target.code == perms.ROLE_SUPERADMIN and target.is_active:
            raise CannotDeactivateOnlySuperadminError()

        if not target.is_active:
            return  # idempotente

        self._usuario_write.update_profile(
            user_id=user_id,
            full_name=target.full_name,
            role_id=target.role_id,
            is_active=False,
        )
        self._audit.log(
            action="usuario_deactivated",
            user_id=actor_user_id,
            details=json.dumps({"target_id": user_id}, ensure_ascii=False),
        )
        self._log.info("Usuario desactivado: id=%s actor=%s", user_id, actor_user_id)

    def reactivate_usuario(self, user_id: int, actor_user_id: int) -> None:
        """Reactiva un usuario desactivado. Idempotente."""
        target = self.get_usuario(user_id)
        if target.is_active:
            return
        self._usuario_write.update_profile(
            user_id=user_id,
            full_name=target.full_name,
            role_id=target.role_id,
            is_active=True,
        )
        self._audit.log(
            action="usuario_reactivated",
            user_id=actor_user_id,
            details=json.dumps({"target_id": user_id}, ensure_ascii=False),
        )
        self._log.info("Usuario reactivado: id=%s actor=%s", user_id, actor_user_id)

    def unlock_usuario(self, user_id: int, actor_user_id: int) -> None:
        """Desbloquea cuenta tras N intentos fallidos.

        Resetea ``failed_attempts=0`` y ``locked_until=NULL``. Idempotente.

        Raises:
            UsuarioNotFoundError.
        """
        # Verificar que existe (lanza si no).
        self.get_usuario(user_id)
        self._usuario_write.unlock_account(user_id)
        self._audit.log(
            action="usuario_unlocked",
            user_id=actor_user_id,
            details=json.dumps({"target_id": user_id}, ensure_ascii=False),
        )
        self._log.info("Usuario desbloqueado: id=%s actor=%s", user_id, actor_user_id)

    # ── Validadores privados ──────────────────────────────────────────────

    @staticmethod
    def _validar_username(username: str) -> str:
        """Normaliza (strip) y valida el formato del username."""
        if username is None:
            raise InvalidUsernameError("")
        norm = username.strip()
        if not _USERNAME_RE.match(norm):
            raise InvalidUsernameError(norm)
        return norm

    @staticmethod
    def _validar_full_name(full_name: str) -> str:
        """Normaliza (strip + colapso de espacios) y valida full_name."""
        if full_name is None:
            raise ValueError("El nombre completo no puede ser None.")
        norm = re.sub(r"\s+", " ", full_name).strip()
        if not norm:
            raise ValueError("El nombre completo no puede estar vacío.")
        if len(norm) > _FULL_NAME_MAX:
            raise ValueError(f"El nombre completo no puede exceder {_FULL_NAME_MAX} caracteres.")
        return norm

    def _get_rol_o_fallar(self, role_id: int) -> Rol:
        """Obtiene un rol por id o lanza ``ValueError``."""
        rol = self._rol_read.get_by_id(role_id)
        if rol is None:
            raise ValueError(f"El rol con id {role_id} no existe.")
        return rol

    @staticmethod
    def _enforce_no_assign_superadmin(target_role_code: str, actor_role_code: str) -> None:
        """Aplica R1: solo SUPERADMIN puede asignar el rol SUPERADMIN.

        Si el actor es ADMIN y el target es SUPERADMIN, lanza
        ``CannotAssignSuperadminRoleError``. Si el actor ya es SUPERADMIN,
        permitido (no es el caso típico — la app tiene un solo SUPERADMIN
        por trigger SQL, pero conviene contemplarlo para transferencia
        manual del rol).
        """
        if target_role_code == perms.ROLE_SUPERADMIN and actor_role_code != perms.ROLE_SUPERADMIN:
            raise CannotAssignSuperadminRoleError()
