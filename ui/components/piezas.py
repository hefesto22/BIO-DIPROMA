"""Piezas visuales compartidas por los módulos rediseñados.

Mismo lenguaje visual que "Usuarios y roles": encabezado con título,
subtítulo y botón principal; tarjetas-contador que filtran; tabla a la
izquierda y panel de detalle a la derecha; insignias de estado; avisos
que desaparecen solos. Usar estas piezas en vez de armar widgets sueltos
mantiene toda la app consistente.
"""

from __future__ import annotations

import re
import tkinter as tk
from datetime import date
from tkinter import ttk
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import customtkinter as ctk
from tkcalendar import Calendar

from ui import theme
from ui.formato import dias_activos

# Paleta para avatares (p. ej. por departamento): se elige por id estable.
_COLORES_AVATAR: Tuple[theme.Color, ...] = (
    ("#1E4FA3", "#6EA2F0"),
    ("#0F766E", "#4FD1C5"),
    ("#6D28D9", "#A78BFA"),
    ("#B45309", "#F2B544"),
    ("#BE185D", "#F472B6"),
    ("#15803D", "#5BD08A"),
    ("#475569", "#A3ABB7"),
)


def color_avatar(clave: int) -> theme.Color:
    """Color estable para un id (mismo id → mismo color)."""
    return _COLORES_AVATAR[clave % len(_COLORES_AVATAR)]


# ── Encabezado de página ──────────────────────────────────────────────────────


def encabezado(
    master: Any,
    titulo: str,
    subtitulo: str,
    texto_boton: Optional[str] = None,
    on_boton: Optional[Callable[[], None]] = None,
) -> Tuple[ctk.CTkFrame, Optional[ctk.CTkButton]]:
    """Título grande + subtítulo + botón principal opcional a la derecha."""
    frame = ctk.CTkFrame(master, fg_color="transparent")
    frame.grid_columnconfigure(0, weight=1)
    ctk.CTkLabel(frame, text=titulo, font=theme.fuente(theme.TAM_TITULO, "bold"), anchor="w").grid(
        row=0, column=0, sticky="w"
    )
    ctk.CTkLabel(
        frame,
        text=subtitulo,
        font=theme.fuente(theme.TAM_SUBTITULO),
        text_color=theme.TEXTO_SUAVE,
        anchor="w",
    ).grid(row=1, column=0, sticky="w", pady=(2, 0))
    boton = None
    if texto_boton and on_boton:
        boton = boton_primario(frame, texto_boton, on_boton, ancho=170)
        boton.grid(row=0, column=1, rowspan=2, sticky="e")
    return frame, boton


def boton_primario(
    master: Any, texto: str, comando: Callable[[], None], ancho: int = 140, alto: int = 38
) -> ctk.CTkButton:
    return ctk.CTkButton(
        master,
        text=texto,
        width=ancho,
        height=alto,
        font=theme.fuente(theme.TAM_NORMAL, "bold"),
        fg_color=theme.PRIMARIO,
        hover_color=theme.PRIMARIO_HOVER,
        command=comando,
    )


def boton_secundario(
    master: Any, texto: str, comando: Callable[[], None], ancho: int = 120, alto: int = 36
) -> ctk.CTkButton:
    return ctk.CTkButton(
        master,
        text=texto,
        width=ancho,
        height=alto,
        fg_color=theme.SECUNDARIO,
        hover_color=theme.SECUNDARIO_HOVER,
        text_color=theme.TEXTO,
        command=comando,
    )


# ── Tarjetas-contador ─────────────────────────────────────────────────────────


