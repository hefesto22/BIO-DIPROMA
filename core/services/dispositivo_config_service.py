"""Servicio de configuración de dispositivos ZKTeco (Plan B / Sub-2.4b).

Responsabilidad única: orquestar el CRUD del catálogo de dispositivos
(``alta``, ``edición``, ``archivado``, ``reactivación``) detrás de la
UI de Configuración. Hasta esta sub-tarea los dispositivos solo se
gestionaban por SQL directo — este servicio reemplaza ese flujo manual.

Reglas de negocio aplicadas:

    - ``nombre`` con ``strip()`` + colapso de espacios. Case-sensitive
      (consistente con el ``UNIQUE(nombre)`` del schema, igual que el
      ``CatalogoService``).
    - ``ip`` validada con ``ipaddress.IPv4Address`` — rechaza IPv6,
      notación octal y octetos fuera de 0..255. Coincide con la
      decisión documentada en el docstring del modelo
      ``core/models/dispositivo.py``.
    - ``puerto`` en rango 1..65535 (alineado con el ``CHECK`` del
      schema).
    - Pre-check explícito de duplicados (nombre + endpoint) antes del
      INSERT/UPDATE — así el usuario ve mensajes claros en español en
      vez de un ``IntegrityError`` críptico de SQLite. Aún así
      capturamos ``IntegrityError`` como fallback por race-conditions.
    - Cada mutación genera una entrada en ``audit_log`` con el
      ``actor_user_id`` que pasa el controller.

Diseño (SOLID):
    - **S**: solo gestiona el catálogo de dispositivos. NO ejecuta
      sincronizaciones — eso es ``SincronizacionService``.
    - **D**: recibe ``IDispositivoReadRepository``,
      ``IDispositivoWriteRepository`` y ``AuditLogger`` por inyección.
      No conoce SQLite ni ``customtkinter``.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import sqlite3
from typing import List, Optional

from core.models.dispositivo import MODO_DIRECTO, MODO_PUSH, MODOS, Dispositivo
from core.repositories.dispositivo_repository import (
    IDispositivoReadRepository,
    IDispositivoWriteRepository,
)
from core.services.audit_logger import AuditLogger
from core.services.errors import (
    DispositivoNotFoundError,
    DuplicateDispositivoEndpointError,
    DuplicateDispositivoNombreError,
    DuplicateNumeroSerieError,
    InvalidClaveComunicacionError,
    InvalidIPError,
    InvalidPuertoError,
    MissingRequiredFieldError,
)
from core.services.validators import normalize_nombre

_CLAVE_MAX = 999999

# Rango TCP válido — alineado con el CHECK del schema y con IPPROTO_TCP.
_PUERTO_MIN = 1
_PUERTO_MAX = 65535


class DispositivoConfigService:
    """CRUD del catálogo de dispositivos ZKTeco."""

    def __init__(
        self,
        dispositivo_read: IDispositivoReadRepository,
        dispositivo_write: IDispositivoWriteRepository,
        audit_logger: AuditLogger,
    ) -> None:
        """Inicializa el servicio con sus dependencias inyectadas.

        Args:
            dispositivo_read: Repositorio de lectura.
            dispositivo_write: Repositorio de escritura.
            audit_logger: Logger de auditoría (append-only).
        """
        self._read = dispositivo_read
        self._write = dispositivo_write
        self._audit = audit_logger
        self._log = logging.getLogger(self.__class__.__name__)

    # ── Lectura ───────────────────────────────────────────────────────────

    def list_dispositivos(self, solo_activos: bool = True) -> List[Dispositivo]:
        """Devuelve la lista de dispositivos (activos por default).

        Args:
            solo_activos: Si ``True`` (default), solo retorna los con
                ``is_active=True``. Si ``False``, incluye archivados —
                útil para el toggle "Ver archivados" de la UI.
        """
        if solo_activos:
            return self._read.list_active()
        return self._read.list_all()

    def get_dispositivo(self, dispositivo_id: int) -> Dispositivo:
        """Devuelve un dispositivo por id.

        Raises:
            DispositivoNotFoundError: Si no existe el id.
        """
        disp = self._read.get_by_id(dispositivo_id)
        if disp is None:
            raise DispositivoNotFoundError(dispositivo_id)
        return disp

    # ── Mutaciones ────────────────────────────────────────────────────────

    def create_dispositivo(
        self,
        nombre: str,
        ip: str,
        puerto: int,
        actor_user_id: int,
        modo: str = MODO_DIRECTO,
        clave_comunicacion: int = 0,
        usar_udp: bool = False,
        numero_serie: Optional[str] = None,
    ) -> Dispositivo:
        """Crea un dispositivo nuevo.

        Args:
            nombre: Texto libre. Se normaliza (trim + colapso de
                espacios). No puede quedar vacío tras normalizar.
            ip: Dirección IPv4 en notación punto-decimal estándar.
            puerto: Puerto TCP del reloj (típicamente 4370).
            actor_user_id: Usuario que ejecuta la acción (audit).
            modo: ``MODO_DIRECTO`` (la app pide las marcas) o ``MODO_PUSH``
                (el reloj las envía solo).
            clave_comunicacion: "Comm Key" del reloj, 0..999999 (0 = sin clave).
            usar_udp: Conectarse por UDP (relojes antiguos).
            numero_serie: Obligatorio en modo PUSH; único entre relojes.

        Returns:
            El ``Dispositivo`` recién creado con ``id`` ya asignado.

        Raises:
            MissingRequiredFieldError: ``nombre`` vacío, o falta la serie en PUSH.
            InvalidIPError: ``ip`` no es IPv4 válida.
            InvalidPuertoError: ``puerto`` fuera de 1..65535.
            InvalidClaveComunicacionError: clave fuera de 0..999999.
            DuplicateDispositivoNombreError: Ya existe ese nombre.
            DuplicateDispositivoEndpointError: Ya existe ``(ip, puerto)``.
            DuplicateNumeroSerieError: Otro reloj ya tiene esa serie.
        """
        nuevo = self._validar(
            None, nombre, ip, puerto, modo, clave_comunicacion, usar_udp, numero_serie
        )
        try:
            creado = self._write.create(nuevo)
        except sqlite3.IntegrityError as exc:
            # Fallback ante race condition entre el pre-check y el INSERT.
            raise self._traducir_integrity_error(exc, nuevo) from exc

        assert creado.id is not None
        self._audit.log(
            action="dispositivo_created",
            user_id=actor_user_id,
            details=json.dumps({"id": creado.id, **self._resumen(creado)}, ensure_ascii=False),
        )
        self._log.info(
            "Dispositivo creado: id=%s nombre=%s endpoint=%s:%s modo=%s",
            creado.id,
            creado.nombre,
            creado.ip,
            creado.puerto,
            creado.modo,
        )
        return creado

    def update_dispositivo(
        self,
        dispositivo_id: int,
        nombre: str,
        ip: str,
        puerto: int,
        actor_user_id: int,
        modo: Optional[str] = None,
        clave_comunicacion: Optional[int] = None,
        usar_udp: Optional[bool] = None,
        numero_serie: Optional[str] = None,
    ) -> None:
        """Actualiza los datos de conexión de un dispositivo existente.

        NO toca ``is_active`` — para eso existen ``archive`` y
        ``unarchive`` con semántica explícita. Los parámetros de conexión
        que vengan en ``None`` conservan su valor actual (salvo
        ``numero_serie``, que solo se conserva si el modo sigue siendo el
        mismo y no se pasa otro).

        Raises:
            DispositivoNotFoundError: ``dispositivo_id`` no existe.
            (Y las mismas validaciones que ``create_dispositivo``.)
        """
        actual = self.get_dispositivo(dispositivo_id)
        nuevo = self._validar(
            dispositivo_id,
            nombre,
            ip,
            puerto,
            actual.modo if modo is None else modo,
            actual.clave_comunicacion if clave_comunicacion is None else clave_comunicacion,
            actual.usar_udp if usar_udp is None else usar_udp,
            actual.numero_serie if numero_serie is None else numero_serie,
        )
        nuevo.is_active = actual.is_active
        if (
            self._resumen(nuevo) == self._resumen(actual)
            and nuevo.clave_comunicacion == actual.clave_comunicacion
        ):
            return  # No-op si nada cambió — evita audit ruidoso.
        try:
            self._write.update(nuevo)
        except sqlite3.IntegrityError as exc:
            raise self._traducir_integrity_error(exc, nuevo) from exc

        self._audit.log(
            action="dispositivo_updated",
            user_id=actor_user_id,
            details=json.dumps(
                {"id": dispositivo_id, "old": self._resumen(actual), "new": self._resumen(nuevo)},
                ensure_ascii=False,
            ),
        )
        self._log.info(
            "Dispositivo actualizado: id=%s %s:%s (%s) -> %s:%s (%s)",
            dispositivo_id,
            actual.ip,
            actual.puerto,
            actual.modo,
            nuevo.ip,
            nuevo.puerto,
            nuevo.modo,
        )

    def _validar(
        self,
        dispositivo_id: Optional[int],
        nombre: str,
        ip: str,
        puerto: int,
        modo: str,
        clave_comunicacion: int,
        usar_udp: bool,
        numero_serie: Optional[str],
    ) -> Dispositivo:
        """Normaliza y valida todo; devuelve el ``Dispositivo`` listo para guardar."""
        nombre_norm = normalize_nombre(nombre, "nombre")
        ip_norm = self._validar_ip(ip)
        self._validar_puerto(puerto)
        if modo not in MODOS:
            raise ValueError(f"Modo de conexión desconocido: {modo!r}")
        self._validar_clave(clave_comunicacion)
        serie = (numero_serie or "").strip() or None
        if modo == MODO_PUSH and serie is None:
            raise MissingRequiredFieldError("numero_serie")

        # Pre-check de duplicados, ignorando la propia fila.
        otro = self._read.get_by_nombre(nombre_norm)
        if otro is not None and otro.id != dispositivo_id:
            raise DuplicateDispositivoNombreError(nombre_norm)
        otro = self._read.get_by_ip_puerto(ip_norm, puerto)
        if otro is not None and otro.id != dispositivo_id:
            raise DuplicateDispositivoEndpointError(ip_norm, puerto)
        if serie is not None:
            otro = self._read.get_by_numero_serie(serie)
            if otro is not None and otro.id != dispositivo_id:
                raise DuplicateNumeroSerieError(serie, otro.nombre)

        return Dispositivo(
            id=dispositivo_id,
            nombre=nombre_norm,
            ip=ip_norm,
            puerto=puerto,
            modo=modo,
            clave_comunicacion=clave_comunicacion,
            usar_udp=bool(usar_udp),
            numero_serie=serie,
        )

    @staticmethod
    def _resumen(d: Dispositivo) -> dict[str, object]:
        """Los campos editables, para auditoría (la clave no se registra, solo si hay)."""
        return {
            "nombre": d.nombre,
            "ip": d.ip,
            "puerto": d.puerto,
            "modo": d.modo,
            "con_clave": d.clave_comunicacion != 0,
            "usar_udp": d.usar_udp,
            "numero_serie": d.numero_serie,
        }

    def archive_dispositivo(self, dispositivo_id: int, actor_user_id: int) -> None:
        """Archiva un dispositivo (soft-delete, ``is_active=False``).

        El dispositivo deja de aparecer en el combo de sincronización
        pero se preservan los registros históricos (FK ``RESTRICT``).
        Idempotente: archivar uno ya archivado es no-op.

        Raises:
            DispositivoNotFoundError: ``dispositivo_id`` no existe.
        """
        actual = self.get_dispositivo(dispositivo_id)
        if not actual.is_active:
            return  # Ya archivado — no-op idempotente.
        self._write.archive(dispositivo_id)
        self._audit.log(
            action="dispositivo_archived",
            user_id=actor_user_id,
            details=json.dumps({"id": dispositivo_id}),
        )
        self._log.info("Dispositivo archivado: id=%s", dispositivo_id)

    def unarchive_dispositivo(self, dispositivo_id: int, actor_user_id: int) -> None:
        """Reactiva un dispositivo archivado (``is_active=True``).

        Idempotente: reactivar uno ya activo es no-op.

        Raises:
            DispositivoNotFoundError: ``dispositivo_id`` no existe.
        """
        actual = self.get_dispositivo(dispositivo_id)
        if actual.is_active:
            return  # Ya activo — no-op idempotente.
        self._write.unarchive(dispositivo_id)
        self._audit.log(
            action="dispositivo_unarchived",
            user_id=actor_user_id,
            details=json.dumps({"id": dispositivo_id}),
        )
        self._log.info("Dispositivo reactivado: id=%s", dispositivo_id)

    # ── Validadores privados ──────────────────────────────────────────────

    @staticmethod
    def _validar_ip(ip: str) -> str:
        """Valida una IP IPv4 y devuelve su forma canónica.

        ``ipaddress.IPv4Address`` rechaza octal, IPv6 y octetos
        fuera de rango. Devuelve el ``str(...)`` canónico —
        normalizar acá evita que ``"192.168.000.101"`` y
        ``"192.168.0.101"`` se traten como dos endpoints distintos.

        Raises:
            InvalidIPError: Si la entrada no es IPv4 válida.
        """
        if ip is None or not ip.strip():
            raise InvalidIPError(ip if ip is not None else "")
        candidata = ip.strip()
        try:
            return str(ipaddress.IPv4Address(candidata))
        except (ipaddress.AddressValueError, ValueError) as exc:
            raise InvalidIPError(candidata) from exc

    @staticmethod
    def _validar_puerto(puerto: int) -> None:
        """Valida que ``puerto`` esté en 1..65535.

        Raises:
            InvalidPuertoError: Si está fuera de rango.
        """
        if not isinstance(puerto, int) or isinstance(puerto, bool):
            # bool es subclase de int — lo rechazamos explícitamente
            # para evitar que ``True``/``False`` pasen como puerto.
            raise InvalidPuertoError(puerto)
        if not (_PUERTO_MIN <= puerto <= _PUERTO_MAX):
            raise InvalidPuertoError(puerto)

    @staticmethod
    def _validar_clave(clave: int) -> None:
        if isinstance(clave, bool) or not isinstance(clave, int) or not 0 <= clave <= _CLAVE_MAX:
            raise InvalidClaveComunicacionError(clave)

    @staticmethod
    def _traducir_integrity_error(exc: sqlite3.IntegrityError, d: Dispositivo) -> Exception:
        """Convierte un ``IntegrityError`` de SQLite al error de dominio.

        Cuando el pre-check no detectó el conflicto (race condition),
        el INSERT/UPDATE rompe el UNIQUE y caemos acá. Mapeamos al
        error específico según qué constraint disparó el mensaje.
        """
        mensaje = str(exc).lower()
        if "dispositivos.nombre" in mensaje:
            return DuplicateDispositivoNombreError(d.nombre)
        if "dispositivos.ip" in mensaje and "puerto" in mensaje:
            return DuplicateDispositivoEndpointError(d.ip, d.puerto)
        if "numero_serie" in mensaje:
            return DuplicateNumeroSerieError(d.numero_serie or "", "otro reloj")
        # Caso desconocido: relanzamos como nombre duplicado por
        # defecto — preferimos un mensaje en español a un crash.
        return DuplicateDispositivoNombreError(d.nombre)
