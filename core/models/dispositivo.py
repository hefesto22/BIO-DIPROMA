"""Modelo de dominio: Dispositivo ZKTeco.

Representa un reloj checador físico (p.ej. sede principal, alcaldía, obras).
El diseño multi-dispositivo desde día 1 permite que el sistema escale sin
migraciones de datos cuando la municipalidad agregue relojes.

``Dispositivo`` NO es ``frozen`` — los flujos de edición (renombrar, cambiar
IP, archivar) lo mutan antes de persistir.

El puerto default 4370 es el estándar TCP de los ZKTeco (documentado en el
datasheet de pyzk). Se sobreescribe cuando el cliente usa otro puerto.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Optional

PUERTO_ZKTECO_DEFAULT: Final[int] = 4370

# Cómo se obtienen las marcas del reloj.
MODO_DIRECTO: Final[str] = "DIRECTO"  # la app se conecta al reloj y las pide
MODO_PUSH: Final[str] = "PUSH"  # el reloj las envía solo (ADMS / "Servidor en la nube")
MODOS: Final[tuple[str, ...]] = (MODO_DIRECTO, MODO_PUSH)


@dataclass
class Dispositivo:
    """Reloj ZKTeco configurado en el sistema.

    Atributos:
        id: PK en la tabla ``dispositivos``. ``None`` si aún no fue persistido.
        nombre: Nombre legible único para el operador. Ejemplos:
            "Sede Principal", "Alcaldía", "Bodega Obras Públicas".
        ip: Dirección IPv4 del reloj en la LAN. Texto libre validado por el
            servicio con ``ipaddress.ip_address`` (Fase 3.2).
        puerto: Puerto TCP del reloj. Default ``4370`` (estándar ZKTeco).
            Rango válido 1..65535.
        is_active: ``False`` archiva el dispositivo — deja de aparecer en el
            combo de sincronización pero preserva los registros históricos
            vinculados en ``registros_raw`` y ``sincronizaciones``.
        modo: ``MODO_DIRECTO`` (la app pide las marcas) o ``MODO_PUSH`` (el
            reloj las envía solo). Que el reloj sea de huella, de rostro o
            de ambos no importa: las marcas llegan igual.
        clave_comunicacion: "Comm Key" del reloj (0 = sin clave). Modo directo.
        usar_udp: Conectarse por UDP (relojes antiguos). Modo directo.
        numero_serie: Serie del reloj; identifica a un reloj PUSH.
        push_stamp: Última marca de agua de marcas recibida por push.
        ultimo_contacto: ISO-8601 UTC del último contacto por push.
    """

    id: Optional[int]
    nombre: str
    ip: str
    puerto: int = PUERTO_ZKTECO_DEFAULT
    is_active: bool = True
    modo: str = MODO_DIRECTO
    clave_comunicacion: int = 0
    usar_udp: bool = False
    numero_serie: Optional[str] = None
    push_stamp: Optional[str] = None
    ultimo_contacto: Optional[str] = None

    @property
    def es_push(self) -> bool:
        return self.modo == MODO_PUSH