class FilaKpis(ctk.CTkFrame):
    """Fila de tarjetas con un número grande. Las que tienen clave filtran al hacer clic.

    ``definicion``: ``[(clave, etiqueta, color, filtra)]``.
    """

    def __init__(
        self,
        master: Any,
        definicion: Sequence[Tuple[str, str, theme.Color, bool]],
        on_click: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self._tarjetas: Dict[str, Tuple[ctk.CTkFrame, ctk.CTkLabel, ctk.CTkLabel, bool]] = {}
        for col, (clave, texto, color, filtra) in enumerate(definicion):
            self.grid_columnconfigure(col, weight=1, uniform="kpi")
            tarjeta = ctk.CTkFrame(
                self,
                fg_color=theme.TARJETA,
                corner_radius=theme.RADIO,
                border_width=1,
                border_color=theme.BORDE,
                cursor="hand2" if filtra else "arrow",
            )
            tarjeta.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 10, 0))
            tarjeta.grid_columnconfigure(1, weight=1)
            barra = ctk.CTkFrame(tarjeta, width=8, height=34, corner_radius=4, fg_color=color)
            barra.grid(row=0, column=0, rowspan=2, padx=(14, 10), pady=14)
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
            if filtra and on_click is not None:
                for w in (tarjeta, barra, numero, etiqueta):
                    w.bind("<Button-1>", lambda _e, c=clave: on_click(c))
            self._tarjetas[clave] = (tarjeta, numero, etiqueta, filtra)

    def actualizar(self, valores: Dict[str, str], activa: Optional[str] = None) -> None:
        """Pone los números y resalta la tarjeta del filtro activo."""
        for clave, (tarjeta, numero, _etq, filtra) in self._tarjetas.items():
            numero.configure(text=valores.get(clave, "0"))
            resaltada = filtra and clave == activa
            tarjeta.configure(
                border_color=theme.PRIMARIO if resaltada else theme.BORDE,
                border_width=2 if resaltada else 1,
            )

    def etiqueta(self, clave: str, texto: str) -> None:
        self._tarjetas[clave][2].configure(text=texto)


# ── Buscador y menú de filtro ─────────────────────────────────────────────────


def buscador(master: Any, placeholder: str, on_cambio: Callable[[], None]) -> ctk.CTkEntry:
    # Sin textvariable: con ella CTkEntry no muestra el texto guía.
    entry = ctk.CTkEntry(
        master,
        placeholder_text=placeholder,
        height=36,
        fg_color=theme.TARJETA,
        border_color=theme.BORDE,
    )
    entry.bind("<KeyRelease>", lambda _e: on_cambio())

    def limpiar_busqueda(_evento: object) -> None:
        if entry.get():
            entry.delete(0, "end")
            on_cambio()

    entry.bind("<Escape>", limpiar_busqueda)
    return entry


def menu_filtro(
    master: Any, valores: List[str], on_cambio: Callable[[str], None], ancho: int = 210
) -> ctk.CTkOptionMenu:
    return ctk.CTkOptionMenu(
        master,
        values=valores,
        width=ancho,
        height=36,
        fg_color=theme.TARJETA,
        button_color=theme.SECUNDARIO,
        button_hover_color=theme.SECUNDARIO_HOVER,
        text_color=theme.TEXTO,
        dynamic_resizing=False,
        command=on_cambio,
    )


# ── Panel de detalle ──────────────────────────────────────────────────────────


def panel_detalle(master: Any, ancho: int = 320) -> ctk.CTkScrollableFrame:
    panel = ctk.CTkScrollableFrame(
        master,
        width=ancho,
        fg_color=theme.TARJETA,
        corner_radius=theme.RADIO,
        border_width=1,
        border_color=theme.BORDE,
        scrollbar_button_color=theme.BORDE,
        scrollbar_button_hover_color=theme.TEXTO_SUAVE,
    )
    panel.grid_columnconfigure(0, weight=1)
    return panel


def desplazar_hasta_fila(panel: Any, fila: int) -> None:
    """Desplaza un panel con scroll para que la fila ``fila`` quede arriba."""

    def mover() -> None:
        canvas = getattr(panel, "_parent_canvas", None)
        widgets = panel.grid_slaves(row=fila)
        if canvas is None or not widgets or not panel.winfo_exists():
            return
        alto = max(1, panel.winfo_height())
        canvas.yview_moveto(max(0.0, (widgets[0].winfo_y() - 8) / alto))

    panel.after(60, mover)


def limpiar(frame: Any) -> None:
    for hijo in frame.winfo_children():
        hijo.destroy()


