"""Ajustes de rendimiento sobre customtkinter.

``CTkScrollbar`` y ``CTkOptionMenu`` llaman a ``update_idletasks()`` cada vez
que se redibujan. Eso obliga a Tk a resolver en el acto TODO lo pendiente de
la ventana (geometría y dibujo de cientos de widgets), y como se redibujan
muchas veces mientras se arma una pantalla, el trabajo se repite una y otra
vez: era la mayor parte del tiempo al cambiar de módulo.

Sin esa llamada, Tk dibuja igual en cuanto queda libre (milisegundos
después), una sola vez. Se aplica solo con la versión de customtkinter
probada; con otra, no se toca nada.
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable

import customtkinter as ctk

_VERSION_PROBADA = "5.2.2"
_log = logging.getLogger(__name__)
_aplicado = False


def _nada() -> None:
    """Reemplazo de ``update_idletasks`` mientras se redibuja."""


def _sin_forzar_dibujo(original: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(original)
    def envoltura(self: Any, *args: Any, **kwargs: Any) -> Any:
        canvas = self._canvas
        canvas.update_idletasks = _nada  # tapa el método solo en este canvas
        try:
            return original(self, *args, **kwargs)
        finally:
            del canvas.update_idletasks

    return envoltura


def aplicar() -> bool:
    """Aplica los ajustes una sola vez. Devuelve ``True`` si quedaron aplicados."""
    global _aplicado
    if _aplicado:
        return True
    if getattr(ctk, "__version__", "") != _VERSION_PROBADA:
        _log.warning(
            "customtkinter %s: no se aplican los ajustes de rendimiento (probados con %s).",
            getattr(ctk, "__version__", "?"),
            _VERSION_PROBADA,
        )
        return False
    for clase in (ctk.CTkScrollbar, ctk.CTkOptionMenu):
        clase._draw = _sin_forzar_dibujo(clase._draw)
    _aplicado = True
    return True
