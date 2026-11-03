"""Diálogo modal ``UsuarioFormDialog``: alta y edición de usuarios.

Modo "Nuevo":
    usuario + nombre completo + rol + contraseña/confirmación con checklist.

Modo "Editar":
    nombre completo + rol. El usuario no se edita (es la referencia
    inmutable en el audit log) y la contraseña se cambia desde
    ``ResetPasswordDialog``. Si el actor se está editando a sí mismo, el
    rol queda bloqueado (R5) y se explica por qué.

El diálogo NO conoce el controller: recibe ``on_submit(payload)``, que
devuelve ``None`` (guardado → cierra) o un ``str`` con el error a mostrar
sin perder lo escrito.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, TypedDict

import customtkinter as ctk

from core.models.rol import Rol
from ui import theme
from ui.components.dialogo import DialogoModal
from ui.components.password_fields import PasswordFields


class UsuarioFormPayload(TypedDict, total=False):
    """Payload del form. ``username`` y ``password`` solo en modo alta."""

    username: str
    full_name: str
    role_id: int
    password: str
    password_confirm: str


SubmitFn = Callable[[UsuarioFormPayload], Optional[str]]

_HINT_USERNAME = "3 a 32 caracteres: letras, números, punto, guion bajo o guion."


class UsuarioFormDialog(DialogoModal):
    """Diálogo modal para crear o editar un usuario."""

    def __init__(
        self,
        parent: ctk.CTkBaseClass,
        title: str,
        on_submit: SubmitFn,
        roles_disponibles: List[Rol],
        modo_edicion: bool = False,
        initial_username: str = "",
        initial_full_name: str = "",
        initial_role_id: Optional[int] = None,
        rol_bloqueado: Optional[str] = None,
    ) -> None:
        """Construye el diálogo.

        Args:
            parent: Widget padre.
            title: "Nuevo usuario" / "Editar usuario".
            on_submit: Callback. ``None`` = OK y cierra; ``str`` = error in-dialog.
            roles_disponibles: Roles que el actor PUEDE asignar (sin
                SUPERADMIN si el actor es ADMIN — filtrado en el controller).
            modo_edicion: ``True`` oculta la contraseña y bloquea el usuario.
            initial_username: Usuario a mostrar (solo lectura en edición).
            initial_full_name: Precarga del nombre.
            initial_role_id: Rol preseleccionado.
            rol_bloqueado: Si viene un texto, el rol no se puede cambiar y
                se muestra ese motivo (p. ej. editar la propia cuenta).
        """
        subtitulo = (
            "Actualice el nombre o el rol. La contraseña se cambia con " '"Restablecer contraseña".'
            if modo_edicion
            else "La persona entrará con este usuario y contraseña."
        )
        super().__init__(parent, titulo=title, subtitulo=subtitulo, ancho=560)
        self._on_submit = on_submit
        self._roles = roles_disponibles
        self._modo_edicion = modo_edicion
        self._rol_bloqueado = rol_bloqueado
        self._password: Optional[PasswordFields] = None

        self._construir_campos(initial_username, initial_full_name, initial_role_id)
        self.agregar_botones("Guardar cambios" if modo_edicion else "Crear usuario", self._on_save)
        self.bind("<Return>", lambda _e: self._on_save())

    # ── Construcción ──────────────────────────────────────────────────────

    def _construir_campos(
        self,
        ini_username: str,
        ini_full_name: str,
        ini_role_id: Optional[int],
    ) -> None:
        cuerpo = self.cuerpo
        cuerpo.grid_columnconfigure((0, 1), weight=1, uniform="campo")

        self._etiqueta(cuerpo, "Usuario", 0, 0)
        self._etiqueta(cuerpo, "Nombre completo", 0, 1)
        self._entry_username = ctk.CTkEntry(cuerpo, height=36, placeholder_text="ej. maria.lopez")
        if ini_username:
            self._entry_username.insert(0, ini_username)
        if self._modo_edicion:
            self._entry_username.configure(state="disabled", text_color=theme.TEXTO_SUAVE)
        self._entry_username.grid(row=1, column=0, sticky="ew")
        self._entry_full_name = ctk.CTkEntry(
            cuerpo, height=36, placeholder_text="ej. María López Pineda"
        )
        if ini_full_name:
            self._entry_full_name.insert(0, ini_full_name)
        self._entry_full_name.grid(row=1, column=1, sticky="ew", padx=(12, 0))
        if not self._modo_edicion:
            ctk.CTkLabel(
                cuerpo,
                text=_HINT_USERNAME,
                font=theme.fuente(theme.TAM_PEQUENO - 1),
                text_color=theme.TEXTO_SUAVE,
                anchor="w",
            ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(3, 0))

        self._etiqueta(cuerpo, "Rol", 3, 0, arriba=12)
        self._rol_por_label: Dict[str, Rol] = {r.name: r for r in self._roles}
        labels = list(self._rol_por_label)
        seleccion = next(
            (r.name for r in self._roles if r.id == ini_role_id),
            _rol_con_menos_permisos(self._roles).name if self._roles else "",
        )
        self._combo_rol = ctk.CTkOptionMenu(
            cuerpo,
            values=labels,
            height=36,
            fg_color=theme.SECUNDARIO,
            button_color=theme.SECUNDARIO_HOVER,
            button_hover_color=theme.BORDE,
            text_color=theme.TEXTO,
            dropdown_font=theme.fuente(theme.TAM_NORMAL),
            command=lambda _v: self._actualizar_descripcion_rol(),
        )
        self._combo_rol.set(seleccion)
        self._combo_rol.grid(row=4, column=0, columnspan=2, sticky="ew")
        if self._rol_bloqueado:
            self._combo_rol.configure(state="disabled")
        self._desc_rol = ctk.CTkLabel(
            cuerpo,
            text="",
            font=theme.fuente(theme.TAM_PEQUENO),
            text_color=theme.TEXTO_SUAVE,
            anchor="w",
            justify="left",
            wraplength=500,
        )
        self._desc_rol.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self._actualizar_descripcion_rol()

        if not self._modo_edicion:
            self._password = PasswordFields(cuerpo, on_enter=self._on_save)
            self._password.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(14, 0))

        destino = self._entry_full_name if self._modo_edicion else self._entry_username
        self.after(60, destino.focus_set)

    @staticmethod
    def _etiqueta(master: ctk.CTkFrame, texto: str, fila: int, col: int, arriba: int = 0) -> None:
        ctk.CTkLabel(
            master, text=texto, font=theme.fuente(theme.TAM_PEQUENO, "bold"), anchor="w"
        ).grid(row=fila, column=col, sticky="w", padx=(12 if col else 0, 0), pady=(arriba, 4))

    def _actualizar_descripcion_rol(self) -> None:
        if self._rol_bloqueado:
            self._desc_rol.configure(text=self._rol_bloqueado, text_color=theme.ALERTA)
            return
        rol = self._rol_por_label.get(self._combo_rol.get())
        self._desc_rol.configure(text=rol.description if rol else "")

    # ── Guardar ───────────────────────────────────────────────────────────

    def _on_save(self) -> None:
        payload = self._recolectar_payload()
        if isinstance(payload, str):
            self.mostrar_error(payload)
            return
        resultado = self._on_submit(payload)
        if resultado is None:
            self.cerrar()
        else:
            self.mostrar_error(resultado)

    def _recolectar_payload(self) -> UsuarioFormPayload | str:
        rol = self._rol_por_label.get(self._combo_rol.get())
        if rol is None:
            return "Seleccione un rol válido."

        full_name = self._entry_full_name.get().strip()
        if not full_name:
            return "El nombre completo es obligatorio."

        if self._modo_edicion:
            return UsuarioFormPayload(full_name=full_name, role_id=rol.id)

        username = self._entry_username.get().strip()
        if not username:
            return "El nombre de usuario es obligatorio."
        assert self._password is not None
        error = self._password.error_local()
        if error:
            return error

        return UsuarioFormPayload(
            username=username,
            full_name=full_name,
            role_id=rol.id,
            password=self._password.password(),
            password_confirm=self._password.confirmacion(),
        )


def _rol_con_menos_permisos(roles: List[Rol]) -> Rol:
    """Rol sugerido para un alta: el de menos permisos (mínimo privilegio)."""
    return min(reversed(roles), key=lambda r: len(r.permissions))
