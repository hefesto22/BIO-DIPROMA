"""Pestaña "Roles y permisos": qué puede hacer cada rol, de un vistazo.

    ┌──────────────┬──────────────┬──────────────┬──────────────┐
    │ Super Admin. │ Administrad. │ Reportes     │ Operador     │  ← tarjetas por rol
    │ descripción  │              │              │              │
    │ 1 usuario    │ 1 usuario    │ …            │              │
    └──────────────┴──────────────┴──────────────┴──────────────┘
    ┌──────────────────────────────┬────┬────┬────┬────┐
    │ PERMISO                      │ SA │ AD │ RE │ OP │  ← matriz
    │ Ver asistencia               │ ✓  │ ✓  │ —  │ ✓  │
    └──────────────────────────────┴────┴────┴────┴────┘

Solo lectura: los permisos de cada rol los define el sistema (migraciones
002 y 006). Los usuarios por rol cuentan solo cuentas activas.
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, List

import customtkinter as ctk

from core.models.rol import Rol
from core.services.usuario_admin_service import UsuarioConRol
from ui import theme
from ui.components.usuarios_format import PERMISOS_UI

ListRolesFn = Callable[[], List[Rol]]
ListUsuariosFn = Callable[[bool], List[UsuarioConRol]]


class RolesMatrix(ctk.CTkScrollableFrame):
    """Tarjetas de roles + matriz de permisos (solo lectura)."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        list_roles_fn: ListRolesFn,
        list_usuarios_fn: ListUsuariosFn,
    ) -> None:
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self._list_roles_fn = list_roles_fn
        self._list_usuarios_fn = list_usuarios_fn
        self._log = logging.getLogger(self.__class__.__name__)
        self.grid_columnconfigure(0, weight=1)
        self.refrescar()

    def refrescar(self) -> None:
        for hijo in self.winfo_children():
            hijo.destroy()
        try:
            roles = self._list_roles_fn()
            usuarios = self._list_usuarios_fn(True)
        except Exception:  # noqa: BLE001
            self._log.exception("Error cargando roles/usuarios")
            ctk.CTkLabel(
                self, text="No se pudieron cargar los roles.", text_color=theme.ERROR
            ).grid(row=0, column=0, pady=40)
            return
        conteo: Dict[str, int] = {}
        for u in usuarios:
            conteo[u.rol_code] = conteo.get(u.rol_code, 0) + 1

        self._tarjetas(roles, conteo).grid(row=0, column=0, sticky="ew")
        self._matriz(roles).grid(row=1, column=0, sticky="ew", pady=(16, 0))
        ctk.CTkLabel(
            self,
            text=(
                "Los permisos de cada rol vienen definidos por el sistema. "
                "Para dar o quitar acceso a una persona, cambie su rol desde la pestaña Usuarios."
            ),
            font=theme.fuente(theme.TAM_PEQUENO),
            text_color=theme.TEXTO_SUAVE,
            anchor="w",
            justify="left",
            wraplength=900,
        ).grid(row=2, column=0, sticky="w", pady=(12, 8))

    # ── Bloques ───────────────────────────────────────────────────────────

    def _tarjetas(self, roles: List[Rol], conteo: Dict[str, int]) -> ctk.CTkFrame:
        fila = ctk.CTkFrame(self, fg_color="transparent")
        for col, rol in enumerate(roles):
            fila.grid_columnconfigure(col, weight=1, uniform="rol")
            tarjeta = ctk.CTkFrame(
                fila,
                fg_color=theme.TARJETA,
                corner_radius=theme.RADIO,
                border_width=1,
                border_color=theme.BORDE,
            )
            tarjeta.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 10, 0))
            tarjeta.grid_columnconfigure(0, weight=1)
            tarjeta.grid_rowconfigure(2, weight=1)
            ctk.CTkFrame(
                tarjeta, height=4, corner_radius=2, fg_color=theme.color_rol(rol.code)
            ).grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 10))
            ctk.CTkLabel(
                tarjeta, text=rol.name, font=theme.fuente(theme.TAM_SECCION, "bold"), anchor="w"
            ).grid(row=1, column=0, sticky="w", padx=16)
            ctk.CTkLabel(
                tarjeta,
                text=rol.description,
                font=theme.fuente(theme.TAM_PEQUENO),
                text_color=theme.TEXTO_SUAVE,
                anchor="nw",
                justify="left",
                wraplength=210,
            ).grid(row=2, column=0, sticky="nw", padx=16, pady=(4, 10))
            n = conteo.get(rol.code, 0)
            ctk.CTkLabel(
                tarjeta,
                text=f"{n} usuario{'s' if n != 1 else ''} activo{'s' if n != 1 else ''}",
                font=theme.fuente(theme.TAM_PEQUENO, "bold"),
                text_color=theme.color_rol(rol.code),
                anchor="w",
            ).grid(row=3, column=0, sticky="w", padx=16, pady=(0, 14))
        return fila

    def _matriz(self, roles: List[Rol]) -> ctk.CTkFrame:
        tabla = ctk.CTkFrame(
            self,
            fg_color=theme.TARJETA,
            corner_radius=theme.RADIO,
            border_width=1,
            border_color=theme.BORDE,
        )
        tabla.grid_columnconfigure(0, weight=1)
        for col in range(1, len(roles) + 1):
            tabla.grid_columnconfigure(col, minsize=130, uniform="permiso")

        encabezado = theme.fuente(theme.TAM_PEQUENO - 1, "bold")
        ctk.CTkLabel(
            tabla, text="PERMISO", font=encabezado, text_color=theme.TEXTO_SUAVE, anchor="w"
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(14, 8))
        for col, rol in enumerate(roles, start=1):
            ctk.CTkLabel(
                tabla, text=rol.name.upper(), font=encabezado, text_color=theme.TEXTO_SUAVE
            ).grid(row=0, column=col, pady=(14, 8))

        for i, permiso in enumerate(PERMISOS_UI, start=1):
            ctk.CTkFrame(tabla, height=1, fg_color=theme.BORDE).grid(
                row=2 * i - 1, column=0, columnspan=len(roles) + 1, sticky="ew", padx=12
            )
            celda = ctk.CTkFrame(tabla, fg_color="transparent")
            celda.grid(row=2 * i, column=0, sticky="w", padx=18, pady=8)
            ctk.CTkLabel(
                celda,
                text=permiso.nombre,
                font=theme.fuente(theme.TAM_NORMAL),
                anchor="w",
                height=20,
            ).grid(row=0, column=0, sticky="w")
            ctk.CTkLabel(
                celda,
                text=permiso.descripcion,
                font=theme.fuente(theme.TAM_PEQUENO - 1),
                text_color=theme.TEXTO_SUAVE,
                anchor="w",
                height=16,
            ).grid(row=1, column=0, sticky="w")
            for col, rol in enumerate(roles, start=1):
                tiene = permiso.code in rol.permissions
                ctk.CTkLabel(
                    tabla,
                    text="✓" if tiene else "—",
                    width=28,
                    height=28,
                    corner_radius=14,
                    fg_color=theme.EXITO_FONDO if tiene else "transparent",
                    text_color=theme.EXITO if tiene else theme.BORDE,
                    font=theme.fuente(theme.TAM_NORMAL, "bold"),
                ).grid(row=2 * i, column=col)
        ctk.CTkFrame(tabla, fg_color="transparent", height=8).grid(
            row=2 * len(PERMISOS_UI) + 1, column=0
        )
        return tabla
