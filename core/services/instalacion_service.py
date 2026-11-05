"""Datos con los que arranca una instalación nueva de BIO-DIPROMA.

Cuando la base de datos está recién creada (sin ningún usuario), se crean:

    - ``admin`` — Super Administrador (acceso total; la cuenta de quien
      instala y da soporte).
    - ``administrador`` — Administrador (acceso total, para la empresa).

    Los dos con la contraseña ``12345678`` (se recomienda cambiarla desde
    Usuarios y roles). No pasa por la política de contraseñas: es la
    decisión de quien instala.

Si no hay ningún turno, el turno **Operativo**:

    - Lunes a viernes de 07:00 a 17:00, con almuerzo de 12:00 a 13:00.
    - Sábado de 07:00 a 12:00, sin almuerzo.

Y, si no hay catálogos, el departamento **Contenedor** y el cargo
**Usuario** (global: sirve en cualquier departamento). Así se pueden
registrar empleados desde el primer día sin configurar nada: el
formulario de empleado ya los trae elegidos junto con el turno.

Se usa al abrir la app (antes del login) y desde ``bin.crear_admin``. Es
idempotente: en una base que ya tiene usuarios no crea usuarios, si ya hay
turnos no crea el turno, y lo mismo con departamentos y cargos. Una
instalación existente no cambia.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Final, List, Optional, Tuple

from core.models import permissions as perms
from core.models.cargo import Cargo
from core.models.departamento import Departamento
from core.models.turno import DIAS_LABORALES, SABADO, HorarioTurno
from core.models.usuario import Usuario
from core.repositories.cargo_repository import ICargoReadRepository, ICargoWriteRepository
from core.repositories.departamento_repository import (
    IDepartamentoReadRepository,
    IDepartamentoWriteRepository,
)
from core.repositories.rol_repository import IRolReadRepository
from core.repositories.turno_repository import ITurnoReadRepository, ITurnoWriteRepository
from core.repositories.usuario_repository import (
    IUsuarioReadRepository,
    IUsuarioWriteRepository,
)
from core.services.audit_logger import AuditLogger
from core.services.turno_service import TurnoService
from infrastructure.security.bcrypt_hasher import BcryptHasher

CLAVE_INICIAL: Final[str] = "12345678"


@dataclass(frozen=True)
class UsuarioInicial:
    """Un usuario que trae la instalación."""

    username: str
    full_name: str
    rol_code: str


SUPERADMIN_INICIAL: Final = UsuarioInicial("admin", "Super Administrador", perms.ROLE_SUPERADMIN)
ADMINISTRADOR_INICIAL: Final = UsuarioInicial("administrador", "Administrador", perms.ROLE_ADMIN)


@dataclass(frozen=True)
class TurnoInicial:
    """El turno que trae la instalación."""

    nombre: str = "Operativo"
    entrada: str = "07:00"
    salida: str = "17:00"
    almuerzo: Tuple[str, str] = ("12:00", "13:00")
    dias: int = DIAS_LABORALES
    sabado: Tuple[str, str] = ("07:00", "12:00")


TURNO_INICIAL: Final = TurnoInicial()

DEPARTAMENTO_INICIAL: Final[str] = "Contenedor"
CARGO_INICIAL: Final[str] = "Usuario"


@dataclass(frozen=True)
class ResultadoInstalacion:
    """Qué se creó (vacío si la base ya estaba en uso)."""

    usuarios: Tuple[str, ...] = ()
    turno: Optional[str] = None
    departamento: Optional[str] = None
    cargo: Optional[str] = None

    @property
    def hizo_algo(self) -> bool:
        return bool(self.usuarios or self.turno or self.departamento or self.cargo)


class InstalacionService:
    """Crea los usuarios, el turno y los catálogos de una instalación nueva."""

    def __init__(
        self,
        usuario_read: IUsuarioReadRepository,
        usuario_write: IUsuarioWriteRepository,
        rol_read: IRolReadRepository,
        turno_read: ITurnoReadRepository,
        turno_write: ITurnoWriteRepository,
        dep_read: IDepartamentoReadRepository,
        dep_write: IDepartamentoWriteRepository,
        cargo_read: ICargoReadRepository,
        cargo_write: ICargoWriteRepository,
        hasher: BcryptHasher,
        audit_logger: AuditLogger,
    ) -> None:
        self._dep_read = dep_read
        self._dep_write = dep_write
        self._cargo_read = cargo_read
        self._cargo_write = cargo_write
        self._usuario_read = usuario_read
        self._usuario_write = usuario_write
        self._rol_read = rol_read
        self._turno_read = turno_read
        self._turnos = TurnoService(turno_read, turno_write, audit_logger)
        self._hasher = hasher
        self._audit = audit_logger
        self._log = logging.getLogger(self.__class__.__name__)

    def preparar(
        self,
        superadmin_username: str = SUPERADMIN_INICIAL.username,
        clave_superadmin: str = CLAVE_INICIAL,
    ) -> ResultadoInstalacion:
        """Deja la instalación lista si es nueva; si no, no toca nada.

        Args:
            superadmin_username: Usuario del Super Administrador (``admin``).
            clave_superadmin: Su contraseña. El ``administrador`` siempre
                recibe ``CLAVE_INICIAL``.

        Returns:
            Lo que se creó.

        Raises:
            RuntimeError: Faltan los roles (la base no tiene las migraciones).
        """
        creados: List[str] = []
        actor: Optional[int] = None
        if not self._usuario_read.list_all():
            iniciales = [
                (
                    UsuarioInicial(
                        superadmin_username,
                        SUPERADMIN_INICIAL.full_name,
                        SUPERADMIN_INICIAL.rol_code,
                    ),
                    clave_superadmin,
                ),
            ]
            if superadmin_username != ADMINISTRADOR_INICIAL.username:
                iniciales.append((ADMINISTRADOR_INICIAL, CLAVE_INICIAL))
            for inicial, clave in iniciales:
                usuario = self._crear_usuario(inicial, clave)
                if actor is None:
                    actor = usuario.id
                creados.append(inicial.username)

        turno: Optional[str] = None
        if not self._turno_read.list_all():
            t = TURNO_INICIAL
            self._turnos.create_turno(
                nombre=t.nombre,
                hora_entrada=t.entrada,
                hora_salida=t.salida,
                minutos_descanso=0,
                dias_semana=t.dias,
                actor_user_id=actor,
                horarios_extra=[HorarioTurno(SABADO, t.sabado[0], t.sabado[1])],
                almuerzo_inicio=t.almuerzo[0],
                almuerzo_fin=t.almuerzo[1],
            )
            turno = t.nombre

        departamento: Optional[str] = None
        if not self._dep_read.list_all():
            self._dep_write.create(Departamento(id=None, nombre=DEPARTAMENTO_INICIAL))
            departamento = DEPARTAMENTO_INICIAL
        cargo: Optional[str] = None
        if not self._cargo_read.list_all():
            self._cargo_write.create(Cargo(id=None, nombre=CARGO_INICIAL))
            cargo = CARGO_INICIAL

        resultado = ResultadoInstalacion(
            usuarios=tuple(creados), turno=turno, departamento=departamento, cargo=cargo
        )
        if resultado.hizo_algo:
            self._audit.log(
                action="instalacion_inicial",
                user_id=actor,
                details=json.dumps(
                    {
                        "usuarios": list(resultado.usuarios),
                        "turno": turno,
                        "departamento": departamento,
                        "cargo": cargo,
                    },
                    ensure_ascii=False,
                ),
            )
            self._log.info(
                "Instalación nueva: usuarios=%s turno=%s departamento=%s cargo=%s",
                creados,
                turno,
                departamento,
                cargo,
            )
        return resultado

    def _crear_usuario(self, inicial: UsuarioInicial, clave: str) -> Usuario:
        rol = self._rol_read.get_by_code(inicial.rol_code)
        if rol is None or rol.id is None:
            raise RuntimeError(
                f"No se encontró el rol {inicial.rol_code}: la base de datos no tiene las "
                "migraciones aplicadas."
            )
        return self._usuario_write.create(
            Usuario(
                id=None,
                username=inicial.username,
                password_hash=self._hasher.hash(clave),
                full_name=inicial.full_name,
                role_id=rol.id,
                is_active=True,
            )
        )
