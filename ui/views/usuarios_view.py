"""Vista del módulo "Usuarios y roles".

    Usuarios y roles                                   [+ Nuevo usuario]
    Administre quién entra al sistema y qué puede hacer cada rol.
    [ Usuarios | Roles y permisos ]
    ─────────────────────────────────────────────────────────────────
    (contenido de la pestaña elegida)

El guard de permiso ``MANAGE_USERS`` vive en el controller (decorador en
cada método). El ``MenuItem`` "users" del sidebar requiere ese mismo
permiso, así que solo SUPERADMIN o ADMIN llegan acá.
"""

from __future__ import annotations

import customtkinter as ctk

import config
from ui import theme
from ui.components.roles_matrix import RolesMatrix
from ui.components.usuarios_tab import UsuariosTab
from ui.controllers.usuarios_controller import UsuariosController

_TAB_USUARIOS = "Usuarios"
_TAB_ROLES = "Roles y permisos"


class UsuariosView(ctk.CTkFrame):
    """Vista raíz del módulo Usuarios y roles."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        controller: UsuariosController,
    ) -> None:
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self._controller = controller
        self._construir_ui()

    def _construir_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        cabecera = ctk.CTkFrame(self, fg_color="transparent")
        cabecera.grid(row=0, column=0, sticky="ew", padx=28, pady=(24, 0))
        cabecera.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            cabecera,
            text="Usuarios y roles",
            font=theme.fuente(theme.TAM_TITULO, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            cabecera,
            text=f"Administre quién entra a {config.APP_NAME} y qué puede hacer cada rol.",
            font=theme.fuente(theme.TAM_SUBTITULO),
            text_color=theme.TEXTO_SUAVE,
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        self._btn_nuevo = ctk.CTkButton(
            cabecera,
            text="+  Nuevo usuario",
            height=38,
            width=160,
            font=theme.fuente(theme.TAM_NORMAL, "bold"),
            fg_color=theme.PRIMARIO,
            hover_color=theme.PRIMARIO_HOVER,
            command=self._nuevo_usuario,
        )
        self._btn_nuevo.grid(row=0, column=1, rowspan=2, sticky="e")

        self._selector = ctk.CTkSegmentedButton(
            self,
            values=[_TAB_USUARIOS, _TAB_ROLES],
            command=self._cambiar_tab,
            height=34,
            font=theme.fuente(theme.TAM_NORMAL),
            # Estilo "pastilla": la opción elegida se ve como una tarjeta
            # sobre el fondo gris (buen contraste en claro y oscuro).
            selected_color=theme.TARJETA,
            selected_hover_color=theme.TARJETA,
            unselected_color=theme.SECUNDARIO,
            unselected_hover_color=theme.SECUNDARIO_HOVER,
            fg_color=theme.SECUNDARIO,
            text_color=theme.TEXTO,
        )
        self._selector.grid(row=1, column=0, sticky="w", padx=28, pady=(18, 16))

        c = self._controller
        self._tab_usuarios = UsuariosTab(
            self,
            list_usuarios_fn=c.list_usuarios,
            list_roles_fn=c.list_roles_asignables,
            create_fn=c.create_usuario,
            update_fn=c.update_usuario,
            reset_password_fn=c.reset_password,
            deactivate_fn=c.deactivate_usuario,
            reactivate_fn=c.reactivate_usuario,
            unlock_fn=c.unlock_usuario,
            ultimos_accesos_fn=c.ultimos_accesos,
            actividad_fn=c.actividad_reciente,
            actor_user_id=c.actor_user_id,
            actor_es_superadmin=c.actor_es_superadmin,
        )
        self._tab_roles: RolesMatrix | None = None

        self._selector.set(_TAB_USUARIOS)
        self._cambiar_tab(_TAB_USUARIOS)

    def al_mostrar(self) -> None:
        """Al volver al módulo (la vista queda guardada): datos al día."""
        if self._tab_roles is not None and self._tab_roles.winfo_ismapped():
            self._tab_roles.refrescar()
        else:
            self._tab_usuarios.refrescar()

    def _nuevo_usuario(self) -> None:
        self._tab_usuarios.nuevo_usuario()

    def _cambiar_tab(self, nombre: str) -> None:
        if nombre == _TAB_ROLES:
            self._tab_usuarios.grid_remove()
            self._btn_nuevo.grid_remove()
            if self._tab_roles is None:
                self._tab_roles = RolesMatrix(
                    self,
                    list_roles_fn=self._controller.list_roles_asignables,
                    list_usuarios_fn=self._controller.list_usuarios,
                )
            else:
                # Recalcula los usuarios activos por rol (pueden haber cambiado).
                self._tab_roles.refrescar()
            self._tab_roles.grid(row=2, column=0, sticky="nsew", padx=(28, 16), pady=(0, 20))
        else:
            if self._tab_roles is not None:
                self._tab_roles.grid_remove()
            self._btn_nuevo.grid()
            self._tab_usuarios.grid(row=2, column=0, sticky="nsew", padx=28, pady=(0, 20))
