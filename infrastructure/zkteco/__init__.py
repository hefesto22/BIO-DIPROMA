"""Infrastructure — adapter ZKTeco.

Encapsula la comunicación TCP/IP con el dispositivo biométrico detrás de
``IZKTecoAdapter``. Los servicios de dominio (``core/services/``)
dependen EXCLUSIVAMENTE de la interfaz — nunca importan ``zk`` ni
``zk.exception`` directamente.

Exports:
    IZKTecoAdapter: contrato abstracto.
    PyzkAdapter: implementación real con pyzk.
    FakeZKTecoAdapter: in-memory para tests y smoke.
    ZKAdapterError + subclases: jerarquía de excepciones propia.
    map_status_to_tipo_marcada: utilidad de mapeo del código de firmware.
"""

from .exceptions import (
    ZKAdapterError,
    ZKConnectionError,
    ZKProtocolError,
    ZKTimeoutError,
)
from .fake_adapter import FakeZKTecoAdapter, PullCall
from .interface import IZKTecoAdapter
from .pyzk_adapter import PyzkAdapter
from .status_mapping import map_status_to_tipo_marcada

__all__ = [
    "IZKTecoAdapter",
    "PyzkAdapter",
    "FakeZKTecoAdapter",
    "PullCall",
    "ZKAdapterError",
    "ZKConnectionError",
    "ZKTimeoutError",
    "ZKProtocolError",
    "map_status_to_tipo_marcada",
]
