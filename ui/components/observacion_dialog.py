"""Diálogo modal ``ObservacionDialog``: nota manual sobre un día de asistencia.

    ┌ Observación ──────────────────────────────────────┐
    │ lunes 26 de octubre de 2026 · Karla Villeda        │
    │ ┌───────────────────────────────────────────────┐ │
    │ │ Llegó tarde por cita médica (presentó nota).   │ │
    │ └───────────────────────────────────────────────┘ │
    │                               [Cancelar] [Guardar]│
    └────────────────────────────────────────────────────┘

Contrato sin cambios: ``on_submit(texto)`` recibe el texto recortado
(vacío → ``None``) y devuelve ``None`` (cierra) o un ``str`` con el error.
La nota se conserva aunque se recalcule la asistencia.
"""

from __future__ import annotations

from typing import Callable, Optional

import customtkinter as ctk

from ui import theme
from ui.components.dialogo import DialogoModal

SubmitFn = Callable[[Optional[str]], Optional[str]]


class ObservacionDialog(DialogoModal):
    """Diálogo modal para escribir o borrar la observación de un día."""

    def __init__(
        self,
        parent: ctk.CTkBaseClass,
        subtitle: str,
        on_submit: SubmitFn,
        initial_text: Optional[str] = None,
    ) -> None:
        super().__init__(
            parent,
            titulo="Observación",
            subtitulo=f"{subtitle}\nLa nota se conserva aunque se vuelva a calcular el día.",
            ancho=480,
        )
        self._on_submit = on_submit
        self._texto = ctk.CTkTextbox(
            self.cuerpo,
            height=120,
            wrap="word",
            border_width=1,
            border_color=theme.BORDE,
            fg_color=theme.TARJETA_ALT,
            font=theme.fuente(theme.TAM_NORMAL),
        )
        self._texto.grid(row=0, column=0, sticky="ew")
        if initial_text:
            self._texto.insert("1.0", initial_text)
        self.agregar_botones("Guardar", self._guardar)
        self.after(80, self._texto.focus_set)

    def _guardar(self) -> None:
        texto = str(self._texto.get("1.0", "end")).strip()
        error = self._on_submit(texto or None)
        if error is None:
            self.cerrar()
        else:
            self.mostrar_error(error)
