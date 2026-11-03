"""Atajos de teclado de una vista (Ctrl+N, Ctrl+F…).

Los atajos se enganchan a la ventana principal para que funcionen sin
importar qué control tenga el foco, pero solo actúan mientras la vista
está en pantalla: al cambiar de módulo la vista queda guardada (oculta)
para volver rápido, y sus atajos no deben responder desde otro módulo.
Al destruir la vista hay que llamar a ``quitar()``. En Mac se enganchan
también con la tecla ⌘.

Los diálogos son ventanas aparte, así que los atajos no se disparan
mientras hay uno abierto.
"""

from __future__ import annotations

import sys
import tkinter as tk
from typing import Callable, List, Optional, Sequence, Tuple

ES_MAC = sys.platform == "darwin"
# Cómo se escribe el modificador en los textos de ayuda.
MOD = "⌘" if ES_MAC else "Ctrl+"
TECLA_BORRAR = "⌫" if ES_MAC else "Supr"
TECLA_ENTER = "↩" if ES_MAC else "Enter"


class Atajos:
    """Grupo de atajos enganchados a la ventana de ``widget``."""

    def __init__(self, widget: tk.Misc) -> None:
        self._widget = widget
        self._ventana = widget.winfo_toplevel()
        self._enganchados: List[Tuple[str, str]] = []

    def con_modificador(self, tecla: str, accion: Callable[[], None]) -> None:
        """Ctrl+``tecla`` (y ⌘+``tecla`` en Mac). ``tecla`` en minúscula: ``"n"``, ``"1"``."""
        # Con Bloq Mayús la letra llega en mayúscula: se enganchan las dos.
        teclas = {tecla, tecla.upper()}
        modificadores = ["Control", "Command"] if ES_MAC else ["Control"]
        self.agregar(
            [f"<{m}-Key-{t}>" for m in modificadores for t in sorted(teclas)],
            accion,
        )

    def agregar(self, secuencias: Sequence[str], accion: Callable[[], None]) -> None:
        def manejar(_evento: "tk.Event[tk.Misc]") -> Optional[str]:
            if not self._widget.winfo_viewable():
                return None  # la vista está oculta (otro módulo en pantalla)
            accion()
            return "break"

        for secuencia in secuencias:
            try:
                funcid = self._ventana.bind(secuencia, manejar, add="+")
            except tk.TclError:
                continue  # combinación que esta plataforma no conoce
            self._enganchados.append((secuencia, funcid))

    def quitar(self) -> None:
        """Desengancha solo los atajos de este grupo (no toca los demás)."""
        for secuencia, funcid in self._enganchados:
            try:
                script = str(self._ventana.bind(secuencia))
                restantes = "\n".join(
                    linea for linea in script.split("\n") if funcid not in linea
                ).strip()
                self._ventana.tk.call("bind", str(self._ventana), secuencia, restantes)
                self._ventana.deletecommand(funcid)
            except tk.TclError:
                pass  # la ventana ya se cerró
        self._enganchados.clear()
