"""Implementación en memoria de ``IZKTecoAdapter`` para tests y smoke sin hardware.

Usos:
    1. Tests unitarios de servicios de Fase 3.3 (consolidación) — permite
       pre-cargar marcadas sintéticas y verificar el flujo sin un reloj.
    2. Smoke tests de la UI de sincronización — se puede correr la app
       en un equipo sin dispositivo ZKTeco conectado.
    3. Demos para stakeholders.

Responsabilidades:
    - ``preload_attendance``: agregar marcadas simuladas al "buffer"
      interno, cada una con su ``dispositivo_id`` + ``status`` crudo
      (entero, NO ``TipoMarcada``). Replica el flujo real: el mapeo
      status→TipoMarcada lo hace el propio ``pull_attendance`` usando el
      mismo ``status_mapping`` que el adapter real.
    - ``set_next_error``: dispara la excepción indicada en el próximo
      ``pull_attendance`` y luego se auto-resetea. Permite testear el
      manejo de errores en el servicio sin romper el test siguiente.
    - ``pulls``: lista de tuplas (dispositivo_id, sincronizacion_id,
      desde, hasta) con cada invocación — para aserciones en tests.

NO se usa en producción. Alineado con el patrón del proyecto "stubs
manuales en vez de MagicMock".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import List, Optional, Tuple

from core.models.dispositivo import Dispositivo
from core.models.info_reloj import InfoReloj
from core.models.registro_raw import RegistroRaw

from .exceptions import ZKAdapterError
from .interface import IZKTecoAdapter
from .status_mapping import map_status_to_tipo_marcada


@dataclass(frozen=True)
class _PreloadedAttendance:
    """Marcada sintética almacenada en el buffer interno del fake."""

    dispositivo_id: int
    zkteco_user_id: int
    timestamp: datetime
    status: int


@dataclass(frozen=True)
class PullCall:
    """Registro de una invocación a ``pull_attendance`` — auditoría de tests."""

    dispositivo_id: int
    sincronizacion_id: int
    desde: date
    hasta: date


class FakeZKTecoAdapter(IZKTecoAdapter):
    """Adapter en memoria. NO toca red ni pyzk."""

    def __init__(self) -> None:
        self._preloaded: List[_PreloadedAttendance] = []
        self._next_error: Optional[ZKAdapterError] = None
        self._pulls: List[PullCall] = []
        # Lo que devuelve ``leer_info`` (los tests lo reemplazan a gusto).
        self.info = InfoReloj(nombre_equipo="Reloj de prueba")
        # ``(dispositivo_id, momento)`` de cada ``poner_en_hora``.
        self.horas_puestas: List[Tuple[int, datetime]] = []

    # ── Control del test (setup) ──────────────────────────────────────

    def preload_attendance(
        self,
        *,
        dispositivo_id: int,
        zkteco_user_id: int,
        timestamp: datetime,
        status: int,
    ) -> None:
        """Agrega una marcada simulada al "buffer" del reloj.

        Args:
            dispositivo_id: Reloj dueño de la marcada.
            zkteco_user_id: ID del usuario en el reloj.
            timestamp: Momento de la marcada.
            status: Código crudo del firmware (0/1/4/5/otro). Se
                mapeará a ``TipoMarcada`` al hacer ``pull_attendance``,
                igual que el adapter real.
        """
        self._preloaded.append(
            _PreloadedAttendance(
                dispositivo_id=dispositivo_id,
                zkteco_user_id=zkteco_user_id,
                timestamp=timestamp,
                status=status,
            )
        )

    def set_next_error(self, error: ZKAdapterError) -> None:
        """Hace que el PRÓXIMO ``pull_attendance`` lance ``error``.

        Luego el fake se auto-resetea al estado normal. Diseñado para
        escenarios "simular fallo de red una vez, luego recuperar".

        Args:
            error: Instancia de ``ZKAdapterError`` (o subclase) a lanzar.
        """
        self._next_error = error

    def clear(self) -> None:
        """Vacía buffer, error pendiente e historial de llamadas."""
        self._preloaded.clear()
        self._next_error = None
        self._pulls.clear()
        self.horas_puestas.clear()

    # ── Auditoría (assertions) ────────────────────────────────────────

    @property
    def pulls(self) -> List[PullCall]:
        """Historial de invocaciones a ``pull_attendance`` (read-only view)."""
        return list(self._pulls)

    # ── Contrato IZKTecoAdapter ───────────────────────────────────────

    def pull_attendance(
        self,
        dispositivo: Dispositivo,
        sincronizacion_id: int,
        desde: date,
        hasta: date,
    ) -> List[RegistroRaw]:
        """Simula el flujo real: valida, (opcionalmente) lanza, filtra, mapea."""
        if dispositivo.id is None:
            raise ValueError("Dispositivo debe estar persistido (id requerido)")
        if desde > hasta:
            raise ValueError(f"Rango invertido: desde={desde} > hasta={hasta}")

        self._pulls.append(
            PullCall(
                dispositivo_id=dispositivo.id,
                sincronizacion_id=sincronizacion_id,
                desde=desde,
                hasta=hasta,
            )
        )

        if self._next_error is not None:
            error = self._next_error
            self._next_error = None
            raise error

        desde_dt = datetime.combine(desde, time.min)
        hasta_dt = datetime.combine(hasta, time.max)
        resultado: List[RegistroRaw] = []
        for att in self._preloaded:
            if att.dispositivo_id != dispositivo.id:
                continue
            if att.timestamp < desde_dt or att.timestamp > hasta_dt:
                continue
            tipo = map_status_to_tipo_marcada(att.status)
            resultado.append(
                RegistroRaw(
                    id=None,
                    dispositivo_id=dispositivo.id,
                    sincronizacion_id=sincronizacion_id,
                    zkteco_user_id=att.zkteco_user_id,
                    timestamp=att.timestamp.isoformat(timespec="seconds"),
                    tipo_marcada=tipo.value,
                )
            )
        return resultado

    def leer_info(self, dispositivo: Dispositivo) -> InfoReloj:
        """Devuelve ``self.info`` (o lanza el error pendiente)."""
        self._lanzar_error_pendiente()
        return self.info

    def poner_en_hora(self, dispositivo: Dispositivo, momento: datetime) -> None:
        """Registra el ajuste en ``horas_puestas`` (o lanza el error pendiente)."""
        self._lanzar_error_pendiente()
        self.horas_puestas.append((dispositivo.id or 0, momento))
        self.info = InfoReloj(
            nombre_equipo=self.info.nombre_equipo,
            serie=self.info.serie,
            firmware=self.info.firmware,
            hora=momento,
            usuarios=self.info.usuarios,
            marcas=self.info.marcas,
            capacidad_marcas=self.info.capacidad_marcas,
        )

    def _lanzar_error_pendiente(self) -> None:
        if self._next_error is not None:
            error, self._next_error = self._next_error, None
            raise error
