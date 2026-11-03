"""Vista de Configuración.

    Configuración                                        [+ Nuevo departamento]
    Catálogos de la empresa, relojes biométricos y feriados.
    [ Departamentos | Cargos | Relojes | Feriados ]
    ─────────────────────────────────────────────────────────────────────
    (pestaña elegida: contadores, buscador, tabla y panel de detalle)

El botón principal cambia según la pestaña. Las pestañas se construyen la
primera vez que se abren. El permiso ``MANAGE_SETTINGS`` lo valida el
controller en cada método.

Atajos (⌘ en lugar de Ctrl en Mac): Ctrl+N nuevo, Ctrl+F buscar,
Ctrl+1…4 cambiar de pestaña; en la tabla, Enter edita y Supr archiva
(o elimina, en Feriados). En el buscador, Enter baja a la tabla y Esc
lo limpia.
"""

from __future__ import annotations

import functools
from typing import Callable, Dict, Tuple

import customtkinter as ctk

from ui import theme
from ui.atajos import MOD, TECLA_BORRAR, TECLA_ENTER, Atajos
from ui.components import piezas
from ui.components.cargos_tab import CargosTab
from ui.components.departamentos_tab import DepartamentosTab
from ui.components.dispositivos_tab import DispositivosTab
from ui.components.feriados_tab import FeriadosTab
from ui.components.lista_detalle import ListaDetalle
from ui.controllers.configuracion_controller import ConfiguracionController

_DEPARTAMENTOS = "Departamentos"
_CARGOS = "Cargos"
_RELOJES = "Relojes"
_FERIADOS = "Feriados"

Fabrica = Callable[[ctk.CTkBaseClass, ConfiguracionController], ListaDetalle]

# Pestaña → (texto del botón principal, cómo construirla, qué hace Supr).
_PESTANAS: Dict[str, Tuple[str, Fabrica, str]] = {
    _DEPARTAMENTOS: ("+  Nuevo departamento", DepartamentosTab, "archivar"),
    _CARGOS: ("+  Nuevo cargo", CargosTab, "archivar"),
    _RELOJES: ("+  Nuevo reloj", DispositivosTab, "archivar"),
    _FERIADOS: ("+  Nuevo feriado", FeriadosTab, "eliminar"),
}


def texto_atajos(accion_borrar: str) -> str:
    return (
        f"{MOD}N nuevo   ·   {MOD}F buscar   ·   {TECLA_ENTER} editar   ·   "
        f"{TECLA_BORRAR} {accion_borrar}"
    )


class ConfiguracionView(ctk.CTkFrame):
    """Vista raíz del módulo Configuración."""

    def __init__(self, master: ctk.CTkBaseClass, controller: ConfiguracionController) -> None:
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self._controller = controller
        self._tabs: Dict[str, ListaDetalle] = {}
        self._actual = _DEPARTAMENTOS
        self._construir_ui()
        self._atajos = Atajos(self)
        self._atajos.con_modificador("n", self._on_nuevo)
        self._atajos.con_modificador("f", lambda: self._tabs[self._actual].enfocar_busqueda())
        for numero, nombre in enumerate(_PESTANAS, start=1):
            self._atajos.con_modificador(str(numero), functools.partial(self._ir_a, nombre))

    def destroy(self) -> None:
        self._atajos.quitar()
        super().destroy()

    def al_mostrar(self) -> None:
        """Al volver al módulo (la vista queda guardada): datos al día."""
        tab = self._tabs[self._actual]
        tab.refrescar(seleccionar=tab.tabla.seleccionado())

    def _construir_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        cabecera, boton = piezas.encabezado(
            self,
            "Configuración",
            "Catálogos de la empresa, relojes biométricos y feriados.",
            _PESTANAS[_DEPARTAMENTOS][0],
            self._on_nuevo,
        )
        cabecera.grid(row=0, column=0, sticky="ew", padx=28, pady=(24, 0))
        assert boton is not None
        self._boton = boton
        self._boton.configure(width=200)

        self._selector = ctk.CTkSegmentedButton(
            self,
            values=list(_PESTANAS),
            command=self._cambiar_tab,
            height=34,
            font=theme.fuente(theme.TAM_NORMAL),
            # Estilo "pastilla", igual que en Usuarios y Turnos.
            selected_color=theme.TARJETA,
            selected_hover_color=theme.TARJETA,
            unselected_color=theme.SECUNDARIO,
            unselected_hover_color=theme.SECUNDARIO_HOVER,
            fg_color=theme.SECUNDARIO,
            text_color=theme.TEXTO,
        )
        self._selector.set(_DEPARTAMENTOS)
        self._selector.grid(row=1, column=0, sticky="w", padx=28, pady=(18, 0))
        self._ayuda = ctk.CTkLabel(
            self,
            text="",
            font=theme.fuente(theme.TAM_PEQUENO - 1),
            text_color=theme.TEXTO_SUAVE,
        )
        self._ayuda.grid(row=1, column=0, sticky="e", padx=28, pady=(18, 0))
        self._cambiar_tab(_DEPARTAMENTOS)

    def _ir_a(self, nombre: str) -> None:
        self._selector.set(nombre)
        self._cambiar_tab(nombre)
        self._tabs[nombre].enfocar_tabla()

    def _cambiar_tab(self, nombre: str) -> None:
        for tab in self._tabs.values():
            tab.grid_remove()
        self._actual = nombre
        texto, fabrica, borrar = _PESTANAS[nombre]
        self._boton.configure(text=texto)
        self._ayuda.configure(text=texto_atajos(borrar))
        if nombre in self._tabs:
            tab = self._tabs[nombre]
            tab.refrescar(seleccionar=tab.tabla.seleccionado())
        else:
            tab = fabrica(self, self._controller)
            self._tabs[nombre] = tab
        tab.grid(row=2, column=0, sticky="nsew", padx=28, pady=(16, 20))

    def _on_nuevo(self) -> None:
        self._tabs[self._actual].nuevo()
