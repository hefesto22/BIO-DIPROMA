"""Campos de contraseña con confirmación, mostrar/ocultar y checklist en vivo.

Lo usan ``UsuarioFormDialog`` (alta) y ``ResetPasswordDialog``. La
checklist refleja ``config``/``PasswordPolicy`` mientras se escribe; la
validación definitiva sigue siendo la del servicio al guardar.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional, Tuple

import customtkinter as ctk

import config
from ui import theme
from ui.components.usuarios_format import evaluar_password

_OK = "✓"
_PENDIENTE = "○"


class PasswordFields(ctk.CTkFrame):
    """Bloque "Contraseña" + "Confirmar" + reglas."""

    def __init__(
        self,
        master: Any,
        etiqueta: str = "Contraseña",
        on_enter: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure((0, 1), weight=1, uniform="pwd")

        negrita = theme.fuente(theme.TAM_PEQUENO, "bold")
        ctk.CTkLabel(self, text=etiqueta, font=negrita, anchor="w").grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        ctk.CTkLabel(self, text="Confirmar contraseña", font=negrita, anchor="w").grid(
            row=0, column=1, sticky="w", padx=(12, 0), pady=(0, 4)
        )
        self._entry = ctk.CTkEntry(self, show="•", height=36)
        self._entry.grid(row=1, column=0, sticky="ew")
        self._confirm = ctk.CTkEntry(self, show="•", height=36)
        self._confirm.grid(row=1, column=1, sticky="ew", padx=(12, 0))

        self._mostrar = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            self,
            text="Mostrar contraseñas",
            variable=self._mostrar,
            command=self._alternar_visibilidad,
            font=theme.fuente(theme.TAM_PEQUENO),
            checkbox_width=16,
            checkbox_height=16,
            border_width=2,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 8))

        # Reglas en dos columnas para que el diálogo quepa en pantallas de 768 px.
        reglas = ctk.CTkFrame(self, fg_color=theme.TARJETA_ALT, corner_radius=8)
        reglas.grid(row=3, column=0, columnspan=2, sticky="ew")
        reglas.grid_columnconfigure((0, 1), weight=1, uniform="regla")
        self._labels_reglas: List[ctk.CTkLabel] = []
        total = len(self._evaluar()) + 1  # +1: "coinciden"
        filas = (total + 1) // 2
        for i in range(total):
            fila, col = i % filas, i // filas
            lbl = ctk.CTkLabel(
                reglas, text="", anchor="w", height=22, font=theme.fuente(theme.TAM_PEQUENO)
            )
            lbl.grid(
                row=fila,
                column=col,
                sticky="w",
                padx=12,
                pady=(8 if fila == 0 else 0, 8 if fila == filas - 1 else 0),
            )
            self._labels_reglas.append(lbl)

        for entry in (self._entry, self._confirm):
            entry.bind("<KeyRelease>", lambda _e: self._actualizar())
            if on_enter is not None:
                entry.bind("<Return>", lambda _e: on_enter())
        self._actualizar()

    # ── API ───────────────────────────────────────────────────────────────

    def password(self) -> str:
        return str(self._entry.get())

    def confirmacion(self) -> str:
        return str(self._confirm.get())

    def focus(self) -> None:
        self._entry.focus_set()

    def error_local(self) -> Optional[str]:
        """Primer problema detectable sin ir al servicio, o ``None``."""
        if not self.password():
            return "La contraseña es obligatoria."
        for regla, cumple in self._evaluar():
            if not cumple:
                return f"La contraseña no cumple: {regla.lower()}."
        if self.password() != self.confirmacion():
            return "La contraseña y su confirmación no coinciden."
        return None

    # ── Internos ──────────────────────────────────────────────────────────

    def _evaluar(self) -> List[Tuple[str, bool]]:
        return evaluar_password(
            self._entry.get() if hasattr(self, "_entry") else "",
            config.MIN_PASSWORD_LENGTH,
            require_digit=config.PASSWORD_REQUIRE_DIGIT,
            require_letter=config.PASSWORD_REQUIRE_LETTER,
        )

    def _actualizar(self) -> None:
        pwd, conf = self._entry.get(), self._confirm.get()
        reglas = self._evaluar() + [("Las dos contraseñas coinciden", bool(pwd) and pwd == conf)]
        for lbl, (texto, cumple) in zip(self._labels_reglas, reglas):
            lbl.configure(
                text=f"{_OK if cumple else _PENDIENTE}  {texto}",
                text_color=theme.EXITO if cumple else theme.TEXTO_SUAVE,
            )

    def _alternar_visibilidad(self) -> None:
        caracter = "" if self._mostrar.get() else "•"
        self._entry.configure(show=caracter)
        self._confirm.configure(show=caracter)
