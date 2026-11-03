"""Servicio del Setup Wizard.

Responsabilidad única: gestionar la creación del único SUPERADMIN del
sistema en el primer arranque.

Política (REGLAS del proyecto):
    - ``is_first_run()`` devuelve True si NO existe ningún usuario con
      rol SUPERADMIN. Es la única fuente de verdad: no hay bandera en
      BD que se pueda manipular para "re-activar" el wizard.
    - ``create_superadmin(username, password)``:
        1. Verifica que aún sea first_run. Si no → SetupAlreadyCompletedError.
        2. Valida la password contra la política inyectada.
        3. Verifica duplicado de username (fail fast antes de hashear).
        4. Hashea con bcrypt (cost factor del config).
        5. Inserta al usuario con rol SUPERADMIN.
        6. Registra ``setup_completed`` en audit_log con user_id del nuevo
           SUPERADMIN.
    - La GUI del wizard vendrá en 1.5; este service es consumido por un
      entrypoint CLI en ``bin/setup_wizard.py`` y por la futura vista.

Diseño (SOLID):
    - S: solo orquesta el first-run setup.
    - D: recibe repos + hasher + audit_logger + password_policy por
         inyección; no conoce detalles de SQLite ni de bcrypt.
"""

from __future__ import annotations

import logging

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
    DuplicateUsernameError,
    SetupAlreadyCompletedError,
)
from core.services.password_policy import PasswordPolicy
from infrastructure.security.bcrypt_hasher import BcryptHasher


class SetupWizardService:
    """Crea el único SUPERADMIN del sistema en el primer arranque."""

    def __init__(
        self,
        usuario_read: IUsuarioReadRepository,
        usuario_write: IUsuarioWriteRepository,
        rol_read: IRolReadRepository,
        hasher: BcryptHasher,
        audit_logger: AuditLogger,
        password_policy: PasswordPolicy,
    ) -> None:
        """Inicializa el servicio con sus dependencias inyectadas.

        Args:
            usuario_read: Lectura (para ``count_by_role`` y duplicados).
            usuario_write: Escritura (crea el nuevo SUPERADMIN).
            rol_read: Para obtener el ``role_id`` del SUPERADMIN a partir
                de su code (``SUPERADMIN``). Evita hardcodear el id 1.
            hasher: Adaptador bcrypt.
            audit_logger: Para registrar ``setup_completed``.
            password_policy: Reglas de complejidad inyectadas.
        """
        self._usuario_read = usuario_read
        self._usuario_write = usuario_write
        self._rol_read = rol_read
        self._hasher = hasher
        self._audit = audit_logger
        self._policy = password_policy
        self._log = logging.getLogger(self.__class__.__name__)

    def is_first_run(self) -> bool:
        """Devuelve True si aún no existe ningún SUPERADMIN.

        Raises:
            RuntimeError: Si el rol SUPERADMIN no está en BD. Indica que
                la migración 002_auth.sql no corrió — estado catastrófico
                del que no se puede recuperar sin intervención manual.
        """
        rol = self._get_rol_superadmin()
        return self._usuario_read.count_by_role(rol.id) == 0

    def create_superadmin(
        self,
        username: str,
        password: str,
        full_name: str,
    ) -> Usuario:
        """Crea el único SUPERADMIN del sistema.

        Args:
            username: Nombre de usuario. No se recorta — el caller debe
                validarlo visualmente (la UI hará trim).
            password: Password en texto plano. Se valida contra la
                política y luego se hashea con bcrypt.
            full_name: Nombre completo a mostrar.

        Returns:
            El usuario creado, con id asignado.

        Raises:
            SetupAlreadyCompletedError: Ya existe un SUPERADMIN.
            WeakPasswordError: La password no cumple la política.
            DuplicateUsernameError: El username ya existe.
            ValueError: Si ``username`` o ``full_name`` son vacíos.
        """
        self._validar_entrada_no_vacia(username, full_name)
        rol = self._get_rol_superadmin()

        if self._usuario_read.count_by_role(rol.id) > 0:
            raise SetupAlreadyCompletedError()

        # Valida password ANTES de tocar BD — feedback rápido al usuario.
        self._policy.validate(password)

        if self._usuario_read.get_by_username(username) is not None:
            raise DuplicateUsernameError(username)

        password_hash = self._hasher.hash(password)
        creado = self._usuario_write.create(
            Usuario(
                id=None,
                username=username,
                password_hash=password_hash,
                full_name=full_name,
                role_id=rol.id,
                is_active=True,
            )
        )
        # Por contrato, create() devuelve el usuario con id asignado.
        assert creado.id is not None
        self._audit.log("setup_completed", user_id=creado.id)
        self._log.info("SUPERADMIN creado: username=%s id=%s", username, creado.id)
        return creado

    # ── Helpers ───────────────────────────────────────────────────────────

    def _get_rol_superadmin(self) -> Rol:
        """Busca el rol SUPERADMIN en BD. Encapsula el error si falta."""
        rol = self._rol_read.get_by_code(perms.ROLE_SUPERADMIN)
        if rol is None:
            raise RuntimeError(
                "El rol SUPERADMIN no existe en la BD. " "La migración 002_auth.sql no se aplicó."
            )
        return rol

    @staticmethod
    def _validar_entrada_no_vacia(username: str, full_name: str) -> None:
        """Valida que los campos de texto no vengan vacíos.

        La password se valida aparte vía ``PasswordPolicy``.
        """
        if not username:
            raise ValueError("El nombre de usuario no puede estar vacío.")
        if not full_name:
            raise ValueError("El nombre completo no puede estar vacío.")
