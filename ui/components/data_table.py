"""Tabla de datos reutilizable (``ttk.Treeview`` con el estilo de la app).

Por qué ``ttk.Treeview`` y no una grilla de widgets customtkinter:
    - Columnas siempre alineadas (una grilla por fila se desalinea con
      textos largos).
    - Dibuja cientos de filas al instante; una grilla de CTkLabel tarda
      segundos con unas pocas centenas.
    - Trae selección, teclado y scroll nativos.

El estilo usa el tema ``clam`` de ttk (se ve igual en Windows y macOS) con
los colores de :mod:`ui.theme`. :func:`instalar_estilo_ttk` se llama una
vez al crear la ventana raíz; :func:`refrescar_estilo_ttk` vuelve a aplicar
la paleta del modo activo (claro/oscuro) cada vez que se crea una tabla.
"""

from __future__ import annotations

import functools
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass, field
from tkinter import ttk
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)

import customtkinter as ctk

from ui import theme

Alineacion = Literal["w", "center", "e"]

_ESTILO_TABLA = "BD.Treeview"
_ESTILO_ENCABEZADO = "BD.Treeview.Heading"

# Fuentes de ttk: se guardan a nivel de módulo para que Tk no las libere.
_fuentes: Dict[str, tkfont.Font] = {}


@dataclass(frozen=True)
class Columna:
    """Definición de una columna.

    Attributes:
        clave: Identificador interno (clave en ``Fila.valores``).
        titulo: Texto del encabezado.
        ancho: Ancho relativo en px lógicos. Las columnas se reparten el
            ancho disponible en proporción a este valor.
        estirar: Reservado (compatibilidad); el reparto es siempre proporcional.
        alinear: ``"w"`` (izquierda), ``"center"`` o ``"e"``.
    """

    clave: str
    titulo: str
    ancho: int = 120
    estirar: bool = False
    alinear: Alineacion = "w"


@dataclass(frozen=True)
class Fila:
    """Fila de la tabla.

    Attributes:
        iid: Identificador estable (p. ej. el id en BD como texto).
        valores: Texto a mostrar por columna.
        orden: Valores alternativos para ordenar (fechas, números). Si una
            columna no aparece acá se ordena por su texto.
        tags: Tags de estilo (ver :meth:`DataTable.color_tag`).
    """

    iid: str
    valores: Mapping[str, str]
    orden: Mapping[str, Any] = field(default_factory=dict)
    tags: Tuple[str, ...] = ()


def _escala(widget: tk.Misc) -> float:
    try:
        return float(ctk.ScalingTracker.get_widget_scaling(widget))
    except Exception:  # noqa: BLE001 — widget sin escala registrada
        return 1.0


def _fuente(nombre: str, size: int, weight: theme.Peso, escala: float) -> tkfont.Font:
    px = -round(size * escala)
    fuente = _fuentes.get(nombre)
    if fuente is None:
        fuente = tkfont.Font(family=theme.familia(), size=px, weight=weight)
        _fuentes[nombre] = fuente
    else:
        fuente.configure(family=theme.familia(), size=px, weight=weight)
    return fuente


def instalar_estilo_ttk(root: tk.Misc) -> None:
    """Activa el tema ``clam`` y aplica la paleta. Llamar una vez al arrancar."""
    estilo = ttk.Style(root)
    if "clam" in estilo.theme_names():
        estilo.theme_use("clam")
    refrescar_estilo_ttk(root)


def refrescar_estilo_ttk(widget: tk.Misc) -> None:
    """(Re)aplica colores y tamaños del modo activo a los estilos ttk de la app."""
    r = theme.resolver
    escala = _escala(widget)
    cuerpo = _fuente("cuerpo", theme.TAM_NORMAL, "normal", escala)
    encabezado = _fuente("encabezado", theme.TAM_PEQUENO, "bold", escala)
    alto_fila = cuerpo.metrics("linespace") + round(18 * escala)

    estilo = ttk.Style(widget)
    estilo.configure(
        _ESTILO_TABLA,
        background=r(theme.TARJETA),
        fieldbackground=r(theme.TARJETA),
        foreground=r(theme.TEXTO),
        bordercolor=r(theme.TARJETA),
        lightcolor=r(theme.TARJETA),
        darkcolor=r(theme.TARJETA),
        borderwidth=0,
        rowheight=alto_fila,
        font=cuerpo,
    )
    estilo.map(
        _ESTILO_TABLA,
        background=[("selected", r(theme.SELECCION))],
        foreground=[("selected", r(theme.TEXTO))],
    )
    estilo.configure(
        _ESTILO_ENCABEZADO,
        background=r(theme.TARJETA_ALT),
        foreground=r(theme.TEXTO_SUAVE),
        bordercolor=r(theme.BORDE),
        lightcolor=r(theme.TARJETA_ALT),
        darkcolor=r(theme.BORDE),
        relief="flat",
        font=encabezado,
        padding=(0, round(8 * escala)),
    )
    estilo.map(
        _ESTILO_ENCABEZADO,
        background=[("active", r(theme.SECUNDARIO_HOVER))],
        relief=[("pressed", "flat")],
    )

    # Selector de fecha (tkcalendar.DateEntry) y combos ttk: mismos colores.
    for nombre in ("DateEntry", "TCombobox", "TEntry"):
        estilo.configure(
            nombre,
            fieldbackground=r(theme.TARJETA),
            background=r(theme.SECUNDARIO),
            foreground=r(theme.TEXTO),
            arrowcolor=r(theme.TEXTO_SUAVE),
            bordercolor=r(theme.BORDE),
            lightcolor=r(theme.TARJETA),
            darkcolor=r(theme.TARJETA),
            insertcolor=r(theme.TEXTO),
            selectbackground=r(theme.SELECCION),
            selectforeground=r(theme.TEXTO),
            padding=(round(6 * escala), round(4 * escala)),
        )
        estilo.map(
            nombre,
            fieldbackground=[("readonly", r(theme.TARJETA))],
            background=[("active", r(theme.SECUNDARIO_HOVER))],
            bordercolor=[("focus", r(theme.PRIMARIO))],
        )


