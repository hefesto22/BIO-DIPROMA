"""Controller de la vista de Sincronización ZKTeco (Sub-3.4a).

Orquesta tres responsabilidades — todas con guard ``RUN_ZKTECO_SYNC``:

    1. Listar dispositivos activos para poblar el combo del form.
    2. Calcular un rango sugerido por dispositivo: desde el ``rango_hasta``
       de la última sync ``OK`` → hoy. Fallback: últimos 7 días.
    3. Ejecutar la sincronización propiamente dicha, delegando en
       ``SincronizacionService`` (que ya maneja cabecera + pull + cierre +
       consolidación in-line).

El controller NO sabe de Tk/customtkinter. Expone métodos sincrónicos —
la vista los envuelve con ``run_async_ui`` para no bloquear el UI thread
durante el pull contra el reloj (que puede tardar varios segundos).

Campo ``aviso_recuperacion`` (no decorado): lo inyecta el composition
root con el conteo de syncs huérfanas cerradas al arrancar la app. La
vista lo consume al montarse la primera vez — luego se limpia a ``None``
para que un logout/login no vuelva a dispararlo.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Dict, List, Optional, Set, Tuple

from core.models import permissions as perms
from core.models.dispositivo import Dispositivo
from core.models.sincronizacion import EstadoSincronizacion, Sincronizacion
from core.repositories.dispositivo_repository import IDispositivoReadRepository
from core.repositories.sincronizacion_repository import ISincronizacionReadRepository
from core.services.permission_service import PermissionService, require_permission
from core.services.session import Session
from core.services.sincronizacion_result import ResultadoSincronizacion
from core.services.sincronizacion_service import SincronizacionService

# Cuántos días hacia atrás se propone si nunca se ha sincronizado el
# dispositivo. Elegido en el plan aprobado para Sub-3.4a.
_DIAS_FALLBACK_RANGO_DEFAULT: int = 7

# Cuántas filas del historial recorremos buscando la última OK antes de
# darnos por vencidos. 20 cubre sobradamente el típico uso diario.
_LIMITE_BUSQUEDA_ULTIMA_OK: int = 20


class SincronizacionController:
    """Controller de la vista de Sincronización ZKTeco."""

    def __init__(
        self,
        session: Session,
        permission_service: PermissionService,
        sincronizacion_service: SincronizacionService,
        dispositivo_read: IDispositivoReadRepository,
        sincronizacion_read: ISincronizacionReadRepository,
    ) -> None:
        """Inicializa el controller.

        Args:
            session: Sesión activa del usuario.
            permission_service: Verificador usado por ``@require_permission``.
            sincronizacion_service: Servicio orquestador de Sub-3.3b.
            dispositivo_read: Repo de lectura — lista dispositivos activos.
            sincronizacion_read: Repo de lectura — busca última sync OK.
        """
        # Nombres obligatorios para el decorador ``@require_permission``.
        self.session = session
        self.permission_service = permission_service
        self._sync = sincronizacion_service
        self._dispositivo_read = dispositivo_read
        self._sync_read = sincronizacion_read
        self._log = logging.getLogger(self.__class__.__name__)

        # Inyectado por el composition root tras el recover al arrancar.
        # La vista lo consume una vez y lo limpia.
        self.aviso_recuperacion: Optional[int] = None

    # ── Reads ────────────────────────────────────────────────────────────────

    @require_permission(perms.RUN_ZKTECO_SYNC)
    def list_dispositivos_activos(self) -> List[Dispositivo]:
        """Devuelve los dispositivos ``is_active = True`` para el combo."""
        return self._dispositivo_read.list_active()

    @require_permission(perms.RUN_ZKTECO_SYNC)
    def calcular_rango_default(
        self,
        dispositivo_id: int,
        hoy: Optional[date] = None,
    ) -> Tuple[str, str]:
        """Sugiere el rango inicial del form para un dispositivo.

        Args:
            dispositivo_id: PK del dispositivo a consultar.
            hoy: Inyectable para tests; por defecto ``date.today()``.

        Returns:
            Tupla ``(desde_iso, hasta_iso)`` en formato ``YYYY-MM-DD``.
            Si el dispositivo tiene historial OK, ``desde`` es el
            ``rango_hasta`` de la última sync exitosa (overlap intencional:
            el UPSERT idempotente no duplica). Si no, ``desde = hoy - 7 días``.
            ``hasta`` siempre es ``hoy``.
        """
        hoy_ = hoy or date.today()
        ultima_ok = self._buscar_ultima_sync_ok(dispositivo_id)
        if ultima_ok is not None:
            return ultima_ok, hoy_.isoformat()
        fallback = hoy_ - timedelta(days=_DIAS_FALLBACK_RANGO_DEFAULT)
        return fallback.isoformat(), hoy_.isoformat()

    @require_permission(perms.RUN_ZKTECO_SYNC)
    def historial(self, limit: int = 50) -> List[Sincronizacion]:
        """Últimas sincronizaciones (de todos los relojes), la más reciente primero."""
        return self._sync_read.list_recientes(limit=limit)

    @require_permission(perms.RUN_ZKTECO_SYNC)
    def nombres_dispositivos(self) -> Dict[int, str]:
        """``{dispositivo_id: nombre}`` incluyendo relojes archivados (para el historial)."""
        return {d.id: d.nombre for d in self._dispositivo_read.list_all() if d.id is not None}

    def relojes_push(self) -> Set[int]:
        """Ids de los relojes que envían las marcas solos (no se sincronizan a mano)."""
        return {d.id for d in self._dispositivo_read.list_all() if d.id is not None and d.es_push}

    # ── Writes ───────────────────────────────────────────────────────────────

    @require_permission(perms.RUN_ZKTECO_SYNC)
    def ejecutar_sincronizacion(
        self,
        dispositivo_id: int,
        rango_desde: str,
        rango_hasta: str,
    ) -> ResultadoSincronizacion:
        """Ejecuta la sincronización completa y devuelve el resultado.

        La vista debe envolver esta llamada con ``run_async_ui`` para no
        bloquear el UI thread — el pull contra el reloj puede tardar
        varios segundos (regla del proyecto: I/O fuera del main thread).

        Raises:
            DispositivoNotFoundError: dispositivo inexistente.
            DispositivoInactiveError: dispositivo archivado.
            InvalidRangoError: rango invertido.
            InvalidDateError: fechas con formato inválido.
            ZKAdapterError: fallo del adapter (red, timeout, protocolo).
        """
        return self._sync.ejecutar(
            dispositivo_id=dispositivo_id,
            rango_desde=rango_desde,
            rango_hasta=rango_hasta,
            iniciada_por_user_id=self.session.user_id,
        )

    # ── Aviso de recuperación (sin permiso: puro estado local) ──────────────

    def consumir_aviso_recuperacion(self) -> Optional[int]:
        """Devuelve el aviso pendiente y lo limpia.

        La vista lo invoca al montarse. Si hay un valor ``>= 1``, pinta
        un banner informativo; si es ``None`` o ``0``, no muestra nada.
        Tras el primer consumo, vuelve a ``None`` para que un
        logout/login no dispare el banner dos veces.
        """
        valor = self.aviso_recuperacion
        self.aviso_recuperacion = None
        return valor

    # ── Helpers privados ────────────────────────────────────────────────────

    def _buscar_ultima_sync_ok(self, dispositivo_id: int) -> Optional[str]:
        """Busca el ``rango_hasta`` de la última sync OK del dispositivo.

        Returns:
            String ISO ``YYYY-MM-DD`` si hay una OK en el historial reciente;
            ``None`` si no. Se ignoran filas con fechas corruptas (defensivo).
        """
        historial = self._sync_read.list_by_dispositivo(
            dispositivo_id, limit=_LIMITE_BUSQUEDA_ULTIMA_OK
        )
        for sync in historial:
            if sync.estado != EstadoSincronizacion.OK.value:
                continue
            try:
                date.fromisoformat(sync.rango_hasta)
            except ValueError:
                self._log.warning(
                    "sync.rango_hasta inválido id=%s valor=%r — ignorando",
                    sync.id,
                    sync.rango_hasta,
                )
                continue
            return sync.rango_hasta
        return None
