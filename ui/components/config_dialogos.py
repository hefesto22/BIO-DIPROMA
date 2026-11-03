"""Diálogos de Configuración: departamento, cargo, feriado y "pasar y archivar".

Mismo contrato que el resto de los formularios: ``on_submit(...)``
devuelve ``None`` (cierra) o un ``str`` con el error para mostrarlo
dentro del diálogo. Las reglas (nombre único, fecha válida…) las
valida el servicio.

Los diálogos de alta pueden "seguir agregando": con la casilla marcada,
al guardar se limpian y quedan abiertos para el siguiente. La casilla
recuerda su estado mientras la app esté abierta.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Callable, Dict, List, Optional, Tuple

import customtkinter as ctk

from ui import formato, theme
from ui.components import piezas
from ui.components.dialogo import DialogoModal

_CUALQUIER_DEPTO = "Cualquier departamento"
_SEGUIR = "Seguir agregando (no cerrar al guardar)"
_ELIJA = "Elija…"


class _SeguirAgregando:
    """Casilla "seguir agregando" compartida por los diálogos de alta."""

    recordar = False

    def __init__(self, master: ctk.CTkFrame, fila: int) -> None:
        self._casilla = ctk.CTkCheckBox(
            master,
            text=_SEGUIR,
            font=theme.fuente(theme.TAM_PEQUENO),
            checkbox_width=18,
            checkbox_height=18,
            command=self._guardar,
        )
        if _SeguirAgregando.recordar:
            self._casilla.select()
        self._casilla.grid(row=fila, column=0, columnspan=2, sticky="w", pady=(14, 0))

    def activo(self) -> bool:
        return bool(self._casilla.get())

    def _guardar(self) -> None:
        _SeguirAgregando.recordar = self.activo()


class NombreDialog(DialogoModal):
    """Un solo campo de texto (crear o renombrar un departamento)."""

    def __init__(
        self,
        parent: ctk.CTkBaseClass,
        titulo: str,
        subtitulo: str,
        etiqueta: str,
        on_submit: Callable[[str], Optional[str]],
        inicial: str = "",
        texto_ok: str = "Guardar",
        placeholder: str = "",
        permitir_varios: bool = False,
    ) -> None:
        super().__init__(parent, titulo=titulo, subtitulo=subtitulo, ancho=440)
        self._on_submit = on_submit
        self._creados = 0
        c = self.cuerpo
        ctk.CTkLabel(c, text=etiqueta, font=theme.fuente(theme.TAM_PEQUENO, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 3)
        )
        self._entry = ctk.CTkEntry(c, height=36, placeholder_text=placeholder)
        if inicial:
            self._entry.insert(0, inicial)
        self._entry.grid(row=1, column=0, sticky="ew")
        self._entry.bind("<Return>", lambda _e: self._guardar())
        self._seguir = _SeguirAgregando(c, 2) if permitir_varios else None
        self.agregar_botones(texto_ok, self._guardar)
        self.after(80, self._enfocar)

    def _enfocar(self) -> None:
        self._entry.focus_set()
        self._entry.select_range(0, "end")

    def _guardar(self) -> None:
        texto = str(self._entry.get()).strip()
        if not texto:
            self.mostrar_error("Escriba el nombre.")
            return
        error = self._on_submit(texto)
        if error is not None:
            self.mostrar_error(error)
        elif self._seguir is not None and self._seguir.activo():
            self._creados += 1
            self._entry.delete(0, "end")
            self.cancelar_ahora_es_listo()
            self.mostrar_ok(_texto_creado(texto, self._creados))
            self._entry.focus_set()
        else:
            self.cerrar()


class CargoDialog(DialogoModal):
    """Nombre del cargo + a qué departamento aplica (o a cualquiera)."""

    def __init__(
        self,
        parent: ctk.CTkBaseClass,
        titulo: str,
        departamentos: List[Tuple[int, str]],
        on_submit: Callable[[str, Optional[int]], Optional[str]],
        inicial_nombre: str = "",
        inicial_departamento_id: Optional[int] = None,
        texto_ok: str = "Guardar",
        permitir_varios: bool = False,
    ) -> None:
        super().__init__(
            parent,
            titulo=titulo,
            subtitulo=(
                "Si el cargo es propio de un departamento, al registrar un empleado solo "
                "aparecerá cuando se elija ese departamento."
            ),
            ancho=460,
        )
        self._on_submit = on_submit
        self._creados = 0
        self._ids: Dict[str, Optional[int]] = {_CUALQUIER_DEPTO: None}
        for dep_id, nombre in departamentos:
            self._ids[nombre] = dep_id
        negrita = theme.fuente(theme.TAM_PEQUENO, "bold")
        c = self.cuerpo
        ctk.CTkLabel(c, text="Nombre del cargo", font=negrita).grid(
            row=0, column=0, sticky="w", pady=(0, 3)
        )
        self._entry = ctk.CTkEntry(c, height=36, placeholder_text="ej. Auxiliar contable")
        if inicial_nombre:
            self._entry.insert(0, inicial_nombre)
        self._entry.grid(row=1, column=0, sticky="ew")
        self._entry.bind("<Return>", lambda _e: self._guardar())

        ctk.CTkLabel(c, text="Departamento", font=negrita).grid(
            row=2, column=0, sticky="w", pady=(14, 3)
        )
        self._menu = piezas.menu_filtro(c, list(self._ids), lambda _v: None, ancho=412)
        actual = next(
            (n for n, i in self._ids.items() if i == inicial_departamento_id), _CUALQUIER_DEPTO
        )
        self._menu.set(actual)
        self._menu.grid(row=3, column=0, sticky="ew")
        self._seguir = _SeguirAgregando(c, 4) if permitir_varios else None

        self.agregar_botones(texto_ok, self._guardar)
        self.after(80, self._entry.focus_set)

    def _guardar(self) -> None:
        nombre = str(self._entry.get()).strip()
        if not nombre:
            self.mostrar_error("Escriba el nombre del cargo.")
            return
        error = self._on_submit(nombre, self._ids.get(str(self._menu.get())))
        if error is not None:
            self.mostrar_error(error)
        elif self._seguir is not None and self._seguir.activo():
            # El departamento se queda: lo normal es cargar varios del mismo.
            self._creados += 1
            self._entry.delete(0, "end")
            self.cancelar_ahora_es_listo()
            self.mostrar_ok(_texto_creado(nombre, self._creados))
            self._entry.focus_set()
        else:
            self.cerrar()


class FeriadoDialog(DialogoModal):
    """Fecha + descripción de un feriado."""

    def __init__(
        self,
        parent: ctk.CTkBaseClass,
        titulo: str,
        on_submit: Callable[[str, str], Optional[str]],
        inicial_fecha: Optional[date] = None,
        inicial_descripcion: str = "",
        texto_ok: str = "Guardar",
        permitir_varios: bool = False,
    ) -> None:
        super().__init__(
            parent,
            titulo=titulo,
            subtitulo=(
                "Ese día nadie debe marcar: la asistencia mostrará «Feriado» y no se "
                "descuenta de las vacaciones."
            ),
            ancho=460,
        )
        self._on_submit = on_submit
        self._creados = 0
        negrita = theme.fuente(theme.TAM_PEQUENO, "bold")
        c = self.cuerpo
        c.grid_columnconfigure(0, weight=0)
        c.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(c, text="Fecha", font=negrita).grid(row=0, column=0, sticky="w", pady=(0, 3))
        self._fecha = piezas.selector_fecha(c, inicial_fecha or date.today())
        self._fecha.grid(row=1, column=0, sticky="w")
        self._dia = ctk.CTkLabel(
            c, text="", font=theme.fuente(theme.TAM_PEQUENO), text_color=theme.TEXTO_SUAVE
        )
        self._dia.grid(row=1, column=1, sticky="w", padx=(12, 0))
        for evento in ("<<DateEntrySelected>>", "<FocusOut>", "<KeyRelease>"):
            self._fecha.bind(evento, lambda _e: self._pintar_dia())

        ctk.CTkLabel(c, text="Descripción", font=negrita).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(14, 3)
        )
        self._entry = ctk.CTkEntry(c, height=36, placeholder_text="ej. Día de la Independencia")
        if inicial_descripcion:
            self._entry.insert(0, inicial_descripcion)
        self._entry.grid(row=3, column=0, columnspan=2, sticky="ew")
        self._entry.bind("<Return>", lambda _e: self._guardar())

        self._seguir = _SeguirAgregando(c, 4) if permitir_varios else None
        self._pintar_dia()
        self.agregar_botones(texto_ok, self._guardar)
        self.after(80, self._entry.focus_set)

    def _leer_fecha(self) -> Optional[date]:
        try:
            leida: date = self._fecha.parse_date(self._fecha.get())
        except (ValueError, IndexError):
            return None
        return leida

    def _pintar_dia(self) -> None:
        d = self._leer_fecha()
        self._dia.configure(text=formato.fecha_larga(d.isoformat()) if d else "")

    def _guardar(self) -> None:
        d = self._leer_fecha()
        if d is None:
            self.mostrar_error("Escriba la fecha como dd/mm/aaaa.")
            return
        descripcion = str(self._entry.get()).strip()
        if not descripcion:
            self.mostrar_error("Escriba la descripción del feriado.")
            return
        error = self._on_submit(d.isoformat(), descripcion)
        if error is not None:
            self.mostrar_error(error)
        elif self._seguir is not None and self._seguir.activo():
            # Se propone el día siguiente con la misma descripción: así se
            # cargan rápido los feriados de varios días (Semana Morazánica).
            self._creados += 1
            self._fecha.set_date(d + timedelta(days=1))
            self._pintar_dia()
            self.cancelar_ahora_es_listo()
            self.mostrar_ok(_texto_creado(f"{descripcion}, {d.strftime('%d/%m')}", self._creados))
            self._entry.focus_set()
        else:
            self.cerrar()


class PasarYArchivarDialog(DialogoModal):
    """Elegir a dónde pasa el personal antes de archivar un departamento o cargo."""

    def __init__(
        self,
        parent: ctk.CTkBaseClass,
        titulo: str,
        explicacion: str,
        etiqueta: str,
        opciones: List[Tuple[int, str]],
        on_submit: Callable[[int], Optional[str]],
        detalle: str = "",
    ) -> None:
        super().__init__(parent, titulo=titulo, subtitulo=explicacion, ancho=480)
        self._on_submit = on_submit
        self._ids = {nombre: i for i, nombre in opciones}
        c = self.cuerpo
        ctk.CTkLabel(c, text=etiqueta, font=theme.fuente(theme.TAM_PEQUENO, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 3)
        )
        # Sin destino elegido de entrada: que no se archive por un clic apurado.
        self._menu = piezas.menu_filtro(c, list(self._ids), lambda _v: None, ancho=432)
        self._menu.set(_ELIJA)
        self._menu.grid(row=1, column=0, sticky="ew")
        if detalle:
            ctk.CTkLabel(
                c,
                text=detalle,
                font=theme.fuente(theme.TAM_PEQUENO),
                text_color=theme.TEXTO_SUAVE,
                anchor="w",
                justify="left",
                wraplength=432,
            ).grid(row=2, column=0, sticky="w", pady=(12, 0))
        self.agregar_botones("Pasar y archivar", self._guardar, peligro=True)

    def _guardar(self) -> None:
        destino = self._ids.get(str(self._menu.get()))
        if destino is None:
            self.mostrar_error("Elija a dónde pasan.")
            return
        error = self._on_submit(destino)
        if error is None:
            self.cerrar()
        else:
            self.mostrar_error(error)


def _texto_creado(nombre: str, cantidad: int) -> str:
    total = "" if cantidad == 1 else f" ({cantidad} en total)"
    return f"✓ «{nombre}» guardado{total}. Escriba el siguiente."