def detalle_vacio(panel: Any, texto: str) -> None:
    ctk.CTkLabel(
        panel,
        text=texto,
        font=theme.fuente(theme.TAM_NORMAL),
        text_color=theme.TEXTO_SUAVE,
        justify="center",
    ).grid(row=0, column=0, sticky="ew", padx=16, pady=60)


def cabecera_detalle(
    panel: Any,
    fila: int,
    iniciales: str,
    color: theme.Color,
    titulo: str,
    subtitulo: str,
    ancho_texto: int = 180,
) -> int:
    cab = ctk.CTkFrame(panel, fg_color="transparent")
    cab.grid(row=fila, column=0, sticky="ew", padx=16, pady=(18, 0))
    cab.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(
        cab,
        text=iniciales,
        width=52,
        height=52,
        corner_radius=26,
        fg_color=color,
        text_color=theme.TEXTO_SOBRE_ROL,
        font=theme.fuente(18, "bold"),
    ).grid(row=0, column=0, rowspan=2, padx=(0, 12))
    ctk.CTkLabel(
        cab,
        text=titulo,
        font=theme.fuente(theme.TAM_SECCION + 1, "bold"),
        anchor="w",
        justify="left",
        wraplength=ancho_texto,
    ).grid(row=0, column=1, sticky="sw")
    ctk.CTkLabel(
        cab,
        text=subtitulo,
        font=theme.fuente(theme.TAM_PEQUENO),
        text_color=theme.TEXTO_SUAVE,
        anchor="w",
        justify="left",
        wraplength=ancho_texto,
    ).grid(row=1, column=1, sticky="nw")
    return fila + 1


def fila_insignias(
    panel: Any, fila: int, insignias: Sequence[Tuple[str, theme.Color, theme.Color]]
) -> int:
    contenedor = ctk.CTkFrame(panel, fg_color="transparent")
    contenedor.grid(row=fila, column=0, sticky="w", padx=16, pady=(12, 0))
    for col, (texto, color, fondo) in enumerate(insignias):
        insignia(contenedor, texto, color, fondo).grid(row=0, column=col, padx=(0, 6))
    return fila + 1


def insignia(master: Any, texto: str, color: theme.Color, fondo: theme.Color) -> ctk.CTkLabel:
    return ctk.CTkLabel(
        master,
        text=texto,
        fg_color=fondo,
        text_color=color,
        corner_radius=11,
        height=24,
        font=theme.fuente(theme.TAM_PEQUENO, "bold"),
        padx=10,
    )


def titulo_seccion(panel: Any, fila: int, texto: str, arriba: int = 18) -> int:
    ctk.CTkLabel(
        panel,
        text=texto.upper(),
        font=theme.fuente(theme.TAM_PEQUENO - 1, "bold"),
        text_color=theme.TEXTO_SUAVE,
        anchor="w",
    ).grid(row=fila, column=0, sticky="w", padx=16, pady=(arriba, 6))
    return fila + 1


