"""Diálogo modal ``ResetPasswordDialog``: restablecer la contraseña de un usuario.

El administrador escribe la contraseña nueva dos veces (con checklist de
reglas y opción de mostrarla) y se la comunica a la persona. El servicio
aplica la política real y las reglas de permisos (R4) al guardar.

Contrato: ``on_submit(nueva, confirmacion)`` devuelve ``None`` (guardado
→ cierra) o un ``str`` con el error a mostrar.
"""

from __future__ import annotations

from typing import Callable, Optional

import customtkinter as ctk

from ui.components.dialogo import DialogoModal
from ui.components.password_fields import PasswordFields

# (new_password, confirm) -> None ok / str mensaje in-dialog
SubmitFn = Callable[[str, str], Optional[str]]


class ResetPasswordDialog(DialogoModal):
    """Diálogo modal para restablecer la contraseña de un usuario."""

    def __init__(
        self,
        parent: ctk.CTkBaseClass,
        username_objetivo: str,
        on_submit: SubmitFn,
        nombre_completo: str = "",
    ) -> None:
        quien = f"{nombre_completo} ({username_objetivo})" if nombre_completo else username_objetivo
        super().__init__(
            parent,
            titulo="Restablecer contraseña",
            subtitulo=(
                f"Nueva contraseña para {quien}. Comuníquesela en persona; "
                "la anterior deja de funcionar al guardar."
            ),
            ancho=520,
        )
        self._on_submit = on_submit

        self._campos = PasswordFields(
            self.cuerpo, etiqueta="Nueva contraseña", on_enter=self._on_save
        )
        self._campos.grid(row=0, column=0, sticky="ew")
        self.agregar_botones("Restablecer", self._on_save)
        self.after(60, self._campos.focus)

    def _on_save(self) -> None:
        error = self._campos.error_local()
        if error:
            self.mostrar_error(error)
            return
        resultado = self._on_submit(self._campos.password(), self._campos.confirmacion())
        if resultado is None:
            self.cerrar()
        else:
            self.mostrar_error(resultado)
