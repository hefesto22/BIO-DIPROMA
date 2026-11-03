"""Servicio de autenticación.

Orquesta el flujo de login con la política del proyecto:

    1. Buscar usuario por username. Si no existe → ``InvalidCredentialsError``.
    2. Si la cuenta está bloqueada y ``now < locked_until`` → ``AccountLockedError``.
       Si el bloqueo ya expiró, se limpia y se continúa el flujo.
    3. Verificar el password contra el hash. Si falla:
         - Incrementar ``failed_attempts``.
         - Si alcanza ``MAX_FAILED_LOGIN_ATTEMPTS`` → aplicar ``locked_until``.
         - Registrar ``login_fail`` en audit_log.
         - Lanzar ``InvalidCredentialsError``.
    4. Si la password es correcta pero ``is_active=False`` →
       ``AccountInactiveError`` (tras resetear intentos fallidos).
    5. Password correcta + activo: resetear contador, registrar ``login_ok``,
       cargar permisos del rol, devolver ``Session``.

REGLAS aplicadas:
    - Mensajes de error genéricos en login (no revelar si es user o pwd).
    - Todo intento de login (ok o fail) queda en audit_log.
    - Bloqueo tras 5 fallidos consecutivos (configurable en ``config.py``).
    - Opción B aprobada: bloqueo temporal fijo con auto-desbloqueo.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from core.models.usuario import Usuario
from core.repositories.rol_repository import IRolReadRepository
from core.repositories.usuario_repository import (
    IUsuarioReadRepository,
    IUsuarioWriteRepository,
)
from core.services.audit_logger import AuditLogger
from core.services.errors import (
    AccountInactiveError,
    AccountLockedError,
    InvalidCredentialsError,
)
from core.services.session import Session
from infrastructure.security.bcrypt_hasher import BcryptHasher


class AuthService:
    """Autenticación con bloqueo temporal tras intentos fallidos."""

    def __init__(
        self,
        usuario_read: IUsuarioReadRepository,
        usuario_write: IUsuarioWriteRepository,
        rol_read: IRolReadRepository,
        hasher: BcryptHasher,
        audit_logger: AuditLogger,
        max_failed_attempts: int,
        lockout_duration_minutes: int,
    ) -> None:
        """Inicializa el servicio con sus dependencias inyectadas.

        Args:
            usuario_read: Lectura de usuarios.
            usuario_write: Escritura (solo campos de login state / password).
            rol_read: Lectura de roles (para cargar permisos en la Session).
            hasher: Verificador bcrypt inyectado.
            audit_logger: Para registrar login_ok / login_fail.
            max_failed_attempts: Umbral de bloqueo (de ``config.py``).
            lockout_duration_minutes: Duración del bloqueo en minutos.

        Raises:
            ValueError: Si los parámetros numéricos no son positivos.
        """
        if max_failed_attempts < 1:
            raise ValueError("max_failed_attempts debe ser >= 1")
        if lockout_duration_minutes < 1:
            raise ValueError("lockout_duration_minutes debe ser >= 1")
        self._usuario_read = usuario_read
        self._usuario_write = usuario_write
        self._rol_read = rol_read
        self._hasher = hasher
        self._audit = audit_logger
        self._max_failed = max_failed_attempts
        self._lockout_delta = timedelta(minutes=lockout_duration_minutes)
        self._log = logging.getLogger(self.__class__.__name__)

    def login(self, username: str, password: str) -> Session:
        """Autentica al usuario y devuelve una ``Session`` válida.

        Raises:
            InvalidCredentialsError: Usuario no existe o password incorrecta.
            AccountLockedError: Cuenta bloqueada temporalmente.
            AccountInactiveError: Credenciales correctas pero ``is_active=False``.
        """
        usuario = self._usuario_read.get_by_username(username)
        if usuario is None:
            # Registramos el intento con user_id=None y username como detalle.
            # Eso cumple la REGLA de audit_log sin enumerar usuarios.
            self._audit.log(
                "login_fail",
                user_id=None,
                details=f'{{"username_intento": "{username}"}}',
            )
            raise InvalidCredentialsError()

        self._ensure_not_locked(usuario)

        if not self._hasher.verify(password, usuario.password_hash):
            self._handle_failed_attempt(usuario)
            raise InvalidCredentialsError()

        # Password correcta — pero puede estar desactivado.
        if not usuario.is_active:
            self._reset_failed_attempts(usuario)
            self._audit.log("login_fail_inactive", user_id=usuario.id)
            raise AccountInactiveError()

        return self._finalize_successful_login(usuario)

    # ── Helpers privados (cada uno con una responsabilidad) ───────────────

    def _ensure_not_locked(self, usuario: Usuario) -> None:
        """Si la cuenta sigue bloqueada, lanza. Si expiró, NO hace nada.

        La limpieza real del ``locked_until`` se hace dentro de
        ``_reset_failed_attempts`` tras un login exitoso — así evitamos
        escrituras en el path de error ("cuenta está bloqueada pero
        expiró" no necesita persistir nada: el siguiente login exitoso
        resetea todo).
        """
        if usuario.locked_until is None:
            return
        now_iso = _utc_now_iso()
        if usuario.locked_until > now_iso:
            raise AccountLockedError(usuario.locked_until)
        # locked_until <= now → expirado, seguimos el flujo normal.

    def _handle_failed_attempt(self, usuario: Usuario) -> None:
        """Incrementa el contador y aplica bloqueo si corresponde."""
        assert usuario.id is not None  # usuarios leídos de BD siempre tienen id
        nuevos = usuario.failed_attempts + 1
        locked_until: Optional[str] = None
        action = "login_fail"

        if nuevos >= self._max_failed:
            locked_until = (datetime.now(timezone.utc) + self._lockout_delta).isoformat(
                timespec="seconds"
            )
            action = "account_locked"
            self._log.warning(
                "Cuenta bloqueada: usuario=%s hasta=%s", usuario.username, locked_until
            )

        self._usuario_write.update_login_state(usuario.id, nuevos, locked_until)
        self._audit.log(action, user_id=usuario.id)

    def _reset_failed_attempts(self, usuario: Usuario) -> None:
        """Limpia el contador y el bloqueo tras un login válido."""
        assert usuario.id is not None
        if usuario.failed_attempts == 0 and usuario.locked_until is None:
            return  # Nada que limpiar — evitamos una escritura innecesaria.
        self._usuario_write.update_login_state(usuario.id, failed_attempts=0, locked_until=None)

    def _finalize_successful_login(self, usuario: Usuario) -> Session:
        """Crea la sesión tras validar password + is_active."""
        assert usuario.id is not None
        self._reset_failed_attempts(usuario)
        rol = self._rol_read.get_by_id(usuario.role_id)
        if rol is None:
            # Estado inconsistente: FK debería prevenirlo. Auditamos y fallamos.
            self._audit.log("login_fail_role_missing", user_id=usuario.id)
            raise InvalidCredentialsError()
        self._audit.log("login_ok", user_id=usuario.id)
        return Session(
            user_id=usuario.id,
            username=usuario.username,
            role_id=rol.id,
            role_code=rol.code,
            permissions=rol.permissions,
        )


def _utc_now_iso() -> str:
    """Timestamp UTC ISO-8601 con precisión de segundos."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
