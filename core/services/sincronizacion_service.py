"""Servicio de sincronización con dispositivos ZKTeco.

Orquesta el ciclo completo de una descarga:

    1. Valida entradas (dispositivo activo, rango correcto).
    2. Crea la cabecera ``sincronizaciones`` con estado ``EN_CURSO``.
    3. Llama al adapter ``IZKTecoAdapter.pull_attendance`` para traer las
       marcadas del reloj.
    4. Persiste las marcadas en ``registros_raw`` (bulk insert con
       dedupe defensivo vía ``INSERT OR IGNORE``).
    5. Cierra la sync con ``marcar_ok`` o ``marcar_fallida`` según resultado.
    6. Dispara consolidación in-line (Decisión 2 aprobada) si se inyectó
       un ``ConsolidacionService``.

Responsabilidades que NO asume:
    - Retries, backoff, schedulers — todo eso es competencia de la UI
      o de un orquestador externo.
    - Lógica de negocio de asistencia — vive en ``ConsolidacionService``.
    - Bloqueo del hilo principal — el caller (controller) debe correr
      ``ejecutar`` en un worker thread (regla de concurrencia del PRD).

Diseño (SOLID):
    - S: orquesta la descarga y nada más. La consolidación se delega.
    - D: recibe repos + adapter + (opcional) ConsolidacionService por
         inyección. Ningún ``import`` de pyzk ni de SQLite concreto.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import List, Optional, Protocol

from core.models.dispositivo import Dispositivo
from core.models.registro_raw import RegistroRaw
from core.models.sincronizacion import Sincronizacion
from core.repositories.dispositivo_repository import IDispositivoReadRepository
from core.repositories.registro_raw_repository import IRegistroRawWriteRepository
from core.repositories.sincronizacion_repository import (
    ISincronizacionReadRepository,
    ISincronizacionWriteRepository,
)
from core.services.audit_logger import AuditLogger
from core.services.errors import (
    DispositivoInactiveError,
    DispositivoNotFoundError,
    InvalidRangoError,
)
from core.services.sincronizacion_result import (
    ResultadoConsolidacion,
    ResultadoSincronizacion,
)
from core.services.validators import validate_fecha_iso
from infrastructure.zkteco.exceptions import ZKAdapterError
from infrastructure.zkteco.interface import IZKTecoAdapter

logger = logging.getLogger(__name__)


class IConsolidadorDeRango(Protocol):
    """Contrato mínimo que debe implementar cualquier consolidador inyectado.

    Definido como ``typing.Protocol`` (structural typing) para evitar un
    import circular entre ``SincronizacionService`` y
    ``ConsolidacionService``. ``ConsolidacionService.consolidar_rango``
    satisface este protocolo naturalmente por la firma.
    """

    def consolidar_rango(self, desde: str, hasta: str) -> ResultadoConsolidacion:
        """Consolida raw → asistencias dentro del rango inclusivo."""
        ...


class SincronizacionService:
    """Orquesta el pull de marcadas desde un ZKTeco + persistencia + consolidación."""

    def __init__(
        self,
        dispositivo_read: IDispositivoReadRepository,
        sincronizacion_read: ISincronizacionReadRepository,
        sincronizacion_write: ISincronizacionWriteRepository,
        registro_raw_write: IRegistroRawWriteRepository,
        adapter: IZKTecoAdapter,
        audit_logger: AuditLogger,
        consolidador: Optional[IConsolidadorDeRango] = None,
    ) -> None:
        """Construye el servicio.

        Args:
            dispositivo_read: Para validar que el dispositivo existe y
                está activo antes de llamar al reloj.
            sincronizacion_read: Para recovery de huérfanas al arranque.
            sincronizacion_write: Para crear la cabecera y cerrarla.
            registro_raw_write: Para bulk-insert de las marcadas.
            adapter: Implementación concreta del driver ZKTeco (real o fake).
            audit_logger: Traza de auditoría (quién disparó qué sync).
            consolidador: Si se inyecta, se invoca automáticamente tras
                ``marcar_ok`` exitoso para consolidar raw → asistencias
                del rango recién sincronizado (Decisión 2 aprobada).
                Si se omite, ``ResultadoSincronizacion.consolidacion``
                queda en ``None`` — útil para tests del path aislado.
        """
        self._dispositivo_read = dispositivo_read
        self._sync_read = sincronizacion_read
        self._sync_write = sincronizacion_write
        self._registro_raw_write = registro_raw_write
        self._adapter = adapter
        self._audit = audit_logger
        self._consolidador = consolidador

    # ── API pública ──────────────────────────────────────────────────────────

    def recover_huerfanas(self) -> int:
        """Cierra como FALLIDA cualquier sync que haya quedado ``EN_CURSO``.

        Se invoca desde el composition root al arrancar la app (Decisión
        1 aprobada). La app es desktop single-user — una sync ``EN_CURSO``
        al arranque solo puede significar que el proceso anterior murió
        sin cerrarla.

        Returns:
            Cantidad de filas marcadas como FALLIDA. 0 si no había ninguna
            (caso normal).
        """
        huerfanas = self._sync_read.list_en_curso()
        if not huerfanas:
            return 0
        fin_ts = _now_iso_utc()
        mensaje = "Interrumpida por cierre inesperado de la aplicación."
        for sync in huerfanas:
            assert sync.id is not None  # list_en_curso devuelve filas persistidas
            self._sync_write.marcar_fallida(sync.id, fin=fin_ts, error_mensaje=mensaje)
            logger.warning(
                "sync.huerfana_cerrada sincronizacion_id=%s dispositivo_id=%s",
                sync.id,
                sync.dispositivo_id,
            )
        return len(huerfanas)

    def ejecutar(
        self,
        dispositivo_id: int,
        rango_desde: str,
        rango_hasta: str,
        iniciada_por_user_id: Optional[int],
    ) -> ResultadoSincronizacion:
        """Ejecuta una sincronización completa contra un dispositivo.

        Flujo:
            1. Valida dispositivo (existe + activo) y rango (formato + orden).
            2. Crea cabecera ``sincronizaciones`` EN_CURSO.
            3. Invoca ``adapter.pull_attendance`` dentro de try/except.
            4. Si falla: marca la sync FALLIDA y re-propaga como
               ``ZKAdapterError``.
            5. Si OK: bulk-inserta los raw (post-dedupe), marca la sync OK.
            6. Si hay consolidador inyectado: lo invoca — si falla, se
               captura y se reporta en
               ``ResultadoSincronizacion.error_consolidacion`` sin tocar
               el estado OK de la sync.

        Args:
            dispositivo_id: PK del reloj desde el cual descargar.
            rango_desde: Fecha ISO ``"YYYY-MM-DD"``.
            rango_hasta: Fecha ISO ``"YYYY-MM-DD"`` (inclusiva).
            iniciada_por_user_id: FK al usuario actual o ``None``.

        Returns:
            ``ResultadoSincronizacion`` con sync_id, registros_recibidos,
            y (opcional) resultado de la consolidación in-line.

        Raises:
            DispositivoNotFoundError: dispositivo inexistente.
            DispositivoInactiveError: dispositivo archivado.
            InvalidRangoError: rango invertido.
            InvalidDateError: fechas con formato inválido.
            ZKAdapterError: cualquier fallo del adapter (red, timeout,
                protocolo). La sync queda persistida como FALLIDA.
        """
        dispositivo = self._validar_dispositivo(dispositivo_id)
        desde_date, hasta_date = self._validar_rango(rango_desde, rango_hasta)

        sync = self._crear_cabecera(
            dispositivo_id=dispositivo_id,
            rango_desde=rango_desde,
            rango_hasta=rango_hasta,
            iniciada_por_user_id=iniciada_por_user_id,
        )
        assert sync.id is not None  # create garantiza id

        registros = self._pull_con_manejo_de_errores(
            dispositivo=dispositivo,
            sincronizacion_id=sync.id,
            desde_date=desde_date,
            hasta_date=hasta_date,
            iniciada_por_user_id=iniciada_por_user_id,
        )

        registros_recibidos = self._persistir_y_cerrar(sync.id, registros)
        self._audit.log(
            action="sync.ok",
            user_id=iniciada_por_user_id,
            details=json.dumps(
                {
                    "sincronizacion_id": sync.id,
                    "dispositivo_id": dispositivo_id,
                    "registros_recibidos": registros_recibidos,
                }
            ),
        )

        resultado = ResultadoSincronizacion(
            sincronizacion_id=sync.id,
            dispositivo_id=dispositivo_id,
            registros_recibidos=registros_recibidos,
        )
        self._ejecutar_consolidacion_inline(resultado, rango_desde, rango_hasta)
        return resultado

    # ── Helpers privados ─────────────────────────────────────────────────────

    def _validar_dispositivo(self, dispositivo_id: int) -> Dispositivo:
        """Carga y valida el dispositivo; lanza la excepción de dominio correcta."""
        dispositivo = self._dispositivo_read.get_by_id(dispositivo_id)
        if dispositivo is None:
            raise DispositivoNotFoundError(dispositivo_id)
        if not dispositivo.is_active:
            raise DispositivoInactiveError(dispositivo_id)
        return dispositivo

    def _validar_rango(self, rango_desde: str, rango_hasta: str) -> tuple[date, date]:
        """Valida formato ISO y orden lógico ``desde <= hasta``.

        Returns:
            Tupla ``(desde_date, hasta_date)`` parseada para pasar al adapter.
        """
        desde = validate_fecha_iso(rango_desde, campo="rango_desde")
        hasta = validate_fecha_iso(rango_hasta, campo="rango_hasta")
        if desde > hasta:
            raise InvalidRangoError(desde=rango_desde, hasta=rango_hasta)
        return desde, hasta

    def _crear_cabecera(
        self,
        dispositivo_id: int,
        rango_desde: str,
        rango_hasta: str,
        iniciada_por_user_id: Optional[int],
    ) -> Sincronizacion:
        """Crea la fila EN_CURSO en ``sincronizaciones``."""
        sync = Sincronizacion(
            id=None,
            dispositivo_id=dispositivo_id,
            iniciada_por_user_id=iniciada_por_user_id,
            inicio=_now_iso_utc(),
            rango_desde=rango_desde,
            rango_hasta=rango_hasta,
        )
        return self._sync_write.create(sync)

    def _pull_con_manejo_de_errores(
        self,
        dispositivo: Dispositivo,
        sincronizacion_id: int,
        desde_date: date,
        hasta_date: date,
        iniciada_por_user_id: Optional[int],
    ) -> List[RegistroRaw]:
        """Pull del adapter; en caso de error marca la sync y re-propaga."""
        try:
            return self._adapter.pull_attendance(
                dispositivo=dispositivo,
                sincronizacion_id=sincronizacion_id,
                desde=desde_date,
                hasta=hasta_date,
            )
        except ZKAdapterError as exc:
            self._cerrar_fallida(sincronizacion_id, error_mensaje=str(exc))
            self._audit.log(
                action="sync.fallida",
                user_id=iniciada_por_user_id,
                details=json.dumps(
                    {
                        "sincronizacion_id": sincronizacion_id,
                        "dispositivo_id": dispositivo.id,
                        "error": str(exc),
                    }
                ),
            )
            raise

    def _persistir_y_cerrar(self, sincronizacion_id: int, registros: List[RegistroRaw]) -> int:
        """Bulk-inserta los raw y cierra la sync como OK.

        Devuelve la cantidad real de registros insertados (post-dedupe).
        """
        insertados = self._registro_raw_write.create_bulk(registros)
        self._sync_write.marcar_ok(
            sincronizacion_id=sincronizacion_id,
            fin=_now_iso_utc(),
            registros_recibidos=insertados,
        )
        return insertados

    def _cerrar_fallida(self, sincronizacion_id: int, error_mensaje: str) -> None:
        """Marca la sync como FALLIDA con el mensaje dado."""
        self._sync_write.marcar_fallida(
            sincronizacion_id=sincronizacion_id,
            fin=_now_iso_utc(),
            error_mensaje=error_mensaje,
        )

    def _ejecutar_consolidacion_inline(
        self,
        resultado: ResultadoSincronizacion,
        rango_desde: str,
        rango_hasta: str,
    ) -> None:
        """Invoca el consolidador si fue inyectado. Nunca relanza — loguea y guarda error."""
        if self._consolidador is None:
            return
        try:
            resultado.consolidacion = self._consolidador.consolidar_rango(
                desde=rango_desde, hasta=rango_hasta
            )
        except Exception as exc:  # noqa: BLE001 — boundary explícito; ver nota.
            # Regla del proyecto: "nunca Exception genérica". Aquí es un
            # boundary catch documentado: la consolidación tiene múltiples
            # caminos de falla (validación, BD, datos inconsistentes) y
            # NINGUNO debe borrar el estado OK de la sync ya persistida.
            # Se loguea stack completo y se guarda el mensaje en el
            # resultado para que la UI muestre el banner al usuario.
            logger.exception(
                "consolidacion.inline_fallo sincronizacion_id=%s",
                resultado.sincronizacion_id,
            )
            resultado.error_consolidacion = str(exc)


# ── Helper ────────────────────────────────────────────────────────────────────


def _now_iso_utc() -> str:
    """Devuelve el momento actual en ISO-8601 UTC con segundos."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
