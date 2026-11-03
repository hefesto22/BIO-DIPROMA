"""Recepción de marcas enviadas por los relojes en modo PUSH (ADMS).

Muchos relojes ZKTeco nuevos (sobre todo los de reconocimiento facial:
SpeedFace, ProFace, SenseFace…) no esperan a que la app les pida las
marcas: las envían solos, por HTTP, a la dirección configurada en el reloj
("Servidor en la nube" / ADMS). El servidor HTTP vive en
``infrastructure/zkteco/servidor_push.py``; este servicio decide qué hacer
con lo que llega:

    - Reconoce al reloj por su número de serie. Si no está registrado (o
      está archivado) NO guarda nada y lo anota como "detectado", para que
      en Configuración → Relojes se pueda agregar con un clic. Como el
      servidor le contesta con error, el reloj conserva las marcas y las
      vuelve a enviar después.
    - Guarda las marcas en ``registros_raw`` (con el mismo deduplicado de
      siempre) bajo una "sincronización" automática (sin usuario), y
      recalcula la asistencia de los días que llegaron.
    - Recuerda la marca de agua (``Stamp``) que manda el reloj para
      devolvérsela al reconectarse y que no repita lo ya enviado.

Que el reloj marque con huella, rostro o tarjeta no cambia nada: cada
marca trae el ID del empleado en el reloj, la fecha y la hora.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from core.models.dispositivo import Dispositivo
from core.models.registro_raw import RegistroRaw
from core.models.sincronizacion import Sincronizacion
from core.repositories.dispositivo_repository import (
    IDispositivoReadRepository,
    IDispositivoWriteRepository,
)
from core.repositories.registro_raw_repository import IRegistroRawWriteRepository
from core.repositories.sincronizacion_repository import ISincronizacionWriteRepository
from core.services.sincronizacion_service import IConsolidadorDeRango
from infrastructure.zkteco.status_mapping import map_status_to_tipo_marcada

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MarcaPush:
    """Una marca tal como la envía el reloj.

    Atributos:
        user_id: ID del empleado en el reloj (el ``zkteco_id``). Texto.
        momento: Fecha y hora local del reloj.
        estado: Código de entrada/salida del firmware (0 entrada, 1 salida…).
        verificacion: Cómo se identificó (1 huella, 15 rostro, 4 tarjeta…).
    """

    user_id: str
    momento: datetime
    estado: int = 0
    verificacion: int = 0


@dataclass(frozen=True)
class Saludo:
    """Respuesta al contacto de un reloj."""

    registrado: bool
    stamp: Optional[str] = None


@dataclass(frozen=True)
class RelojDetectado:
    """Reloj que intenta enviar marcas pero no está registrado en la app."""

    numero_serie: str
    ip: str
    visto: datetime  # UTC
    con_marcas: bool  # ya intentó enviar marcas (las conserva hasta que se registre)


@dataclass
class _Lote:
    """Sincronización automática abierta para un reloj en un día."""

    sincronizacion_id: int
    desde: str
    hasta: str
    total: int


def _utc_ahora() -> datetime:
    return datetime.now(timezone.utc)


class RecepcionPushService:
    """Guarda lo que envían los relojes PUSH."""

    def __init__(
        self,
        dispositivo_read: IDispositivoReadRepository,
        dispositivo_write: IDispositivoWriteRepository,
        sincronizacion_write: ISincronizacionWriteRepository,
        registro_raw_write: IRegistroRawWriteRepository,
        consolidador: Optional[IConsolidadorDeRango] = None,
        ahora: Callable[[], datetime] = _utc_ahora,
    ) -> None:
        """Inicializa el servicio.

        Args:
            dispositivo_read: Para reconocer al reloj por su serie.
            dispositivo_write: Para guardar el último contacto y el stamp.
            sincronizacion_write: Cabeceras de las sincronizaciones automáticas.
            registro_raw_write: Inserción (deduplicada) de las marcas.
            consolidador: Recalcula la asistencia de los días recibidos.
            ahora: Reloj UTC inyectable para tests.
        """
        self._disp_read = dispositivo_read
        self._disp_write = dispositivo_write
        self._sync_write = sincronizacion_write
        self._raw_write = registro_raw_write
        self._consolidador = consolidador
        self._ahora = ahora
        # Varios relojes pueden enviar a la vez (un hilo por pedido): se
        # procesa de a uno para no pisar la consolidación del mismo día.
        self._candado = threading.Lock()
        self._detectados: Dict[str, RelojDetectado] = {}
        self._lotes: Dict[Tuple[int, str], _Lote] = {}

    # ── Lo que llama el servidor HTTP ─────────────────────────────────────

    def saludo(self, numero_serie: str, ip: str) -> Saludo:
        """Contacto del reloj (al conectarse y periódicamente)."""
        with self._candado:
            reloj = self._reloj(numero_serie)
            if reloj is None or reloj.id is None:
                self._detectar(numero_serie, ip, con_marcas=False)
                return Saludo(registrado=False)
            self._disp_write.registrar_contacto(reloj.id, self._iso_ahora())
            return Saludo(registrado=True, stamp=reloj.push_stamp)

    def recibir_marcas(
        self,
        numero_serie: str,
        ip: str,
        marcas: Sequence[MarcaPush],
        stamp: Optional[str] = None,
    ) -> Optional[int]:
        """Guarda un lote de marcas.

        Returns:
            Cuántas marcas nuevas se guardaron, o ``None`` si el reloj no
            está registrado (o está archivado): en ese caso no se guarda
            nada y el servidor le contesta con error para que las reenvíe.
        """
        with self._candado:
            reloj = self._reloj(numero_serie)
            if reloj is None or reloj.id is None:
                self._detectar(numero_serie, ip, con_marcas=True)
                return None
            ahora = self._iso_ahora()
            validas = [m for m in marcas if m.user_id.strip().isdigit()]
            if len(validas) < len(marcas):
                _log.warning(
                    "Reloj %s envió %s marcas con un ID no numérico (se ignoran).",
                    numero_serie,
                    len(marcas) - len(validas),
                )
            insertadas = 0
            if validas:
                insertadas = self._guardar(reloj, validas)
            self._disp_write.registrar_contacto(reloj.id, ahora, stamp)
            return insertadas

    # ── Para la pantalla de Relojes ───────────────────────────────────────

    def detectados(self) -> List[RelojDetectado]:
        """Relojes que se conectaron sin estar registrados (los más recientes primero)."""
        with self._candado:
            vigentes = [d for d in self._detectados.values() if self._reloj(d.numero_serie) is None]
        return sorted(vigentes, key=lambda d: d.visto, reverse=True)

    # ── Internos ──────────────────────────────────────────────────────────

    def _reloj(self, numero_serie: str) -> Optional[Dispositivo]:
        serie = numero_serie.strip()
        if not serie:
            return None
        reloj = self._disp_read.get_by_numero_serie(serie)
        if reloj is None or not reloj.is_active:
            return None
        return reloj

    def _detectar(self, numero_serie: str, ip: str, con_marcas: bool) -> None:
        serie = numero_serie.strip()
        if not serie:
            return
        previo = self._detectados.get(serie)
        self._detectados[serie] = RelojDetectado(
            numero_serie=serie,
            ip=ip,
            visto=self._ahora(),
            con_marcas=con_marcas or (previo is not None and previo.con_marcas),
        )
        if previo is None:
            _log.info("Reloj no registrado se conectó: serie=%s ip=%s", serie, ip)

    def _guardar(self, reloj: Dispositivo, marcas: Sequence[MarcaPush]) -> int:
        assert reloj.id is not None
        fechas = sorted(m.momento.date().isoformat() for m in marcas)
        desde, hasta = fechas[0], fechas[-1]
        lote = self._lote(reloj.id, desde, hasta)
        registros = [
            RegistroRaw(
                id=None,
                dispositivo_id=reloj.id,
                sincronizacion_id=lote.sincronizacion_id,
                zkteco_user_id=int(m.user_id.strip()),
                timestamp=m.momento.replace(microsecond=0).isoformat(),
                tipo_marcada=map_status_to_tipo_marcada(m.estado).value,
            )
            for m in marcas
        ]
        insertadas = self._raw_write.create_bulk(registros)
        lote.total += insertadas
        self._sync_write.marcar_ok(lote.sincronizacion_id, self._iso_ahora(), lote.total)
        _log.info(
            "Push de %s: %s marcas recibidas, %s nuevas (%s a %s).",
            reloj.nombre,
            len(registros),
            insertadas,
            desde,
            hasta,
        )
        if insertadas and self._consolidador is not None:
            try:
                self._consolidador.consolidar_rango(desde=desde, hasta=hasta)
            except Exception:  # noqa: BLE001 — las marcas ya quedaron guardadas
                _log.exception("No se pudo recalcular la asistencia tras un push.")
        return insertadas

    def _lote(self, dispositivo_id: int, desde: str, hasta: str) -> _Lote:
        """Una sincronización automática por reloj y día (no una por cada marca).

        Si el lote del día no cubre las fechas recibidas (p. ej. la primera
        vez el reloj envía su historial), se abre otro.
        """
        hoy = datetime.now().date().isoformat()
        clave = (dispositivo_id, hoy)
        lote = self._lotes.get(clave)
        if lote is not None and lote.desde <= desde and hasta <= lote.hasta:
            return lote
        inicio = self._iso_ahora()
        creada = self._sync_write.create(
            Sincronizacion(
                id=None,
                dispositivo_id=dispositivo_id,
                iniciada_por_user_id=None,
                inicio=inicio,
                rango_desde=min(desde, hoy),
                rango_hasta=max(hasta, hoy),
            )
        )
        assert creada.id is not None
        lote = _Lote(creada.id, min(desde, hoy), max(hasta, hoy), 0)
        self._lotes[clave] = lote
        return lote

    def _iso_ahora(self) -> str:
        return self._ahora().astimezone(timezone.utc).replace(microsecond=0).isoformat()
