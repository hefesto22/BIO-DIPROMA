"""Pestaña estándar "contadores + buscador + tabla + panel de detalle".

    ┌──────────┬────────────┬──────────────┐
    │ Activos  │ Archivados │ …            │   ← contadores (los que filtran, filtran)
    └──────────┴────────────┴──────────────┘
    [Buscar…………………………………………]  [extras de la barra]
    ┌──────────────────────────────┐ ┌──────────────┐
    │ tabla (DataTable)            │ │ panel de     │
    │                              │ │ detalle      │
    └──────────────────────────────┘ └──────────────┘

Es el mismo esquema de Empleados y Turnos, empaquetado para las pestañas
de Configuración. Las subclases implementan:

    - ``_cargar()``: trae los datos (puede lanzar; la base avisa).
    - ``_filas()``: ``(filas_visibles, valores_de_contadores)``.
    - ``_detalle(panel, iid)``: pinta el panel del elemento elegido.
    - ``nuevo()``: lo que hace el botón principal del encabezado.

Opcionales: ``_doble_click(iid)`` (también Enter) y ``_suprimir(iid)``
(tecla Supr sobre la tabla).
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Sequence, Tuple

import customtkinter as ctk

from ui import theme
from ui.components import piezas
from ui.components.data_table import Columna, DataTable, Fila

Kpi = Tuple[str, str, theme.Color, bool]


class ListaDetalle(ctk.CTkFrame):
    """Base de las pestañas con lista y panel de detalle."""

    texto_sin_seleccion = "Seleccione un elemento\npara ver su detalle y acciones."

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        columnas: Sequence[Columna],
        kpis: Sequence[Kpi],
        placeholder_busqueda: str,
        texto_vacio: str,
        filtro_inicial: Optional[str] = None,
        filtro_alternable: bool = False,
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self._log = logging.getLogger(self.__class__.__name__)
        self.filtro = filtro_inicial
        self._filtro_alternable = filtro_alternable

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        self.aviso = piezas.Aviso(self, row=0, column=0, sticky="ew", pady=(0, 12))
        # Espacio para un aviso que se queda (p. ej. "se conectó un reloj
        # nuevo"); las subclases lo llenan y lo muestran con ``grid()``.
        self.banda = ctk.CTkFrame(self, fg_color="transparent")
        self.banda.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        self.banda.grid_columnconfigure(0, weight=1)
        self.banda.grid_remove()

        self.kpis = piezas.FilaKpis(self, kpis, on_click=self._set_filtro)
        self.kpis.grid(row=2, column=0, sticky="ew", pady=(0, 14))

        self.barra = ctk.CTkFrame(self, fg_color="transparent")
        self.barra.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        self.barra.grid_columnconfigure(0, weight=1)
        self.buscador = piezas.buscador(self.barra, placeholder_busqueda, self.render)
        self.buscador.grid(row=0, column=0, sticky="ew")
        # Escribir y bajar a la tabla sin soltar el teclado.
        self.buscador.bind("<Return>", lambda _e: self.enfocar_tabla())
        self.buscador.bind("<Down>", lambda _e: self.enfocar_tabla())

        cuerpo = ctk.CTkFrame(self, fg_color="transparent")
        cuerpo.grid(row=4, column=0, sticky="nsew")
        cuerpo.grid_columnconfigure(0, weight=1)
        cuerpo.grid_rowconfigure(0, weight=1)
        self.tabla = DataTable(
            cuerpo,
            columnas,
            on_seleccion=lambda _iid: self.mostrar_detalle(),
            on_doble_click=self._doble_click,
            texto_vacio=texto_vacio,
            on_suprimir=self._suprimir,
        )
        self.tabla.grid(row=0, column=0, sticky="nsew")
        self.tabla.color_tag("apagado", theme.TEXTO_SUAVE)
        self.panel = piezas.panel_detalle(cuerpo)
        self.panel.grid(row=0, column=1, sticky="nsew", padx=(14, 0))

    # ── Para las subclases ────────────────────────────────────────────────

    def _cargar(self) -> None:
        raise NotImplementedError

    def _filas(self) -> Tuple[List[Fila], Dict[str, str]]:
        raise NotImplementedError

    def _detalle(self, panel: ctk.CTkScrollableFrame, iid: str) -> None:
        raise NotImplementedError

    def _doble_click(self, iid: str) -> None:
        """Por defecto no hace nada; las subclases suelen abrir "Editar"."""

    def _suprimir(self, iid: str) -> None:
        """Tecla Supr sobre la tabla. Por defecto nada; suele ser "Archivar"."""

    def nuevo(self) -> None:
        raise NotImplementedError

    # ── Flujo común ───────────────────────────────────────────────────────

    def enfocar_busqueda(self) -> None:
        self.buscador.focus_set()
        self.buscador.select_range(0, "end")

    def enfocar_tabla(self) -> None:
        """Lleva el foco a la tabla (para usar flechas, Enter y Supr)."""
        self.tabla.enfocar()

    def texto_busqueda(self) -> str:
        return str(self.buscador.get()).strip().casefold()

    def refrescar(self, seleccionar: Optional[str] = None) -> None:
        try:
            self._cargar()
        except Exception:  # noqa: BLE001 — último recurso: log + aviso
            self._log.exception("Error cargando la lista")
            self.aviso.mostrar("No se pudo cargar la lista. Intente de nuevo.", error=True)
            return
        self.render()
        if seleccionar is not None:
            self.tabla.seleccionar(seleccionar)
        self.mostrar_detalle()

    def render(self) -> None:
        filas, valores = self._filas()
        self.tabla.set_filas(filas)
        self.kpis.actualizar(valores, activa=self.filtro)

    def mostrar_detalle(self) -> None:
        piezas.limpiar(self.panel)
        iid = self.tabla.seleccionado()
        if iid is None:
            piezas.detalle_vacio(self.panel, self.texto_sin_seleccion)
            return
        self._detalle(self.panel, iid)

    def _set_filtro(self, clave: str) -> None:
        if self._filtro_alternable and self.filtro == clave:
            self.filtro = None
        else:
            self.filtro = clave
        self.render()
        self.mostrar_detalle()