def boton_accion(
    panel: Any,
    fila: int,
    texto: str,
    comando: Callable[[], None],
    primario: bool = False,
    peligro: bool = False,
    motivo_deshabilitado: Optional[str] = None,
) -> int:
    """Botón de ancho completo del panel; si hay motivo, sale deshabilitado y lo explica."""
    if primario:
        fg, hover, texto_color = theme.PRIMARIO, theme.PRIMARIO_HOVER, theme.TEXTO_SOBRE_PRIMARIO
    else:
        fg, hover = theme.SECUNDARIO, theme.SECUNDARIO_HOVER
        texto_color = theme.PELIGRO if peligro else theme.TEXTO
    contenedor = ctk.CTkFrame(panel, fg_color="transparent")
    contenedor.grid(row=fila, column=0, sticky="ew", padx=16, pady=(0, 6))
    contenedor.grid_columnconfigure(0, weight=1)
    boton = ctk.CTkButton(
        contenedor,
        text=texto,
        height=34,
        fg_color=fg,
        hover_color=hover,
        text_color=texto_color,
        text_color_disabled=theme.TEXTO_SUAVE,
        command=comando,
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
            justify="left",
            wraplength=270,
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
    return fila + 1


def bloque_datos(panel: Any, fila: int, lineas: Sequence[Tuple[str, str]]) -> int:
    """Tabla de dos columnas (etiqueta gris / valor) sobre fondo suave."""
    datos = ctk.CTkFrame(panel, fg_color=theme.TARJETA_ALT, corner_radius=8)
    datos.grid(row=fila, column=0, sticky="ew", padx=16)
    datos.grid_columnconfigure(1, weight=1)
    for i, (etq, valor) in enumerate(lineas):
        pad = (10 if i == 0 else 3, 10 if i == len(lineas) - 1 else 0)
        ctk.CTkLabel(
            datos,
            text=etq,
            font=theme.fuente(theme.TAM_PEQUENO),
            text_color=theme.TEXTO_SUAVE,
            anchor="w",
            height=20,
        ).grid(row=i, column=0, sticky="nw", padx=(12, 8), pady=pad)
        ctk.CTkLabel(
            datos,
            text=valor,
            font=theme.fuente(theme.TAM_PEQUENO),
            anchor="e",
            justify="right",
            wraplength=170,
            height=20,
        ).grid(row=i, column=1, sticky="ne", padx=(0, 12), pady=pad)
    return fila + 1


def texto_suave(panel: Any, fila: int, texto: str, abajo: int = 16) -> int:
    ctk.CTkLabel(
        panel,
        text=texto,
        font=theme.fuente(theme.TAM_PEQUENO),
        text_color=theme.TEXTO_SUAVE,
        anchor="w",
        justify="left",
        wraplength=280,
    ).grid(row=fila, column=0, sticky="w", padx=16, pady=(0, abajo))
    return fila + 1


def lista_items(
    panel: Any,
    fila: int,
    items: Sequence[Tuple[str, str]],
    debajo: bool = False,
    maximo: int = 40,
) -> int:
    """Lista compacta de ``(texto, detalle)`` sobre fondo suave.

    Con ``debajo=False`` el detalle va a la derecha (p. ej. una cantidad);
    con ``debajo=True`` va en gris bajo el texto (p. ej. el cargo de un
    empleado). Si hay más de ``maximo``, se muestra "y N más".
    """
    caja = ctk.CTkFrame(panel, fg_color=theme.TARJETA_ALT, corner_radius=8)
    caja.grid(row=fila, column=0, sticky="ew", padx=16, pady=(0, 4))
    caja.grid_columnconfigure(0, weight=1)
    visibles = list(items)[:maximo]
    chica = theme.fuente(theme.TAM_PEQUENO)
    for i, (texto, detalle) in enumerate(visibles):
        arriba = 8 if i == 0 else 2
        abajo = 8 if i == len(visibles) - 1 and len(items) <= maximo else 2
        if debajo:
            renglon = ctk.CTkFrame(caja, fg_color="transparent")
            renglon.grid(row=i, column=0, sticky="ew", padx=12, pady=(arriba, abajo))
            ctk.CTkLabel(renglon, text=texto, font=chica, anchor="w", height=18).grid(
                row=0, column=0, sticky="w"
            )
            if detalle:
                ctk.CTkLabel(
                    renglon,
                    text=detalle,
                    font=theme.fuente(theme.TAM_PEQUENO - 1),
                    text_color=theme.TEXTO_SUAVE,
                    anchor="w",
                    height=16,
                ).grid(row=1, column=0, sticky="w")
        else:
            ctk.CTkLabel(
                caja, text=texto, font=chica, anchor="w", justify="left", wraplength=210, height=20
            ).grid(row=i, column=0, sticky="w", padx=(12, 8), pady=(arriba, abajo))
            ctk.CTkLabel(
                caja, text=detalle, font=chica, text_color=theme.TEXTO_SUAVE, height=20
            ).grid(row=i, column=1, sticky="e", padx=(0, 12), pady=(arriba, abajo))
    if len(items) > maximo:
        ctk.CTkLabel(
            caja,
            text=f"y {len(items) - maximo} más…",
            font=theme.fuente(theme.TAM_PEQUENO - 1),
            text_color=theme.TEXTO_SUAVE,
            anchor="w",
        ).grid(row=len(visibles), column=0, sticky="w", padx=12, pady=(2, 8))
    return fila + 1


def chips_dias(master: Any, bitmask: int) -> ctk.CTkFrame:
    """Siete círculos L M M J V S D; los días del turno van resaltados."""
    fila = ctk.CTkFrame(master, fg_color="transparent")
    for col, (inicial, activo) in enumerate(dias_activos(bitmask)):
        ctk.CTkLabel(
            fila,
            text=inicial,
            width=28,
            height=28,
            corner_radius=14,
            fg_color=theme.PRIMARIO if activo else theme.NEUTRO_FONDO,
            text_color=theme.TEXTO_SOBRE_PRIMARIO if activo else theme.TEXTO_SUAVE,
            font=theme.fuente(theme.TAM_PEQUENO, "bold"),
        ).grid(row=0, column=col, padx=(0, 4))
    return fila


# ── Aviso que se oculta solo ──────────────────────────────────────────────────


class Aviso(ctk.CTkLabel):
    """Banda verde (o roja) con un mensaje corto que desaparece a los 4,5 s."""

    def __init__(self, master: Any, **grid: Any) -> None:
        super().__init__(
            master, text="", corner_radius=8, height=34, anchor="w", font=theme.fuente()
        )
        self._grid = grid
        self._job: Optional[str] = None

    def mostrar(self, texto: str, error: bool = False, segundos: float = 4.5) -> None:
        if self._job is not None:
            self.after_cancel(self._job)
        self.configure(
            text=f"   {texto}",
            fg_color=theme.ERROR_FONDO if error else theme.EXITO_FONDO,
            text_color=theme.ERROR if error else theme.EXITO,
        )
        self.grid(**self._grid)
        self._job = self.after(int(segundos * 1000), self._ocultar)

    def _ocultar(self) -> None:
        self._job = None
        self.grid_remove()


# ── Selector de fecha en español ──────────────────────────────────────────────


class SelectorFecha(ttk.Entry):
    """Campo de fecha (dd/mm/aaaa) con flecha que abre un calendario.

    Se ve igual que antes (mismo estilo que los combos ttk) pero es mucho
    más liviano que ``tkcalendar.DateEntry``: aquel creaba al instante una
    ventana oculta con su calendario completo y forzaba varios redibujos
    por cada campo, y eso hacía lento abrir Asistencia, Reportes y
    Sincronización. Aquí el calendario se crea recién la primera vez que
    se abre.

    El calendario se dibuja DENTRO de la misma ventana (diálogo o
    principal), puesto encima del formulario con ``place``: no hay ventana
    aparte, así que un diálogo modal no le bloquea los clics (pasaba en
    macOS). Se abre con la flecha (o Alt+↓) y se cierra al elegir un día,
    con Esc, con la flecha otra vez o con un clic fuera del calendario.
    Se puede escribir la fecha a mano; si al salir del campo no es válida,
    vuelve la última fecha buena. Al elegir un día se genera
    ``<<DateEntrySelected>>``, como en tkcalendar.
    """

    def __init__(
        self, master: Any, *, width: int = 12, font: Any = None, **kw_calendario: Any
    ) -> None:
        _asegurar_estilo_fecha(master)
        super().__init__(master, style=_ESTILO_FECHA, width=width, font=font)
        self._kw_calendario = dict(kw_calendario, font=font, selectmode="day")
        self._date: date = date.today()
        self._emergente: Optional[Calendar] = None
        self._oscuro = False  # modo en que se dibujó el calendario
        self._clic_fuera: Optional[str] = None
        self._set_text(self.format_date(self._date))
        # Igual que tkcalendar: al salir del campo, una fecha inválida se
        # reemplaza por la última válida.
        self.configure(validate="focusout", validatecommand=self.register(self._validate_date))
        self.bind("<ButtonPress-1>", self._al_presionar, add="+")
        self.bind("<Motion>", self._al_mover, add="+")
        self.bind("<Leave>", lambda _e: self.state(["!active"]), add="+")
        self.bind("<Alt-Down>", lambda _e: self.drop_down(), add="+")
        self.bind("<Escape>", self._esc, add="+")

    # ── Valor ─────────────────────────────────────────────────────────────

    @staticmethod
    def format_date(fecha: date) -> str:
        return fecha.strftime("%d/%m/%Y")

    @staticmethod
    def parse_date(texto: str) -> date:
        """dd/mm/aaaa (también d/m/aa). ``ValueError``/``IndexError`` si no se entiende."""
        dia, mes, anio = (re.findall(r"\d+", texto) + ["", "", ""])[:3]
        if not anio:
            raise IndexError(texto)
        a = 2000 + int(anio) if len(anio) == 2 else int(anio)
        d, m = int(dia), int(mes)
        if m > 12:  # 09/25/2026 → se entiende 25/09/2026
            d, m = m, d
        return date(a, m, d)

    def get_date(self) -> date:
        self._validate_date()
        return self._date

    def set_date(self, fecha: date) -> None:
        self._date = fecha
        self._set_text(self.format_date(fecha))

    def _set_text(self, texto: str) -> None:
        self.delete(0, "end")
        self.insert(0, texto)

    def _validate_date(self) -> bool:
        try:
            self._date = self.parse_date(self.get())
            return True
        except (ValueError, IndexError):
            self._set_text(self.format_date(self._date))
            return False

    # ── Abrir / cerrar ────────────────────────────────────────────────────

    def drop_down(self) -> None:
        if self.calendario_abierto():
            self._cerrar()
            return
        self._validate_date()
        ventana = self.winfo_toplevel()
        cal = self._calendario(ventana)
        cal.selection_set(self._date)
        cal.place(x=0, y=0)  # para conocer su tamaño real
        ventana.update_idletasks()
        ancho, alto = cal.winfo_width(), cal.winfo_height()
        x = self.winfo_rootx() - ventana.winfo_rootx()
        arriba = self.winfo_rooty() - ventana.winfo_rooty()
        abajo = arriba + self.winfo_height() + 2
        # Debajo del campo si cabe; si no, encima; y sin salirse por la derecha.
        y = abajo if abajo + alto <= ventana.winfo_height() or arriba < alto else arriba - alto - 2
        x = max(0, min(x, ventana.winfo_width() - ancho))
        cal.place(x=x, y=max(0, y))
        cal.lift()
        cal.focus_set()
        self.state(["pressed"])
        self._clic_fuera = ventana.bind("<ButtonPress-1>", self._al_clic, add="+")

    def calendario_abierto(self) -> bool:
        return self._emergente is not None and bool(self._emergente.winfo_ismapped())

    def _calendario(self, ventana: tk.Misc) -> Calendar:
        oscuro = theme.modo_oscuro()
        if self._emergente is not None and self._oscuro != oscuro:
            # El sistema cambió de modo claro/oscuro: se rehace con los colores nuevos.
            self._emergente.destroy()
            self._emergente = None
        if self._emergente is None:
            colores = {k: theme.resolver(c) for k, c in _COLORES_CALENDARIO.items()}
            cal = Calendar(ventana, **self._kw_calendario, **colores)
            self._oscuro = oscuro
            cal.bind("<<CalendarSelected>>", self._elegido)
            cal.bind("<Escape>", self._esc)
            self._emergente = cal
        return self._emergente

    def _cerrar(self) -> None:
        if self._emergente is not None:
            self._emergente.place_forget()
        self.state(["!pressed"])
        if self._clic_fuera is not None:
            _quitar_binding(self.winfo_toplevel(), "<ButtonPress-1>", self._clic_fuera)
            self._clic_fuera = None

    # ── Eventos ───────────────────────────────────────────────────────────

    def _en_la_flecha(self, evento: "tk.Event[tk.Misc]") -> bool:
        return "downarrow" in str(self.identify(evento.x, evento.y))

    def _al_presionar(self, evento: "tk.Event[tk.Misc]") -> Optional[str]:
        if "disabled" in self.state() or not self._en_la_flecha(evento):
            return None
        self.drop_down()
        return "break"  # que el clic en la flecha no mueva el cursor del texto

    def _al_mover(self, evento: "tk.Event[tk.Misc]") -> None:
        if "disabled" not in self.state():
            self.state(["active"] if self._en_la_flecha(evento) else ["!active"])

    def _elegido(self, _evento: object = None) -> None:
        assert self._emergente is not None
        fecha = self._emergente.selection_get()
        if fecha is not None:
            self.set_date(fecha)
            self.event_generate("<<DateEntrySelected>>")
        self._cerrar()
        self.focus_set()

    def _esc(self, _evento: object) -> Optional[str]:
        if not self.calendario_abierto():
            return None
        self._cerrar()
        self.focus_set()
        return "break"  # que Esc no cierre también el diálogo

    def _al_clic(self, evento: "tk.Event[tk.Misc]") -> None:
        if self._emergente is None or not self.calendario_abierto():
            return
        dentro = str(evento.widget)
        # El campo maneja su propia flecha; los clics en el calendario son suyos.
        if evento.widget is self or dentro.startswith(str(self._emergente)):
            return
        self._cerrar()

    def destroy(self) -> None:
        # El calendario es hijo de la ventana, no del campo: se borra a mano.
        try:
            self._cerrar()
        except tk.TclError:
            pass
        if self._emergente is not None:
            self._emergente.destroy()
            self._emergente = None
        super().destroy()


_ESTILO_FECHA = "DateEntry"
# Colores del calendario: se resuelven al abrirlo (siguen al modo activo).
_COLORES_CALENDARIO: Dict[str, theme.Color] = {
    "background": theme.PRIMARIO,
    "foreground": theme.TEXTO_SOBRE_PRIMARIO,
    "headersbackground": theme.TARJETA_ALT,
    "headersforeground": theme.TEXTO_SUAVE,
    "normalbackground": theme.TARJETA,
    "normalforeground": theme.TEXTO,
    "weekendbackground": theme.TARJETA,
    "weekendforeground": theme.TEXTO,
    "othermonthbackground": theme.TARJETA_ALT,
    "othermonthforeground": theme.TEXTO_SUAVE,
    "othermonthwebackground": theme.TARJETA_ALT,
    "othermonthweforeground": theme.TEXTO_SUAVE,
    "selectbackground": theme.PRIMARIO,
    "selectforeground": theme.TEXTO_SOBRE_PRIMARIO,
    "bordercolor": theme.BORDE,
}
_estilo_fecha_listo: set[int] = set()


def _asegurar_estilo_fecha(widget: tk.Misc) -> None:
    """El campo de fecha usa el dibujo del combo ttk (con su flecha).

    Se copia una sola vez por intérprete: cambiar un estilo ttk hace que
    TODOS los widgets ttk se redibujen, y tkcalendar lo hacía por cada campo.
    """
    clave = int(widget.tk.interpaddr())
    if clave in _estilo_fecha_listo:
        return
    estilo = ttk.Style(widget)
    estilo.layout(_ESTILO_FECHA, estilo.layout("TCombobox"))
    if not estilo.configure(_ESTILO_FECHA):
        conf = estilo.configure("TCombobox")
        if conf:
            estilo.configure(_ESTILO_FECHA, **conf)
    _estilo_fecha_listo.add(clave)


def _quitar_binding(widget: tk.Misc, secuencia: str, funcid: str) -> None:
    """Quita UN binding (``unbind`` de tkinter borraría todos los de la secuencia)."""
    try:
        script = str(widget.bind(secuencia))
        restantes = "\n".join(x for x in script.split("\n") if funcid not in x).strip()
        widget.tk.call("bind", str(widget), secuencia, restantes)
        widget.deletecommand(funcid)
    except tk.TclError:
        pass


def selector_fecha(master: Any, valor: Optional[date] = None) -> SelectorFecha:
    """Campo de fecha en español (dd/mm/aaaa) con los colores de la app."""
    picker = SelectorFecha(
        master,
        locale="es_ES",
        date_pattern="dd/mm/yyyy",
        width=12,
        font=(theme.familia(), -14),  # negativo = píxeles, como el resto de la app
        firstweekday="monday",
        showweeknumbers=False,
        borderwidth=0,
    )
    if valor is not None:
        picker.set_date(valor)
    return picker
