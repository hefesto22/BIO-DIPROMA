"""Base de los diálogos modales con el estilo de la app.

Estructura fija para que todos los formularios se vean igual:

    ┌──────────────────────────────────────┐
    │ Título                               │
    │ Subtítulo (opcional)                 │
    │                                      │
    │ [ cuerpo: campos del formulario ]    │
    │                                      │
    │ mensaje de error (si lo hay)         │
    │                  [Cancelar] [Guardar]│
    └──────────────────────────────────────┘

El diálogo se centra sobre la ventana principal y toma el foco (modal).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

import customtkinter as ctk

from ui import theme


class DialogoModal(ctk.CTkToplevel):
    """Ventana modal con encabezado, cuerpo, error y botonera."""

    def __init__(
        self,
        parent: Any,
        titulo: str,
        subtitulo: str = "",
        ancho: int = 460,
    ) -> None:
        super().__init__(parent)
        self._log = logging.getLogger(self.__class__.__name__)
        self.title(titulo)
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.configure(fg_color=theme.TARJETA)
        self.grid_columnconfigure(0, weight=1, minsize=ancho)

        ctk.CTkLabel(
            self, text=titulo, font=theme.fuente(theme.TAM_SECCION + 3, "bold"), anchor="w"
        ).grid(row=0, column=0, sticky="ew", padx=24, pady=(22, 0))
        if subtitulo:
            ctk.CTkLabel(
                self,
                text=subtitulo,
                font=theme.fuente(theme.TAM_PEQUENO),
                text_color=theme.TEXTO_SUAVE,
                anchor="w",
                justify="left",
                wraplength=ancho - 48,
            ).grid(row=1, column=0, sticky="ew", padx=24, pady=(2, 0))

        self.cuerpo = ctk.CTkFrame(self, fg_color="transparent")
        self.cuerpo.grid(row=2, column=0, sticky="nsew", padx=24, pady=(16, 0))
        self.cuerpo.grid_columnconfigure(0, weight=1)

        self._error = ctk.CTkLabel(
            self,
            text="",
            text_color=theme.ERROR,
            font=theme.fuente(theme.TAM_PEQUENO),
            anchor="w",
            justify="left",
            wraplength=ancho - 48,
        )
        self._error.grid(row=3, column=0, sticky="ew", padx=24, pady=(10, 0))

        self._botonera = ctk.CTkFrame(self, fg_color="transparent")
        self._botonera.grid(row=4, column=0, sticky="e", padx=24, pady=(10, 22))
        self._cancelar: Optional[ctk.CTkButton] = None

        self.bind("<Escape>", lambda _e: self.cerrar())
        self.after(20, self._mostrar_modal)

    # ── API para las subclases ────────────────────────────────────────────

    def agregar_botones(
        self,
        texto_ok: str,
        on_ok: Callable[[], None],
        peligro: bool = False,
    ) -> ctk.CTkButton:
        """Agrega [Cancelar] [texto_ok]. Devuelve el botón principal."""
        self._cancelar = ctk.CTkButton(
            self._botonera,
            text="Cancelar",
            width=110,
            height=36,
            fg_color=theme.SECUNDARIO,
            hover_color=theme.SECUNDARIO_HOVER,
            text_color=theme.TEXTO,
            command=self.cerrar,
        )
        self._cancelar.grid(row=0, column=0, padx=(0, 8))
        boton = ctk.CTkButton(
            self._botonera,
            text=texto_ok,
            width=130,
            height=36,
            fg_color=theme.PELIGRO if peligro else theme.PRIMARIO,
            hover_color=theme.PELIGRO_HOVER if peligro else theme.PRIMARIO_HOVER,
            command=on_ok,
        )
        boton.grid(row=0, column=1)
        return boton

    def agregar_boton_cerrar(self, texto: str = "Listo") -> ctk.CTkButton:
        """Botonera de un solo botón (diálogos que guardan cada acción al momento)."""
        boton = ctk.CTkButton(
            self._botonera,
            text=texto,
            width=130,
            height=36,
            fg_color=theme.PRIMARIO,
            hover_color=theme.PRIMARIO_HOVER,
            command=self.cerrar,
        )
        boton.grid(row=0, column=0)
        return boton

    def mostrar_error(self, mensaje: Optional[str]) -> None:
        self._error.configure(text=mensaje or "", text_color=theme.ERROR)

    def mostrar_ok(self, mensaje: str) -> None:
        """Mensaje en verde en el mismo lugar del error (p. ej. "Guardado")."""
        self._error.configure(text=mensaje, text_color=theme.EXITO)

    def cancelar_ahora_es_listo(self) -> None:
        """Tras guardar algo con el diálogo abierto, "Cancelar" ya no deshace nada."""
        if self._cancelar is not None:
            self._cancelar.configure(text="Listo")

    def cerrar(self) -> None:
        try:
            self.grab_release()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()

    # ── Internos ──────────────────────────────────────────────────────────

    def _mostrar_modal(self) -> None:
        self._centrar()
        try:
            self.grab_set()
            self.focus_force()
        except Exception:  # noqa: BLE001
            self._log.debug("grab_set falló (ventana destruida)")

    def _centrar(self) -> None:
        self.update_idletasks()
        padre = self.master.winfo_toplevel()
        ancho, alto = self.winfo_reqwidth(), self.winfo_reqheight()
        x = padre.winfo_rootx() + max(0, (padre.winfo_width() - ancho) // 2)
        y = padre.winfo_rooty() + max(0, (padre.winfo_height() - alto) // 3)
        self.geometry(f"+{x}+{y}")
