"""Vista de login.

Recibe un ``LoginController`` y un callback ``on_success`` para
notificar al router que la sesión fue creada.

Threading:
    - El controller recibe un ``async_runner`` que envuelve
      ``run_async_ui`` con este frame. Así el ``auth_service.login``
      (~250ms por bcrypt verify) no bloquea la UI.

UX:
    - Botón se deshabilita durante el login (evita dobles clicks).
    - Mensajes de error en label rojo bajo el botón, NO en popup.
    - Enter en cualquier campo dispara el botón.
"""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk

import config
from core.services.session import Session
from ui import theme
from ui.async_util import run_async_ui
from ui.controllers.login_controller import LoginController


class LoginFrame(ctk.CTkFrame):
    """Frame con el formulario de login."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        controller: LoginController,
        on_success: Callable[[Session], None],
    ) -> None:
        """Construye el frame.

        Args:
            master: Ventana raíz donde se monta.
            controller: Controller inyectado.
            on_success: Callback invocado con la ``Session`` tras login OK.
        """
        super().__init__(master, corner_radius=0)
        self._controller = controller
        self._on_success = on_success

        self._construir_ui()

    # ── Construcción ──────────────────────────────────────────────────────

    def _construir_ui(self) -> None:
        contenedor = ctk.CTkFrame(self, corner_radius=12)
        contenedor.place(relx=0.5, rely=0.5, anchor="center")

        marca = ctk.CTkFrame(contenedor, fg_color="transparent")
        marca.grid(row=0, column=0, columnspan=2, padx=32, pady=(30, 0))
        ctk.CTkLabel(
            marca,
            text="BD",
            width=52,
            height=52,
            corner_radius=12,
            fg_color=theme.PRIMARIO,
            text_color=theme.TEXTO_SOBRE_PRIMARIO,
            font=ctk.CTkFont(size=20, weight="bold"),
        ).pack()
        ctk.CTkLabel(
            marca,
            text=config.APP_NAME,
            font=ctk.CTkFont(size=24, weight="bold"),
        ).pack(pady=(10, 0))
        ctk.CTkLabel(
            marca,
            text=config.APP_TAGLINE,
            font=ctk.CTkFont(size=12),
            text_color=theme.TEXTO_SUAVE,
        ).pack()

        titulo = ctk.CTkLabel(
            contenedor,
            text="Iniciar sesión",
            font=ctk.CTkFont(size=15, weight="bold"),
            anchor="w",
        )
        titulo.grid(row=1, column=0, columnspan=2, padx=32, pady=(26, 10), sticky="w")

        lbl_user = ctk.CTkLabel(contenedor, text="Usuario", font=ctk.CTkFont(size=12))
        lbl_user.grid(row=2, column=0, padx=(32, 8), pady=4, sticky="w")
        self._username = ctk.CTkEntry(contenedor, width=220)
        self._username.grid(row=2, column=1, padx=(0, 32), pady=4, sticky="ew")
        self._username.bind("<Return>", lambda _e: self._on_btn_login())

        lbl_pwd = ctk.CTkLabel(contenedor, text="Contraseña", font=ctk.CTkFont(size=12))
        lbl_pwd.grid(row=3, column=0, padx=(32, 8), pady=4, sticky="w")
        self._password = ctk.CTkEntry(contenedor, width=220, show="•")
        self._password.grid(row=3, column=1, padx=(0, 32), pady=4, sticky="ew")
        self._password.bind("<Return>", lambda _e: self._on_btn_login())

        self._error_label = ctk.CTkLabel(
            contenedor,
            text="",
            text_color="#c0392b",
            font=ctk.CTkFont(size=12),
            wraplength=300,
            justify="left",
        )
        self._error_label.grid(row=4, column=0, columnspan=2, padx=32, pady=(10, 0))

        self._btn_login = ctk.CTkButton(
            contenedor,
            text="Ingresar",
            command=self._on_btn_login,
            height=38,
            fg_color=theme.PRIMARIO,
            hover_color=theme.PRIMARIO_HOVER,
        )
        self._btn_login.grid(row=5, column=0, columnspan=2, padx=32, pady=(14, 28), sticky="ew")

        # Foco inicial en el campo usuario.
        self._username.focus_set()

    # ── Handlers ──────────────────────────────────────────────────────────

    def _on_btn_login(self) -> None:
        """Dispara el intento de login usando el async_runner del frame."""
        self._mostrar_error("")
        self._btn_login.configure(state="disabled", text="Ingresando...")

        # Envolvemos run_async_ui con este widget para dárselo al controller.
        def runner(work, on_ok, on_err):  # type: ignore[no-untyped-def]
            run_async_ui(self, work=work, on_success=on_ok, on_error=on_err)

        # Reemplazamos temporalmente el runner del controller por uno que
        # use nuestro widget. Esto mantiene al controller agnóstico de Tk.
        original_runner = self._controller._run_async  # noqa: SLF001
        self._controller._run_async = runner  # noqa: SLF001
        try:
            self._controller.try_login(
                self._username.get().strip(),
                self._password.get(),
                on_success=self._on_login_ok,
                on_error=self._on_login_err,
            )
        finally:
            # Restauramos por si el widget se reutiliza.
            self._controller._run_async = original_runner  # noqa: SLF001

    def _on_login_ok(self, session: Session) -> None:
        """Callback en UI thread cuando el login es exitoso."""
        self._btn_login.configure(state="normal", text="Ingresar")
        self._on_success(session)

    def _on_login_err(self, mensaje: str) -> None:
        """Callback en UI thread cuando el login falla."""
        self._btn_login.configure(state="normal", text="Ingresar")
        self._mostrar_error(mensaje)
        self._password.delete(0, "end")
        self._password.focus_set()

    def _mostrar_error(self, mensaje: str) -> None:
        self._error_label.configure(text=mensaje)
