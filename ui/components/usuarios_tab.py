"""Pestaña "Usuarios" del módulo Usuarios y roles.

Diseño:

    ┌───────────┬───────────┬───────────┬───────────┐
    │ 4 Todos   │ 4 Activos │ 0 Bloque. │ 0 Inact.  │   ← tarjetas = filtro de estado
    └───────────┴───────────┴───────────┴───────────┘
    [ Buscar usuario o nombre…        ] [ Todos los roles ▾]
    ┌──────────────────────────────────────────┐ ┌──────────────┐
    │ USUARIO  NOMBRE  ROL  ESTADO  ÚLT. ACCESO │ │ (AL) Ana L.  │
    │ admin    …                                │ │ Rol · Estado │
    │ …                                         │ │ datos        │
    │                                           │ │ [acciones]   │
    │                                           │ │ actividad    │
    └──────────────────────────────────────────┘ └──────────────┘

Responsabilidad: solo render + interacción. Toda la I/O pasa por los
callables inyectados desde la vista (que a su vez vienen del controller).
Las reglas R1-R7 las aplica el servicio/controller; acá solo se
deshabilitan de antemano las acciones que de todas formas fallarían,
explicando el motivo.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from tkinter import messagebox
from typing import Callable, Dict, List, Optional

import customtkinter as ctk

from core.models import permissions as perms
from core.models.audit_entry import AuditEntry
from core.models.rol import Rol
from core.services.errors import (
    CannotAssignSuperadminRoleError,
    CannotChangeSelfRoleError,
    CannotDeactivateOnlySuperadminError,
    CannotDeactivateSelfError,
    CannotResetSuperadminPasswordError,
    DuplicateUsernameError,
    InvalidUsernameError,
    UsuarioNotFoundError,
    WeakPasswordError,
)
from core.services.usuario_admin_service import UsuarioConRol
from ui import theme
from ui.components import usuarios_format as fmt
from ui.components.data_table import Columna, DataTable, Fila
from ui.components.reset_password_dialog import ResetPasswordDialog
from ui.components.usuario_form_dialog import UsuarioFormDialog, UsuarioFormPayload

# Callables inyectados desde el controller.
ListUsuariosFn = Callable[[bool], List[UsuarioConRol]]
ListRolesFn = Callable[[], List[Rol]]
UltimosAccesosFn = Callable[[], Dict[int, str]]
ActividadFn = Callable[[int], List[AuditEntry]]
CreateFn = Callable[[str, str, int, str], None]
UpdateFn = Callable[[int, str, int], None]
ResetPwdFn = Callable[[int, str], None]
DeactivateFn = Callable[[int], None]
ReactivateFn = Callable[[int], None]
UnlockFn = Callable[[int], None]

_TODOS = "TODOS"
_TODOS_LOS_ROLES = "Todos los roles"

_ESTILO_ESTADO = {
    fmt.ESTADO_ACTIVO: (theme.EXITO, theme.EXITO_FONDO),
    fmt.ESTADO_BLOQUEADO: (theme.ERROR, theme.ERROR_FONDO),
    fmt.ESTADO_INACTIVO: (theme.NEUTRO, theme.NEUTRO_FONDO),
}

_COLUMNAS = (
    Columna("usuario", "Usuario", ancho=140, estirar=True),
    Columna("nombre", "Nombre", ancho=200, estirar=True),
    Columna("rol", "Rol", ancho=160, estirar=True),
    Columna("estado", "Estado", ancho=100, estirar=True),
    Columna("acceso", "Último acceso", ancho=150, estirar=True),
)


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


class UsuariosTab(ctk.CTkFrame):
    """Pestaña de gestión de usuarios."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        list_usuarios_fn: ListUsuariosFn,
        list_roles_fn: ListRolesFn,
        create_fn: CreateFn,
        update_fn: UpdateFn,
        reset_password_fn: ResetPwdFn,
        deactivate_fn: DeactivateFn,
        reactivate_fn: ReactivateFn,
        unlock_fn: UnlockFn,
        ultimos_accesos_fn: UltimosAccesosFn,
        actividad_fn: ActividadFn,
        actor_user_id: int,
        actor_es_superadmin: bool,
        on_cambio: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self._list_usuarios_fn = list_usuarios_fn
        self._list_roles_fn = list_roles_fn
        self._create_fn = create_fn
        self._update_fn = update_fn
        self._reset_password_fn = reset_password_fn
        self._deactivate_fn = deactivate_fn
        self._reactivate_fn = reactivate_fn
        self._unlock_fn = unlock_fn
        self._ultimos_accesos_fn = ultimos_accesos_fn
        self._actividad_fn = actividad_fn
        self._actor_id = actor_user_id
        self._actor_es_superadmin = actor_es_superadmin
        self._on_cambio = on_cambio
        self._log = logging.getLogger(self.__class__.__name__)

        self._usuarios: List[UsuarioConRol] = []
        self._accesos: Dict[int, str] = {}
        self._filtro_estado = _TODOS
        self._aviso_job: Optional[str] = None

        self._construir_ui()
        self.refrescar()

    # ── API pública ───────────────────────────────────────────────────────

    def refrescar(self, seleccionar_id: Optional[int] = None) -> None:
        """Recarga usuarios y último acceso; conserva (o fija) la selección."""
        try:
            self._usuarios = list(self._list_usuarios_fn(False))
        except Exception:  # noqa: BLE001
            self._log.exception("Error listando usuarios")
            messagebox.showerror("Error", "No se pudo cargar la lista de usuarios.")
            self._usuarios = []
        try:
            self._accesos = self._ultimos_accesos_fn()
        except Exception:  # noqa: BLE001
            self._log.exception("Error leyendo últimos accesos")
            self._accesos = {}
        self._actualizar_opciones_rol()
        self._render()
        if seleccionar_id is not None:
            self._tabla.seleccionar(str(seleccionar_id))
        self._mostrar_detalle(self._usuario_seleccionado())

    def nuevo_usuario(self) -> None:
        """Abre el formulario de alta."""
        roles = self._roles_asignables()
        if roles is None:
            return
        # Solo puede existir un SUPERADMIN (trigger en BD): no se ofrece.
        roles = [r for r in roles if r.code != perms.ROLE_SUPERADMIN]

        def submit(p: UsuarioFormPayload) -> Optional[str]:
            return self._intentar_crear(p)

        UsuarioFormDialog(self, title="Nuevo usuario", on_submit=submit, roles_disponibles=roles)

    # ── Construcción ──────────────────────────────────────────────────────

    def _construir_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        self._aviso = ctk.CTkLabel(
            self,
            text="",
            corner_radius=8,
            height=34,
            anchor="w",
            font=theme.fuente(theme.TAM_NORMAL),
        )

        self._construir_kpis().grid(row=1, column=0, sticky="ew", pady=(0, 14))
        self._construir_toolbar().grid(row=2, column=0, sticky="ew", pady=(0, 10))

        cuerpo = ctk.CTkFrame(self, fg_color="transparent")
        cuerpo.grid(row=3, column=0, sticky="nsew")
        cuerpo.grid_columnconfigure(0, weight=1)
        cuerpo.grid_rowconfigure(0, weight=1)

        self._tabla = DataTable(
            cuerpo,
            _COLUMNAS,
            on_seleccion=lambda _iid: self._mostrar_detalle(self._usuario_seleccionado()),
            on_doble_click=lambda iid: self._on_editar(int(iid)),
            texto_vacio="No hay usuarios que coincidan con el filtro.",
        )
        self._tabla.grid(row=0, column=0, sticky="nsew")
        self._tabla.color_tag(fmt.ESTADO_BLOQUEADO, theme.ERROR)
        self._tabla.color_tag(fmt.ESTADO_INACTIVO, theme.TEXTO_SUAVE)

        self._detalle = ctk.CTkScrollableFrame(
            cuerpo,
            width=320,
            fg_color=theme.TARJETA,
            corner_radius=theme.RADIO,
            border_width=1,
            border_color=theme.BORDE,
            scrollbar_button_color=theme.BORDE,
            scrollbar_button_hover_color=theme.TEXTO_SUAVE,
        )
        self._detalle.grid(row=0, column=1, sticky="nsew", padx=(14, 0))
        self._detalle.grid_columnconfigure(0, weight=1)

    def _construir_kpis(self) -> ctk.CTkFrame:
        fila = ctk.CTkFrame(self, fg_color="transparent")
        self._kpis: Dict[str, tuple[ctk.CTkFrame, ctk.CTkLabel]] = {}
        definicion = (
            (_TODOS, "Usuarios", theme.PRIMARIO),
            (fmt.ESTADO_ACTIVO, "Activos", theme.EXITO),
            (fmt.ESTADO_BLOQUEADO, "Bloqueados", theme.ERROR),
            (fmt.ESTADO_INACTIVO, "Inactivos", theme.NEUTRO),
        )
        for col, (clave, texto, color) in enumerate(definicion):
            fila.grid_columnconfigure(col, weight=1, uniform="kpi")
            tarjeta = ctk.CTkFrame(
                fila,
                fg_color=theme.TARJETA,
                corner_radius=theme.RADIO,
                border_width=1,
                border_color=theme.BORDE,
                cursor="hand2",
            )
            tarjeta.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 10, 0))
            tarjeta.grid_columnconfigure(1, weight=1)
            punto = ctk.CTkFrame(tarjeta, width=8, height=34, corner_radius=4, fg_color=color)
            punto.grid(row=0, column=0, rowspan=2, padx=(14, 10), pady=14)
            numero = ctk.CTkLabel(
                tarjeta, text="0", font=theme.fuente(22, "bold"), anchor="w", height=26
            )
            numero.grid(row=0, column=1, sticky="sw", pady=(12, 0))
            etiqueta = ctk.CTkLabel(
                tarjeta,
                text=texto,
                font=theme.fuente(theme.TAM_PEQUENO),
                text_color=theme.TEXTO_SUAVE,
                anchor="w",
                height=18,
            )
            etiqueta.grid(row=1, column=1, sticky="nw", pady=(0, 12))
            for w in (tarjeta, punto, numero, etiqueta):
                w.bind("<Button-1>", lambda _e, c=clave: self._set_filtro_estado(c))
            self._kpis[clave] = (tarjeta, numero)
        return fila

    def _construir_toolbar(self) -> ctk.CTkFrame:
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid_columnconfigure(0, weight=1)
        # Sin textvariable: con ella CTkEntry no muestra el texto guía.
        self._buscador = ctk.CTkEntry(
            bar,
            placeholder_text="Buscar por usuario o nombre…",
            height=36,
            fg_color=theme.TARJETA,
            border_color=theme.BORDE,
        )
        self._buscador.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self._buscador.bind("<KeyRelease>", lambda _e: self._render())

        self._combo_rol = ctk.CTkOptionMenu(
            bar,
            values=[_TODOS_LOS_ROLES],
            width=200,
            height=36,
            fg_color=theme.TARJETA,
            button_color=theme.SECUNDARIO,
            button_hover_color=theme.SECUNDARIO_HOVER,
            text_color=theme.TEXTO,
            command=lambda _v: self._render(),
        )
        self._combo_rol.grid(row=0, column=1)
        return bar

    # ── Render ────────────────────────────────────────────────────────────

    def _actualizar_opciones_rol(self) -> None:
        nombres = sorted({u.rol_name for u in self._usuarios})
        actual = self._combo_rol.get()
        self._combo_rol.configure(values=[_TODOS_LOS_ROLES, *nombres])
        if actual not in nombres:
            self._combo_rol.set(_TODOS_LOS_ROLES)

    def _render(self) -> None:
        ahora = _ahora()
        conteo = {_TODOS: 0, fmt.ESTADO_ACTIVO: 0, fmt.ESTADO_BLOQUEADO: 0, fmt.ESTADO_INACTIVO: 0}
        texto = str(self._buscador.get()).strip().casefold()
        rol = self._combo_rol.get()
        filas: List[Fila] = []
        for row in self._usuarios:
            u = row.usuario
            if u.id is None:
                continue
            estado = fmt.estado_usuario(u, ahora)
            conteo[_TODOS] += 1
            conteo[estado] += 1
            if self._filtro_estado != _TODOS and estado != self._filtro_estado:
                continue
            if rol != _TODOS_LOS_ROLES and row.rol_name != rol:
                continue
            if texto and texto not in u.username.casefold() and texto not in u.full_name.casefold():
                continue
            acceso = self._accesos.get(u.id)
            filas.append(
                Fila(
                    iid=str(u.id),
                    valores={
                        "usuario": (
                            f"{u.username}  (usted)" if u.id == self._actor_id else u.username
                        ),
                        "nombre": u.full_name,
                        "rol": row.rol_name,
                        "estado": fmt.ETIQUETA_ESTADO[estado],
                        "acceso": fmt.hace(acceso, ahora),
                    },
                    orden={"acceso": acceso or ""},
                    tags=(estado,),
                )
            )
        self._tabla.set_filas(filas)
        for clave, (tarjeta, numero) in self._kpis.items():
            numero.configure(text=str(conteo[clave]))
            activa = clave == self._filtro_estado
            tarjeta.configure(
                border_color=theme.PRIMARIO if activa else theme.BORDE,
                border_width=2 if activa else 1,
            )

    def _set_filtro_estado(self, clave: str) -> None:
        self._filtro_estado = clave
        self._render()

    # ── Panel de detalle ──────────────────────────────────────────────────

    def _usuario_seleccionado(self) -> Optional[UsuarioConRol]:
        iid = self._tabla.seleccionado()
        return self._buscar_usuario(int(iid)) if iid is not None else None

    def _mostrar_detalle(self, row: Optional[UsuarioConRol]) -> None:
        for hijo in self._detalle.winfo_children():
            hijo.destroy()
        if row is None or row.usuario.id is None:
            self._detalle_vacio()
            return
        u = row.usuario
        user_id = u.id
        assert user_id is not None
        ahora = _ahora()
        estado = fmt.estado_usuario(u, ahora)
        es_actor = user_id == self._actor_id
        es_superadmin = row.rol_code == perms.ROLE_SUPERADMIN
        panel = self._detalle
        fila = 0

        cabecera = ctk.CTkFrame(panel, fg_color="transparent")
        cabecera.grid(row=fila, column=0, sticky="ew", padx=16, pady=(18, 0))
        cabecera.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            cabecera,
            text=fmt.iniciales(u.full_name),
            width=52,
            height=52,
            corner_radius=26,
            fg_color=theme.color_rol(row.rol_code),
            text_color=theme.TEXTO_SOBRE_ROL,
            font=theme.fuente(18, "bold"),
        ).grid(row=0, column=0, rowspan=2, padx=(0, 12))
        ctk.CTkLabel(
            cabecera,
            text=u.full_name,
            font=theme.fuente(theme.TAM_SECCION + 1, "bold"),
            anchor="w",
            justify="left",
            wraplength=180,
        ).grid(row=0, column=1, sticky="sw")
        ctk.CTkLabel(
            cabecera,
            text=f"@{u.username}" + ("  ·  usted" if es_actor else ""),
            font=theme.fuente(theme.TAM_PEQUENO),
            text_color=theme.TEXTO_SUAVE,
            anchor="w",
        ).grid(row=1, column=1, sticky="nw")
        fila += 1

        insignias = ctk.CTkFrame(panel, fg_color="transparent")
        insignias.grid(row=fila, column=0, sticky="w", padx=16, pady=(12, 4))
        color_estado, fondo_estado = _ESTILO_ESTADO[estado]
        self._insignia(insignias, fmt.ETIQUETA_ESTADO[estado], color_estado, fondo_estado, 0)
        self._insignia(
            insignias, row.rol_name, theme.color_rol(row.rol_code), theme.NEUTRO_FONDO, 1
        )
        fila += 1

        fila = self._seccion(panel, "Acciones", fila, arriba=14)
        self._accion(panel, fila, "Editar datos", lambda: self._on_editar(user_id), primario=True)
        fila += 1
        self._accion(
            panel,
            fila,
            "Restablecer contraseña",
            lambda: self._on_reset_password(user_id),
        )
        fila += 1
        if estado == fmt.ESTADO_BLOQUEADO:
            self._accion(panel, fila, "Desbloquear cuenta", lambda: self._on_desbloquear(user_id))
            fila += 1
        if u.is_active:
            motivo = None
            if es_actor:
                motivo = "No puede desactivar su propia cuenta."
            elif es_superadmin:
                motivo = "La cuenta SUPERADMIN no se puede desactivar."
            self._accion(
                panel,
                fila,
                "Desactivar usuario",
                lambda: self._on_desactivar(user_id),
                peligro=True,
                motivo_deshabilitado=motivo,
            )
        else:
            self._accion(panel, fila, "Reactivar usuario", lambda: self._on_reactivar(user_id))
        fila += 1

        fila = self._seccion(panel, "Cuenta", fila, arriba=12)
        datos = ctk.CTkFrame(panel, fg_color=theme.TARJETA_ALT, corner_radius=8)
        datos.grid(row=fila, column=0, sticky="ew", padx=16)
        datos.grid_columnconfigure(1, weight=1)
        hasta = fmt.bloqueado_hasta(u, ahora)
        lineas = [
            ("Último acceso", fmt.fecha_hora_local(self._accesos.get(user_id))),
            ("Intentos fallidos", str(u.failed_attempts)),
            ("Creado", fmt.fecha_hora_local(u.created_at)),
            ("Modificado", fmt.fecha_hora_local(u.updated_at)),
        ]
        if hasta is not None:
            lineas.insert(0, ("Bloqueado hasta", f"{hasta.astimezone():%H:%M}"))
        for i, (etq, valor) in enumerate(lineas):
            pad = (10 if i == 0 else 3, 10 if i == len(lineas) - 1 else 0)
            ctk.CTkLabel(
                datos,
                text=etq,
                font=theme.fuente(theme.TAM_PEQUENO),
                text_color=theme.TEXTO_SUAVE,
                anchor="w",
                height=20,
            ).grid(row=i, column=0, sticky="w", padx=(12, 8), pady=pad)
            ctk.CTkLabel(
                datos, text=valor, font=theme.fuente(theme.TAM_PEQUENO), anchor="e", height=20
            ).grid(row=i, column=1, sticky="e", padx=(0, 12), pady=pad)
        fila += 1

        fila = self._seccion(panel, "Actividad reciente", fila)
        self._pintar_actividad(panel, fila, user_id, ahora)

    def _detalle_vacio(self) -> None:
        ctk.CTkLabel(
            self._detalle,
            text="Seleccione un usuario\npara ver su detalle y acciones.",
            font=theme.fuente(theme.TAM_NORMAL),
            text_color=theme.TEXTO_SUAVE,
            justify="center",
        ).grid(row=0, column=0, sticky="ew", padx=16, pady=60)

    def _pintar_actividad(
        self, panel: ctk.CTkFrame, fila: int, user_id: int, ahora: datetime
    ) -> None:
        try:
            actividad = self._actividad_fn(user_id)
        except Exception:  # noqa: BLE001
            self._log.exception("Error leyendo actividad de user_id=%s", user_id)
            actividad = []
        if not actividad:
            ctk.CTkLabel(
                panel,
                text="Sin actividad registrada.",
                font=theme.fuente(theme.TAM_PEQUENO),
                text_color=theme.TEXTO_SUAVE,
                anchor="w",
            ).grid(row=fila, column=0, sticky="w", padx=16, pady=(0, 16))
            return
        lista = ctk.CTkFrame(panel, fg_color="transparent")
        lista.grid(row=fila, column=0, sticky="ew", padx=16, pady=(0, 16))
        lista.grid_columnconfigure(1, weight=1)
        for i, entrada in enumerate(actividad):
            ctk.CTkFrame(lista, width=6, height=6, corner_radius=3, fg_color=theme.PRIMARIO).grid(
                row=i, column=0, padx=(2, 10), pady=6
            )
            ctk.CTkLabel(
                lista,
                text=fmt.etiqueta_accion(entrada.action),
                font=theme.fuente(theme.TAM_PEQUENO),
                anchor="w",
                height=20,
            ).grid(row=i, column=1, sticky="w")
            ctk.CTkLabel(
                lista,
                text=fmt.hace(entrada.timestamp, ahora),
                font=theme.fuente(theme.TAM_PEQUENO - 1),
                text_color=theme.TEXTO_SUAVE,
                anchor="e",
                height=20,
            ).grid(row=i, column=2, sticky="e")

    @staticmethod
    def _insignia(
        master: ctk.CTkFrame, texto: str, color: theme.Color, fondo: theme.Color, col: int
    ) -> None:
        ctk.CTkLabel(
            master,
            text=texto,
            fg_color=fondo,
            text_color=color,
            corner_radius=11,
            height=24,
            font=theme.fuente(theme.TAM_PEQUENO, "bold"),
            padx=10,
        ).grid(row=0, column=col, padx=(0, 6))

    @staticmethod
    def _seccion(panel: ctk.CTkFrame, titulo: str, fila: int, arriba: int = 18) -> int:
        ctk.CTkLabel(
            panel,
            text=titulo.upper(),
            font=theme.fuente(theme.TAM_PEQUENO - 1, "bold"),
            text_color=theme.TEXTO_SUAVE,
            anchor="w",
        ).grid(row=fila, column=0, sticky="w", padx=16, pady=(arriba, 6))
        return fila + 1

    @staticmethod
    def _accion(
        panel: ctk.CTkFrame,
        fila: int,
        texto: str,
        comando: Callable[[], None],
        primario: bool = False,
        peligro: bool = False,
        motivo_deshabilitado: Optional[str] = None,
    ) -> None:
        if primario:
            colores = dict(fg_color=theme.PRIMARIO, hover_color=theme.PRIMARIO_HOVER)
            texto_color = theme.TEXTO_SOBRE_PRIMARIO
        else:
            colores = dict(fg_color=theme.SECUNDARIO, hover_color=theme.SECUNDARIO_HOVER)
            texto_color = theme.PELIGRO if peligro else theme.TEXTO
        contenedor = ctk.CTkFrame(panel, fg_color="transparent")
        contenedor.grid(row=fila, column=0, sticky="ew", padx=16, pady=(0, 6))
        contenedor.grid_columnconfigure(0, weight=1)
        boton = ctk.CTkButton(
            contenedor,
            text=texto,
            height=34,
            text_color=texto_color,
            text_color_disabled=theme.TEXTO_SUAVE,
            command=comando,
            **colores,
        )
        boton.grid(row=0, column=0, sticky="ew")
        if motivo_deshabilitado:
            boton.configure(state="disabled")
            ctk.CTkLabel(
                contenedor,
                text=motivo_deshabilitado,
                font=theme.fuente(theme.TAM_PEQUENO - 1),
                text_color=theme.TEXTO_SUAVE,
                anchor="w",
                height=18,
            ).grid(row=1, column=0, sticky="w", pady=(2, 0))

    # ── Avisos ────────────────────────────────────────────────────────────

    def _avisar(self, texto: str, error: bool = False) -> None:
        if self._aviso_job is not None:
            self.after_cancel(self._aviso_job)
        self._aviso.configure(
            text=f"   {texto}",
            fg_color=theme.ERROR_FONDO if error else theme.EXITO_FONDO,
            text_color=theme.ERROR if error else theme.EXITO,
        )
        self._aviso.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        self._aviso_job = self.after(4500, self._ocultar_aviso)

    def _ocultar_aviso(self) -> None:
        self._aviso_job = None
        self._aviso.grid_remove()

    def _despues_de_cambio(self, texto: str, user_id: Optional[int]) -> None:
        self.refrescar(seleccionar_id=user_id)
        self._avisar(texto)
        if self._on_cambio is not None:
            self._on_cambio()

    # ── Handlers ──────────────────────────────────────────────────────────

    def _roles_asignables(self) -> Optional[List[Rol]]:
        try:
            return self._list_roles_fn()
        except Exception:  # noqa: BLE001
            self._log.exception("Error cargando roles")
            messagebox.showerror("Error", "No se pudieron cargar los roles.")
            return None

    def _on_editar(self, user_id: int) -> None:
        actual = self._buscar_usuario(user_id)
        roles = self._roles_asignables()
        if actual is None or roles is None:
            return
        if actual.rol_code != perms.ROLE_SUPERADMIN:
            roles = [r for r in roles if r.code != perms.ROLE_SUPERADMIN]
        bloqueo = None
        if user_id == self._actor_id:
            bloqueo = "No puede cambiar su propio rol."
            if actual.usuario.role_id not in {r.id for r in roles}:
                roles = [*roles, _rol_de(actual)]

        def submit(p: UsuarioFormPayload) -> Optional[str]:
            return self._intentar_actualizar(user_id, p)

        UsuarioFormDialog(
            self,
            title="Editar usuario",
            on_submit=submit,
            roles_disponibles=roles,
            modo_edicion=True,
            initial_username=actual.usuario.username,
            initial_full_name=actual.usuario.full_name,
            initial_role_id=actual.usuario.role_id,
            rol_bloqueado=bloqueo,
        )

    def _on_reset_password(self, user_id: int) -> None:
        actual = self._buscar_usuario(user_id)
        if actual is None:
            return

        def submit(new_pwd: str, _confirm: str) -> Optional[str]:
            return self._intentar_reset(user_id, new_pwd)

        ResetPasswordDialog(
            self,
            username_objetivo=actual.usuario.username,
            nombre_completo=actual.usuario.full_name,
            on_submit=submit,
        )

    def _on_desactivar(self, user_id: int) -> None:
        actual = self._buscar_usuario(user_id)
        if actual is None:
            return
        confirmado = messagebox.askyesno(
            "Desactivar usuario",
            (
                f"¿Desactivar a {actual.usuario.full_name} ({actual.usuario.username})?\n\n"
                "No podrá iniciar sesión, pero su historial se conserva. "
                "Puede reactivarlo cuando quiera."
            ),
            icon="warning",
        )
        if not confirmado:
            return
        try:
            self._deactivate_fn(user_id)
        except (
            CannotDeactivateSelfError,
            CannotDeactivateOnlySuperadminError,
            UsuarioNotFoundError,
        ) as exc:
            messagebox.showerror("No se puede desactivar", str(exc))
            return
        except Exception:  # noqa: BLE001
            self._log.exception("Error desactivando usuario id=%s", user_id)
            messagebox.showerror("Error", "No se pudo desactivar el usuario.")
            return
        self._despues_de_cambio(f"{actual.usuario.username} fue desactivado.", user_id)

    def _on_reactivar(self, user_id: int) -> None:
        try:
            self._reactivate_fn(user_id)
        except UsuarioNotFoundError as exc:
            messagebox.showerror("No encontrado", str(exc))
            return
        except Exception:  # noqa: BLE001
            self._log.exception("Error reactivando usuario id=%s", user_id)
            messagebox.showerror("Error", "No se pudo reactivar el usuario.")
            return
        self._despues_de_cambio("Usuario reactivado: ya puede iniciar sesión.", user_id)

    def _on_desbloquear(self, user_id: int) -> None:
        try:
            self._unlock_fn(user_id)
        except UsuarioNotFoundError as exc:
            messagebox.showerror("No encontrado", str(exc))
            return
        except Exception:  # noqa: BLE001
            self._log.exception("Error desbloqueando usuario id=%s", user_id)
            messagebox.showerror("Error", "No se pudo desbloquear el usuario.")
            return
        self._despues_de_cambio("Cuenta desbloqueada.", user_id)

    # ── Lógica de submit ──────────────────────────────────────────────────

    def _intentar_crear(self, p: UsuarioFormPayload) -> Optional[str]:
        try:
            self._create_fn(p["username"], p["full_name"], p["role_id"], p["password"])
        except (
            InvalidUsernameError,
            DuplicateUsernameError,
            WeakPasswordError,
            CannotAssignSuperadminRoleError,
            ValueError,
        ) as exc:
            return str(exc)
        except Exception:  # noqa: BLE001
            self._log.exception("Error inesperado creando usuario")
            return "No se pudo crear el usuario. Intente de nuevo."
        nuevo = next(
            (
                r.usuario.id
                for r in self._list_usuarios_fn(False)
                if r.usuario.username == p["username"]
            ),
            None,
        )
        self._filtro_estado = _TODOS
        self._despues_de_cambio(f"Usuario {p['username']} creado.", nuevo)
        return None

    def _intentar_actualizar(self, user_id: int, p: UsuarioFormPayload) -> Optional[str]:
        try:
            self._update_fn(user_id, p["full_name"], p["role_id"])
        except (
            CannotAssignSuperadminRoleError,
            CannotChangeSelfRoleError,
            UsuarioNotFoundError,
            ValueError,
        ) as exc:
            return str(exc)
        except Exception:  # noqa: BLE001
            self._log.exception("Error inesperado actualizando usuario id=%s", user_id)
            return "No se pudo actualizar el usuario. Intente de nuevo."
        self._despues_de_cambio("Cambios guardados.", user_id)
        return None

    def _intentar_reset(self, user_id: int, new_pwd: str) -> Optional[str]:
        try:
            self._reset_password_fn(user_id, new_pwd)
        except (
            WeakPasswordError,
            CannotResetSuperadminPasswordError,
            UsuarioNotFoundError,
        ) as exc:
            return str(exc)
        except Exception:  # noqa: BLE001
            self._log.exception("Error inesperado reseteando password id=%s", user_id)
            return "No se pudo cambiar la contraseña. Intente de nuevo."
        self._despues_de_cambio("Contraseña restablecida.", user_id)
        return None

    def _buscar_usuario(self, user_id: int) -> Optional[UsuarioConRol]:
        for row in self._usuarios:
            if row.usuario.id == user_id:
                return row
        return None


def _rol_de(row: UsuarioConRol) -> Rol:
    """Rol mínimo para mostrar el rol actual cuando no está entre los asignables."""
    return Rol(
        id=row.usuario.role_id,
        code=row.rol_code,
        name=row.rol_name,
        description="",
        permissions=frozenset(),
    )
