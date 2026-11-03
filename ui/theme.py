"""Paleta y tipografía compartidas de la UI de BIO-DIPROMA.

Todos los colores son tuplas ``(modo_claro, modo_oscuro)``, el formato que
customtkinter acepta directamente en ``fg_color``/``text_color``. Así cada
vista se ve bien con el modo de apariencia del sistema (claro u oscuro)
sin lógica extra.

Los widgets ``ttk`` (p. ej. la tabla) no entienden tuplas: para ellos se
usa :func:`resolver`, que devuelve el color del modo activo.

Regla para módulos nuevos o rediseñados: nada de colores sueltos en las
vistas — se toman de aquí, para que la app se vea como un solo producto.
"""

from __future__ import annotations

from typing import Dict, Final, Literal, Tuple

import customtkinter as ctk

Color = Tuple[str, str]
Peso = Literal["normal", "bold"]

# ── Marca ─────────────────────────────────────────────────────────────────────
PRIMARIO: Final[Color] = ("#1E4FA3", "#3B7DDD")
PRIMARIO_HOVER: Final[Color] = ("#173F84", "#2F68BD")
PRIMARIO_SUAVE: Final[Color] = ("#E7EEFA", "#1C2A40")

# ── Superficies ───────────────────────────────────────────────────────────────
FONDO: Final[Color] = ("#F3F5F8", "#17191D")
TARJETA: Final[Color] = ("#FFFFFF", "#202329")
TARJETA_ALT: Final[Color] = ("#F7F9FB", "#252931")
BORDE: Final[Color] = ("#E1E5EB", "#30353E")
SELECCION: Final[Color] = ("#DCE7F8", "#24395A")

# ── Texto ─────────────────────────────────────────────────────────────────────
TEXTO: Final[Color] = ("#111827", "#E6E8EB")
TEXTO_SUAVE: Final[Color] = ("#5B6472", "#9AA3AF")
TEXTO_SOBRE_PRIMARIO: Final[Color] = ("#FFFFFF", "#FFFFFF")

# ── Botones secundarios / peligro ─────────────────────────────────────────────
SECUNDARIO: Final[Color] = ("#E9EDF2", "#2C313A")
SECUNDARIO_HOVER: Final[Color] = ("#DCE1E8", "#363C46")
PELIGRO: Final[Color] = ("#C62828", "#D9534F")
PELIGRO_HOVER: Final[Color] = ("#A51F1F", "#B94440")

# ── Estados (texto, fondo de la "pastilla") ───────────────────────────────────
EXITO: Final[Color] = ("#166534", "#5BD08A")
EXITO_FONDO: Final[Color] = ("#DCF5E5", "#173A26")
ALERTA: Final[Color] = ("#9A5B00", "#F2B544")
ALERTA_FONDO: Final[Color] = ("#FFF1D6", "#3D2C0B")
ERROR: Final[Color] = ("#B42318", "#F47A70")
ERROR_FONDO: Final[Color] = ("#FDE4E1", "#43191A")
NEUTRO: Final[Color] = ("#4B5563", "#A3ABB7")
NEUTRO_FONDO: Final[Color] = ("#ECEFF3", "#2D323B")

# Color de acento por rol (avatar e indicadores).
COLOR_ROL: Final[Dict[str, Color]] = {
    "SUPERADMIN": ("#6D28D9", "#A78BFA"),
    "ADMIN": ("#1E4FA3", "#6EA2F0"),
    "REPORTES": ("#0F766E", "#4FD1C5"),
    "OPERADOR": ("#B45309", "#F2B544"),
}
COLOR_ROL_DEFECTO: Final[Color] = NEUTRO
# Texto sobre el color del rol: los acentos son oscuros en modo claro y
# claros (pastel) en modo oscuro, así que el texto invierte.
TEXTO_SOBRE_ROL: Final[Color] = ("#FFFFFF", "#111827")

# Ausencias: (texto, fondo) por tipo — vacaciones, día libre, permiso, incapacidad.
COLOR_AUSENCIA: Final[Dict[str, Tuple[Color, Color]]] = {
    "VACACIONES": (("#0F766E", "#4FD1C5"), ("#D5F5F0", "#123A36")),
    "DIA_LIBRE": (("#6D28D9", "#B49CFB"), ("#EDE5FD", "#2A2145")),
    "PERMISO": (ALERTA, ALERTA_FONDO),
    "INCAPACIDAD": (("#BE185D", "#F472B6"), ("#FCE4F0", "#3D1A2B")),
}

# ── Tipografía ────────────────────────────────────────────────────────────────
TAM_TITULO: Final[int] = 24
TAM_SUBTITULO: Final[int] = 13
TAM_SECCION: Final[int] = 15
TAM_NORMAL: Final[int] = 13
TAM_PEQUENO: Final[int] = 12
RADIO: Final[int] = 10


def fuente(size: int = TAM_NORMAL, weight: Peso = "normal") -> ctk.CTkFont:
    """Fuente de la app (misma familia que customtkinter usa en cada SO)."""
    return ctk.CTkFont(size=size, weight=weight)


def familia() -> str:
    """Familia tipográfica activa de customtkinter (Roboto en Windows, SF en macOS)."""
    return str(ctk.ThemeManager.theme["CTkFont"]["family"])


def modo_oscuro() -> bool:
    """``True`` si la app está pintándose en modo oscuro."""
    return str(ctk.get_appearance_mode()).lower() == "dark"


def resolver(color: Color) -> str:
    """Devuelve el color concreto del modo activo (para widgets ttk/tk)."""
    return color[1] if modo_oscuro() else color[0]


def color_rol(rol_code: str) -> Color:
    """Color de acento asociado a un rol (o neutro si es desconocido)."""
    return COLOR_ROL.get(rol_code, COLOR_ROL_DEFECTO)