class DataTable(ctk.CTkFrame):
    """Tabla con encabezados ordenables, selección y estado vacío."""

    def __init__(
        self,
        master: Any,
        columnas: Sequence[Columna],
        on_seleccion: Optional[Callable[[Optional[str]], None]] = None,
        on_doble_click: Optional[Callable[[str], None]] = None,
        texto_vacio: str = "No hay datos para mostrar.",
        on_suprimir: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__(
            master,
            fg_color=theme.TARJETA,
            corner_radius=theme.RADIO,
            border_width=1,
            border_color=theme.BORDE,
        )
        refrescar_estilo_ttk(self)
        self._columnas = list(columnas)
        self._on_seleccion = on_seleccion
        self._on_doble_click = on_doble_click
        self._on_suprimir = on_suprimir
        self._filas: List[Fila] = []
        self._colores_tag: Dict[str, theme.Color] = {}
        self._orden_col: Optional[str] = None
        self._orden_desc = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        escala = _escala(self)
        self._tree = ttk.Treeview(
            self,
            columns=[c.clave for c in self._columnas],
            show="headings",
            selectmode="browse",
            style=_ESTILO_TABLA,
        )
        for col in self._columnas:
            self._tree.heading(
                col.clave,
                text=self._texto_encabezado(col),
                anchor=col.alinear,
                command=functools.partial(self._ordenar_por, col.clave),
            )
            self._tree.column(
                col.clave,
                width=round(col.ancho * escala),
                minwidth=20,
                stretch=False,
                anchor=col.alinear,
            )
        # Margen interior: el borde redondeado del frame no debe tapar la tabla.
        self._tree.grid(row=0, column=0, sticky="nsew", padx=3, pady=3)
        # ttk solo recorta la última columna cuando falta espacio: repartimos
        # nosotros el ancho real entre todas, en proporción a ``ancho``.
        self._ancho_aplicado = 0
        self._tree.bind("<Configure>", self._ajustar_columnas)

        self._scroll = ctk.CTkScrollbar(self, command=self._tree.yview)
        self._scroll.grid(row=0, column=1, sticky="ns", padx=(0, 4), pady=6)
        self._scroll.grid_remove()
        self._scroll_visible = False
        self._scroll_pos = (0.0, 1.0)
        self._tree.configure(yscrollcommand=self._mover_scroll)

        self._vacio = ctk.CTkLabel(
            self,
            text=texto_vacio,
            text_color=theme.TEXTO_SUAVE,
            font=theme.fuente(theme.TAM_NORMAL),
            fg_color=theme.TARJETA,
        )

        self._tree.bind("<<TreeviewSelect>>", self._al_seleccionar)
        self._tree.bind("<Double-1>", self._al_doble_click)
        self._tree.bind("<Return>", self._al_doble_click)
        # Supr en Windows; en Mac la tecla "delete" llega como BackSpace.
        self._tree.bind("<Delete>", self._al_suprimir)
        self._tree.bind("<BackSpace>", self._al_suprimir)

    # ── API ───────────────────────────────────────────────────────────────

    def set_filas(self, filas: Sequence[Fila]) -> None:
        """Reemplaza el contenido conservando la selección si la fila sigue existiendo."""
        seleccion = self.seleccionado()
        self._filas = list(filas)
        self._pintar()
        if seleccion is not None and self._tree.exists(seleccion):
            self.seleccionar(seleccion)
        elif seleccion is not None and self._on_seleccion is not None:
            self._on_seleccion(None)

    def seleccionado(self) -> Optional[str]:
        sel = self._tree.selection()
        return str(sel[0]) if sel else None

    def seleccionar(self, iid: Optional[str]) -> None:
        if iid is None or not self._tree.exists(iid):
            self._tree.selection_set(())
            return
        self._tree.selection_set(iid)
        self._tree.focus(iid)
        self._tree.see(iid)

    def enfocar(self) -> None:
        """Foco en la tabla; si nada está elegido, elige la primera fila."""
        self._tree.focus_set()
        if self.seleccionado() is None:
            hijos = self._tree.get_children()
            if hijos:
                self.seleccionar(str(hijos[0]))

    def color_tag(self, tag: str, color: theme.Color) -> None:
        """Color de texto para las filas con ``tag`` (según el modo activo)."""
        self._colores_tag[tag] = color
        self._tree.tag_configure(tag, foreground=theme.resolver(color))

    def _set_appearance_mode(self, mode_string: str) -> None:
        # Si el sistema pasa a modo claro/oscuro con la app abierta: las vistas
        # quedan guardadas entre módulos, así que la tabla se repinta sola.
        super()._set_appearance_mode(mode_string)
        refrescar_estilo_ttk(self)
        for tag, color in self._colores_tag.items():
            self._tree.tag_configure(tag, foreground=theme.resolver(color))

    def cantidad(self) -> int:
        return len(self._filas)

    # ── Internos ──────────────────────────────────────────────────────────

    def _texto_encabezado(self, col: Columna) -> str:
        flecha = ""
        if self._orden_col == col.clave:
            flecha = "  ▼" if self._orden_desc else "  ▲"
        prefijo = "  " if col.alinear == "w" else ""
        return f"{prefijo}{col.titulo.upper()}{flecha}"

    def _pintar(self) -> None:
        self._tree.delete(*self._tree.get_children())
        filas = self._filas
        if self._orden_col is not None:
            clave = self._orden_col
            filas = sorted(
                filas,
                key=lambda f: _clave_orden(f, clave),
                reverse=self._orden_desc,
            )
        for fila in filas:
            valores = [self._celda(col, fila) for col in self._columnas]
            self._tree.insert("", "end", iid=fila.iid, values=valores, tags=fila.tags)
        self.after_idle(self._revisar_scroll)
        if filas:
            self._vacio.place_forget()
        else:
            self._vacio.place(relx=0.5, rely=0.5, anchor="center")

    @staticmethod
    def _celda(col: Columna, fila: Fila) -> str:
        texto = fila.valores.get(col.clave, "")
        return f"  {texto}" if col.alinear == "w" else texto

    def _ordenar_por(self, clave: str) -> None:
        if self._orden_col == clave:
            self._orden_desc = not self._orden_desc
        else:
            self._orden_col, self._orden_desc = clave, False
        for col in self._columnas:
            self._tree.heading(col.clave, text=self._texto_encabezado(col))
        seleccion = self.seleccionado()
        self._pintar()
        self.seleccionar(seleccion)

    def _ajustar_columnas(self, evento: "tk.Event[ttk.Treeview]") -> None:
        # Tras un cambio de tamaño ttk no siempre avisa por yscrollcommand:
        # re-evaluamos la barra cuando Tk termina de acomodar la tabla.
        self.after_idle(self._revisar_scroll)
        disponible = int(evento.width)
        if disponible <= 1 or disponible == self._ancho_aplicado:
            return
        self._ancho_aplicado = disponible
        total = sum(c.ancho for c in self._columnas) or 1
        restante = disponible
        for i, col in enumerate(self._columnas):
            ancho = restante if i == len(self._columnas) - 1 else disponible * col.ancho // total
            self._tree.column(col.clave, width=max(20, ancho))
            restante -= ancho

    def _revisar_scroll(self) -> None:
        try:
            primero, ultimo = self._tree.yview()
        except tk.TclError:  # widget destruido
            return
        self._mover_scroll(primero, ultimo)

    def _mover_scroll(self, primero: Union[float, str], ultimo: Union[float, str]) -> None:
        """Muestra la barra de scroll solo cuando hay filas fuera de la vista.

        Solo toca la barra si algo cambió: redibujarla es caro y ttk avisa
        la misma posición muchas veces mientras se arma la pantalla.
        """
        inicio, fin = float(primero), float(ultimo)
        visible = not (inicio <= 0.0 and fin >= 1.0)
        if visible != self._scroll_visible:
            self._scroll_visible = visible
            if visible:
                self._scroll.grid()
            else:
                self._scroll.grid_remove()
        if visible and (inicio, fin) != self._scroll_pos:
            self._scroll_pos = (inicio, fin)
            self._scroll.set(inicio, fin)

    def _al_seleccionar(self, _evento: object) -> None:
        if self._on_seleccion is not None:
            self._on_seleccion(self.seleccionado())

    def _al_doble_click(self, _evento: object) -> None:
        iid = self.seleccionado()
        if iid is not None and self._on_doble_click is not None:
            self._on_doble_click(iid)

    def _al_suprimir(self, _evento: object) -> None:
        iid = self.seleccionado()
        if iid is not None and self._on_suprimir is not None:
            self._on_suprimir(iid)


def _clave_orden(fila: Fila, clave: str) -> Tuple[int, Any]:
    """Clave de orden estable: valores vacíos al final, texto sin mayúsculas."""
    valor = fila.orden.get(clave, fila.valores.get(clave, ""))
    if valor is None or valor == "":
        return (1, "")
    if isinstance(valor, str):
        return (0, valor.casefold())
    return (0, valor)
