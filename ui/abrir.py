"""Abrir un archivo o una carpeta con el programa del sistema.

Windows (destino real): ``os.startfile`` abre el Excel o el Explorador.
macOS y Linux (desarrollo): ``open`` / ``xdg-open``.
"""

from __future__ import annotations

import logging
import os
import subprocess  # noqa: S404 — rutas controladas por la app
import sys

_log = logging.getLogger(__name__)


def abrir(ruta: str) -> bool:
    """Abre ``ruta`` (archivo o carpeta). Devuelve ``False`` si no se pudo."""
    if not os.path.exists(ruta):
        return False
    try:
        if hasattr(os, "startfile"):
            os.startfile(ruta)  # noqa: S606 — Windows
            return True
        comando = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen([comando, ruta])  # noqa: S603
        return True
    except OSError:
        _log.exception("No se pudo abrir %s", ruta)
        return False


def abrir_carpeta_de(ruta_archivo: str) -> bool:
    """Abre la carpeta que contiene el archivo."""
    return abrir(os.path.dirname(ruta_archivo) or os.getcwd())
