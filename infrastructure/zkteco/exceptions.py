"""Jerarquía de excepciones propias del adapter ZKTeco.

Los servicios de dominio (``core/services/``) deben atrapar tipos de este
módulo — NUNCA ``zk.exception.*`` de la librería externa. Ese desacople es
el propósito entero del adapter: si mañana cambiamos ``pyzk`` por otra
biblioteca, los servicios no se ven afectados.

Jerarquía:
    ZKAdapterError (base)
      ├── ZKConnectionError  — red, device apagado, IP/puerto incorrectos
      ├── ZKTimeoutError     — el reloj no respondió a tiempo
      └── ZKProtocolError    — respuesta corrupta o firmware inesperado

Todas llevan un mensaje en español dirigido al operador. Los detalles
técnicos de ``pyzk`` quedan en ``__cause__`` gracias al ``raise ... from``
del adapter real.
"""

from __future__ import annotations


class ZKAdapterError(Exception):
    """Excepción base — cualquier falla del adapter ZKTeco hereda de aquí.

    Los callers deben atrapar esta clase (o sus subclases) para manejar
    errores del reloj sin acoplarse a la librería ``pyzk``.
    """


class ZKConnectionError(ZKAdapterError):
    """No se pudo establecer comunicación con el dispositivo.

    Causas típicas: red caída, dispositivo apagado, IP o puerto
    incorrectos, firewall bloqueando el puerto 4370.
    """


class ZKTimeoutError(ZKAdapterError):
    """El dispositivo no respondió dentro del timeout configurado.

    La conexión TCP se estableció o estaba intentándose, pero el reloj
    dejó de contestar. Suele indicar red lenta, dispositivo saturado o
    cable intermitente.
    """


class ZKProtocolError(ZKAdapterError):
    """Respuesta del dispositivo corrupta o no reconocida.

    Indica incompatibilidad de firmware o paquete TCP malformado. El
    registro puntual se pierde; el operador debe revisar la versión del
    reloj o reportarlo a soporte.
    """
