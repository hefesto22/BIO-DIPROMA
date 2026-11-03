"""Interfaz abstracta del adapter ZKTeco.

El servicio de sincronización (Fase 3.3) depende EXCLUSIVAMENTE de esta
interfaz — nunca importa ``zk`` directamente. Esto permite:

    1. Testear la consolidación con ``FakeZKTecoAdapter`` sin hardware.
    2. Migrar a otra librería de comunicación TCP sin tocar core/.
    3. Atrapar errores como ``ZKAdapterError`` en vez de ``zk.exception.*``.

Contratos clave del método ``pull_attendance``:
    - UN llamado = UNA conexión completa. El adapter conecta, descarga,
      desconecta en un try/finally. El caller NO gestiona el ciclo de
      vida de la sesión. (D3: Opción C aprobada — YAGNI sobre sesiones
      compartidas.)
    - El ``sincronizacion_id`` DEBE venir ya creado en BD; el servicio
      crea la fila ``sincronizaciones`` primero, obtiene el id, y lo
      pasa acá. Así los ``RegistroRaw`` devueltos están completos y
      listos para persistir sin post-procesamiento.
    - El rango ``desde``/``hasta`` es INCLUSIVE en ambos extremos,
      usando la fecha del reloj (no UTC — pyzk no reporta timezone).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import List

from core.models.dispositivo import Dispositivo
from core.models.info_reloj import InfoReloj
from core.models.registro_raw import RegistroRaw


class IZKTecoAdapter(ABC):
    """Contrato para descargar marcadas de un dispositivo ZKTeco.

    Las implementaciones son stateless entre llamadas: cada
    ``pull_attendance`` abre y cierra su propia conexión.
    """

    @abstractmethod
    def pull_attendance(
        self,
        dispositivo: Dispositivo,
        sincronizacion_id: int,
        desde: date,
        hasta: date,
    ) -> List[RegistroRaw]:
        """Conecta al dispositivo, descarga marcadas y desconecta.

        Args:
            dispositivo: Reloj desde el que leer. Debe estar persistido
                (``dispositivo.id`` no puede ser ``None``) y tener
                ``ip``/``puerto`` válidos.
            sincronizacion_id: PK de la fila ``sincronizaciones`` bajo la
                cual se agruparán los registros. El servicio la crea
                antes de llamar.
            desde: Fecha inicial inclusive (hora local del reloj).
            hasta: Fecha final inclusive.

        Returns:
            Lista de ``RegistroRaw`` con ``dispositivo_id``,
            ``sincronizacion_id``, ``zkteco_user_id``, ``timestamp`` y
            ``tipo_marcada`` rellenos. El campo ``id`` queda ``None``
            hasta que el repositorio los persista. La lista puede estar
            vacía si no hay marcadas en el rango.

        Raises:
            ZKConnectionError: No se pudo establecer conexión (red, IP,
                puerto, firewall, device apagado).
            ZKTimeoutError: El reloj dejó de responder dentro del
                timeout configurado.
            ZKProtocolError: Respuesta malformada o firmware inesperado.
            ValueError: Si ``dispositivo.id`` es ``None`` o el rango
                está invertido (``desde > hasta``).
        """

    @abstractmethod
    def leer_info(self, dispositivo: Dispositivo) -> InfoReloj:
        """Conecta, lee los datos del reloj (hora, usuarios, memoria) y desconecta.

        Raises:
            ZKConnectionError, ZKTimeoutError, ZKProtocolError: Igual que
                ``pull_attendance``.
        """

    @abstractmethod
    def poner_en_hora(self, dispositivo: Dispositivo, momento: datetime) -> None:
        """Conecta, ajusta la fecha y hora del reloj a ``momento`` y desconecta.

        Raises:
            ZKConnectionError, ZKTimeoutError, ZKProtocolError: Igual que
                ``pull_attendance``.
        """
