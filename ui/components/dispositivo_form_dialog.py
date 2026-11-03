"""Diálogo modal ``DispositivoFormDialog``: registrar o editar un reloj ZKTeco.

    ┌ Nuevo reloj ──────────────────────────────────────────────┐
    │ Nombre [ Reloj de la entrada                            ]  │
    │ Cómo se conecta [ La app lo consulta | El reloj envía ]    │
    │                                                            │
    │  (directo)  Dirección IP [192.168.1.201]  Puerto [4370]    │
    │             Clave de comunicación [0]   [ ] Usar UDP       │
    │                                                            │
    │  (push)     Número de serie [CDQ9…]   Dirección IP [ … ]   │
    │                                       [Cancelar] [Guardar] │
    └────────────────────────────────────────────────────────────┘

Que el reloj sea de huella, de rostro o de ambos no importa: lo que
cambia es si la app le pide las marcas (directo, puerto 4370) o si el
reloj las envía solo (push / ADMS, típico de los faciales nuevos).

Contrato: ``on_submit(payload)`` devuelve ``None`` (cierra) o un ``str``
con el error para mostrarlo en el diálogo. Las validaciones de fondo (IP,
duplicados, serie obligatoria en push) las hace el servicio.
"""

from __future__ import annotations

from typing import Callable, Optional, TypedDict

import customtkinter as ctk

from core.models.dispositivo import MODO_DIRECTO, MODO_PUSH, PUERTO_ZKTECO_DEFAULT
from ui import theme
from ui.components.dialogo import DialogoModal

_DIRECTO = "La app lo consulta"
_PUSH = "El reloj envía las marcas"

_AYUDA = {
    MODO_DIRECTO: (
        "Para la mayoría de los relojes (K40, iClock, iFace, uFace, MB…). La IP y el puerto "
        "se ven en el reloj: Menú › Comunicación › Red. El puerto casi siempre es "
        f"{PUERTO_ZKTECO_DEFAULT}."
    ),
    MODO_PUSH: (
        "Para relojes que envían las marcas solos (ADMS / «Servidor en la nube»), como "
        "muchos faciales nuevos: SpeedFace, ProFace, SenseFace. La serie está en la "
        "etiqueta del reloj o en Menú › Info del sistema."
    ),
}


class DispositivoFormPayload(TypedDict):
    """Resultado del form al pulsar Guardar."""

    nombre: str
    ip: str
    puerto: int
    modo: str
    clave_comunicacion: int
    usar_udp: bool
    numero_serie: Optional[str]


SubmitFn = Callable[[DispositivoFormPayload], Optional[str]]


