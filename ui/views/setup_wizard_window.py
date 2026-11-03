"""Vista del Setup Wizard — primera pantalla del primer arranque.

Recibe un ``SetupController`` pre-instanciado y un callback
``on_success`` para notificar al router que el SUPERADMIN se creó.

Política UX:
    - Todos los campos visibles al mismo tiempo (un solo formulario,
      no un stepper). Son pocos datos y el usuario típico es un admin
      de IT, no un usuario final.
    - Errores de validación se muestran en un label rojo bajo el botón,
      NUNCA en un popup modal (más fluido).
    - Botón de "Crear" se deshabilita mientras el service trabaja
      (el hash bcrypt a cost=12 tarda ~250ms).
"""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from core.models.usuario import Usuario
from ui.controllers.setup_controller import SetupController


class SetupWizardFrame(ctk.CTkFrame):
    """Frame con el formulario del wizard."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        controller: SetupController,
        on_success: Callable[[Usuario], None],
    ) -> None:
        """Construye el frame del wizard.

        Args:
            master: Ventana raíz donde se monta.
            controller: Controller con ``try_create_superadmin``.
            on_success: Se llama con el ``Usuario`` creado para que el
                router avance al login.
        """
        super().__init__(master, corner_radius=0)
        self._controller = controller
        self._on_success = on_success

        self._construir_ui()

    # ── Construcción del árbol de widgets ─────────────────────────────────

    def _construir_ui(self) -> None:
        contenedor = ctk.CTkFrame(self, corner_radius=12)
        contenedor.place(relx=0.5, rely=0.5, anchor="center")

        titulo = ctk.CTkLabel(
            contenedor,
            text="Configuración inicial",
            font=ctk.CTkFont(size=22, weight="bold"),
        )
        titulo.grid(row=0, column=0, columnspan=2, padx=32, pady=(28, 4))

        subtitulo = ctk.CTkLabel(
            contenedor,
            text="Se creará el único SUPERADMIN del sistema.",
            font=ctk.CTkFont(size=13),
            text_color=("gray30", "gray70"),
        )
        subtitulo.grid(row=1, column=0, columnspan=2, padx=32, pady=(0, 20))

        self._username = self._campo(contenedor, 2, "Nombre de usuario")
        self._full_name = self._campo(contenedor, 3, "Nombre completo")
        self._password = self._campo(contenedor, 4, "Contraseña", show="•")
        self._password_confirm = self._campo(contenedor, 5, "Confirmar contraseña", show="•")

        self._error_label = ctk.CTkLabel(
            contenedor,
            text="",
            text_color="#c0392b",
            font=ctk.CTkFont(size=12),
            wraplength=340,
            justify="left",
        )
        self._error_label.grid(row=6, column=0, columnspan=2, padx=32, pady=(8, 0))

        self._btn_crear = ctk.CTkButton(
            contenedor,
            text="Crear SUPERADMIN",
            command=self._on_btn_crear,
            height=38,
        )
        self._btn_crear.grid(row=7, column=0, columnspan=2, padx=32, pady=(14, 28), sticky="ew")

    def _campo(
        self, parent: ctk.CTkBaseClass, row: int, label: str, show: str = ""
    ) -> ctk.CTkEntry:
        """Helper: label + entry en una fila del grid."""
        lbl = ctk.CTkLabel(parent, text=label, font=ctk.CTkFont(size=12))
        lbl.grid(row=row, column=0, padx=(32, 8), pady=4, sticky="w")
        entry = ctk.CTkEntry(parent, width=220, show=show)
        entry.grid(row=row, column=1, padx=(0, 32), pady=4, sticky="ew")
        return entry

    # ── Handlers ──────────────────────────────────────────────────────────

    def _on_btn_crear(self) -> None:
        """Click del botón: valida confirmación y delega al controller."""
        self._mostrar_error("")

        password = self._password.get()
        confirm = self._password_confirm.get()
        if password != confirm:
            self._mostrar_error("Las contraseñas no coinciden.")
            return

        self._btn_crear.configure(state="disabled", text="Creando...")
        resultado = self._controller.try_create_superadmin(
            username=self._username.get().strip(),
            password=password,
            full_name=self._full_name.get().strip(),
        )
        self._btn_crear.configure(state="normal", text="Crear SUPERADMIN")

        if not resultado.ok:
            self._mostrar_error(resultado.error_message or "Error desconocido.")
            return

        assert resultado.usuario is not None
        self._on_success(resultado.usuario)

    def _mostrar_error(self, mensaje: str) -> None:
        """Pinta o limpia el label de error."""
        self._error_label.configure(text=mensaje)