class DispositivoFormDialog(DialogoModal):
    """Diálogo modal para crear o editar un reloj biométrico."""

    def __init__(
        self,
        parent: ctk.CTkBaseClass,
        title: str,
        on_submit: SubmitFn,
        initial_nombre: str = "",
        initial_ip: str = "",
        initial_puerto: int = PUERTO_ZKTECO_DEFAULT,
        initial_modo: str = MODO_DIRECTO,
        initial_clave: int = 0,
        initial_udp: bool = False,
        initial_serie: str = "",
        es_nuevo: Optional[bool] = None,
    ) -> None:
        nuevo = not initial_nombre if es_nuevo is None else es_nuevo
        super().__init__(parent, titulo=title, ancho=500)
        self._on_submit = on_submit
        negrita = theme.fuente(theme.TAM_PEQUENO, "bold")
        c = self.cuerpo
        c.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(c, text="Nombre", font=negrita).grid(row=0, column=0, sticky="w", pady=(0, 3))
        self._entry_nombre = ctk.CTkEntry(c, height=36, placeholder_text="ej. Reloj de la entrada")
        self._entry_nombre.insert(0, initial_nombre)
        self._entry_nombre.grid(row=1, column=0, sticky="ew")

        ctk.CTkLabel(c, text="Cómo se conecta", font=negrita).grid(
            row=2, column=0, sticky="w", pady=(14, 3)
        )
        self._modo = ctk.CTkSegmentedButton(
            c,
            values=[_DIRECTO, _PUSH],
            command=lambda _v: self._mostrar_modo(),
            height=32,
            font=theme.fuente(theme.TAM_PEQUENO),
            selected_color=theme.TARJETA,
            selected_hover_color=theme.TARJETA,
            unselected_color=theme.SECUNDARIO,
            unselected_hover_color=theme.SECUNDARIO_HOVER,
            fg_color=theme.SECUNDARIO,
            text_color=theme.TEXTO,
        )
        self._modo.set(_PUSH if initial_modo == MODO_PUSH else _DIRECTO)
        self._modo.grid(row=3, column=0, sticky="w")
        self._ayuda = ctk.CTkLabel(
            c,
            text="",
            font=theme.fuente(theme.TAM_PEQUENO - 1),
            text_color=theme.TEXTO_SUAVE,
            anchor="w",
            justify="left",
            wraplength=452,
        )
        self._ayuda.grid(row=4, column=0, sticky="w", pady=(6, 0))

        # ── Campos ────────────────────────────────────────────────────────
        campos = ctk.CTkFrame(c, fg_color="transparent")
        campos.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        campos.grid_columnconfigure(0, weight=1)
        self._campos = campos

        self._etq_serie = ctk.CTkLabel(campos, text="Número de serie", font=negrita)
        self._entry_serie = ctk.CTkEntry(campos, height=36, placeholder_text="ej. CDQ9201060223")
        self._entry_serie.insert(0, initial_serie)

        self._etq_ip = ctk.CTkLabel(campos, text="Dirección IP del reloj", font=negrita)
        self._etq_puerto = ctk.CTkLabel(campos, text="Puerto", font=negrita)
        self._entry_ip = ctk.CTkEntry(campos, height=36, placeholder_text="192.168.1.201")
        self._entry_ip.insert(0, initial_ip)
        self._entry_puerto = ctk.CTkEntry(campos, height=36, width=100, justify="center")
        self._entry_puerto.insert(0, str(initial_puerto))

        self._etq_clave = ctk.CTkLabel(campos, text="Clave de comunicación", font=negrita)
        self._entry_clave = ctk.CTkEntry(campos, height=36, width=140, justify="center")
        self._entry_clave.insert(0, str(initial_clave))
        self._nota_clave = ctk.CTkLabel(
            campos,
            text="0 si el reloj no tiene (Menú › Comunicación › Clave / Comm Key).",
            font=theme.fuente(theme.TAM_PEQUENO - 1),
            text_color=theme.TEXTO_SUAVE,
            anchor="w",
        )
        self._udp = ctk.CTkCheckBox(
            campos,
            text="Usar UDP (relojes antiguos que no responden de otra forma)",
            font=theme.fuente(theme.TAM_PEQUENO),
            checkbox_width=18,
            checkbox_height=18,
        )
        if initial_udp:
            self._udp.select()

        for entry in (
            self._entry_nombre,
            self._entry_ip,
            self._entry_puerto,
            self._entry_serie,
            self._entry_clave,
        ):
            entry.bind("<Return>", lambda _e: self._on_save())

        self._mostrar_modo()
        self.agregar_botones("Registrar reloj" if nuevo else "Guardar cambios", self._on_save)
        self.after(80, self._entry_nombre.focus_set)

    # ── Modo ──────────────────────────────────────────────────────────────

    def _modo_actual(self) -> str:
        return MODO_PUSH if self._modo.get() == _PUSH else MODO_DIRECTO

    def _mostrar_modo(self) -> None:
        modo = self._modo_actual()
        self._ayuda.configure(text=_AYUDA[modo])
        for w in self._campos.winfo_children():
            w.grid_forget()
        if modo == MODO_PUSH:
            self._etq_serie.grid(row=0, column=0, sticky="w", pady=(0, 3))
            self._entry_serie.grid(row=1, column=0, sticky="ew")
            self._etq_ip.grid(row=2, column=0, sticky="w", pady=(14, 3))
            self._entry_ip.grid(row=3, column=0, sticky="ew")
        else:
            self._etq_ip.grid(row=0, column=0, sticky="w", pady=(0, 3))
            self._etq_puerto.grid(row=0, column=1, sticky="w", padx=(12, 0), pady=(0, 3))
            self._entry_ip.grid(row=1, column=0, sticky="ew")
            self._entry_puerto.grid(row=1, column=1, sticky="w", padx=(12, 0))
            self._etq_clave.grid(row=2, column=0, sticky="w", pady=(14, 3))
            self._entry_clave.grid(row=3, column=0, sticky="w")
            self._nota_clave.grid(row=4, column=0, columnspan=2, sticky="w", pady=(3, 0))
            self._udp.grid(row=5, column=0, columnspan=2, sticky="w", pady=(12, 0))

    # ── Guardar ───────────────────────────────────────────────────────────

    def _on_save(self) -> None:
        modo = self._modo_actual()
        try:
            puerto = int(str(self._entry_puerto.get()).strip() or PUERTO_ZKTECO_DEFAULT)
        except ValueError:
            self.mostrar_error("El puerto debe ser un número (casi siempre 4370).")
            return
        try:
            clave = int(str(self._entry_clave.get()).strip() or "0")
        except ValueError:
            self.mostrar_error("La clave de comunicación debe ser un número (0 si no tiene).")
            return
        serie = str(self._entry_serie.get()).strip()
        error = self._on_submit(
            DispositivoFormPayload(
                nombre=str(self._entry_nombre.get()).strip(),
                ip=str(self._entry_ip.get()).strip(),
                puerto=puerto,
                modo=modo,
                clave_comunicacion=clave if modo == MODO_DIRECTO else 0,
                usar_udp=bool(self._udp.get()) if modo == MODO_DIRECTO else False,
                numero_serie=serie or None,
            )
        )
        if error is None:
            self.cerrar()
        else:
            self.mostrar_error(error)
