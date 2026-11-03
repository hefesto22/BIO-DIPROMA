"""Diálogo modal ``AsignarTurnoDialog``.

Formulario para asignar un turno a un empleado. Sirve dos casos de uso
que el caller decide externamente (y traduce al service correcto):

* **Asignar** — empleado sin turno vigente. La vista llama a
  ``EmpleadosController.asignar_turno``. Título: "Asignar turno".
* **Cambiar** — empleado con turno vigente. La vista llama a
  ``EmpleadosController.cambiar_turno`` (cierra el historial previo
  y abre uno nuevo atómicamente). Título: "Cambiar turno".

El diálogo es agnóstico a cuál se usó: produce siempre un payload
``(turno_id, fecha_inicio)``. El modo solo cambia el texto del título
y si se muestra el label informativo con el turno actual.

API:

    Igual que ``TurnoFormDialog``: callback ``on_submit(payload)``
    que devuelve ``None`` (OK, cierra) o ``str`` (error in-dialog).

Visual aprox 440x260 px:

    ┌─ Asignar turno ────────────────────────────┐
    │  Empleado: Juan Pérez (DNI 0801-...)       │
    │  Turno actual: Matutino 8-5 (desde 2026-01)│  ← solo "cambiar"
    │                                            │
    │  Nuevo turno:    [combo ▾]                 │
    │  Fecha inicio:   [YYYY-MM-DD]              │
    │                                            │
    │  (error)                                   │
    │                                            │
    │                    [Cancelar]  [Guardar]   │
    └────────────────────────────────────────────┘
"""

from __future__ import annotations

import datetime as _dt
import logging
from typing import Callable, Dict, List, Optional, Tuple, TypedDict, Union

import customtkinter as ctk


class AsignarTurnoPayload(TypedDict):
    """Payload del diálogo — idéntico en modos "asignar" y "cambiar".

    El caller distingue qué service method invocar según el modo con
    que construyó el diálogo.
    """

    turno_id: int
    fecha_inicio: str


# Callback del caller: recibe el payload y decide si cerrar o mostrar error.
SubmitFn = Callable[[AsignarTurnoPayload], Optional[str]]

# Opción de combo: (id en BD, label visible al usuario).
ComboOption = Tuple[int, str]


def _hoy_iso() -> str:
    """Fecha de hoy en formato ``YYYY-MM-DD``."""
    return _dt.date.today().isoformat()


class AsignarTurnoDialog(ctk.CTkToplevel):
    """Diálogo modal para asignar (o cambiar) el turno de un empleado."""

    def __init__(
        self,
        parent: ctk.CTkBaseClass,
        on_submit: SubmitFn,
        empleado_descripcion: str,
        turnos: List[ComboOption],
        modo_cambiar: bool = False,
        turno_actual_descripcion: Optional[str] = None,
        initial_turno_id: Optional[int] = None,
        initial_fecha_inicio: Optional[str] = None,
    ) -> None:
        """Construye el diálogo.

        Args:
            parent: Widget padre; el diálogo se vuelve transient suyo.
            on_submit: Callback del caller. ``None`` = OK (cierra);
                ``str`` = error a mostrar (no cierra).
            empleado_descripcion: Texto corto para identificar al
                empleado en la cabecera del diálogo. Ej:
                ``"Juan Pérez (DNI 0801-1990-12345)"``.
            turnos: Lista de ``(id, nombre)`` para el combo. Debe venir
                pre-filtrada por ``is_active`` por el caller.
            modo_cambiar: ``True`` si el empleado ya tiene turno
                vigente — cambia el título a "Cambiar turno" y muestra
                el label informativo con el turno actual.
            turno_actual_descripcion: Texto del turno vigente del
                empleado. Obligatorio si ``modo_cambiar=True``.
            initial_turno_id: Valor precargado del combo (útil si el
                caller quiere proponer un default distinto al primero).
            initial_fecha_inicio: Fecha precargada. Default = hoy.

        Raises:
            ValueError: Si ``turnos`` viene vacía, o si
                ``modo_cambiar=True`` y no se pasó ``turno_actual_descripcion``.
        """
        super().__init__(parent)
        self._on_submit = on_submit
        self._log = logging.getLogger(self.__class__.__name__)

        if not turnos:
            raise ValueError("Se requiere al menos un turno activo.")
        if modo_cambiar and not turno_actual_descripcion:
            raise ValueError("En modo cambiar se requiere 'turno_actual_descripcion'.")

        titulo = "Cambiar turno" if modo_cambiar else "Asignar turno"
        self.title(titulo)
        alto = 300 if modo_cambiar else 260
        self.geometry(f"460x{alto}")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())

        self._tur_map: Dict[str, int] = {label: id_ for id_, label in turnos}

        self._construir_ui(
            empleado_descripcion=empleado_descripcion,
            turnos=turnos,
            modo_cambiar=modo_cambiar,
            turno_actual_descripcion=turno_actual_descripcion,
            initial_turno_id=initial_turno_id,
            initial_fecha_inicio=initial_fecha_inicio or _hoy_iso(),
        )

        self.after(10, self._configurar_modal)

    # ── Construcción del árbol ────────────────────────────────────────────

    def _construir_ui(
        self,
        empleado_descripcion: str,
        turnos: List[ComboOption],
        modo_cambiar: bool,
        turno_actual_descripcion: Optional[str],
        initial_turno_id: Optional[int],
        initial_fecha_inicio: str,
    ) -> None:
        """Arma el formulario completo."""
        self.grid_columnconfigure(1, weight=1)
        pad_x = 16
        pad_y = 6
        row = 0

        # Cabecera: empleado.
        ctk.CTkLabel(
            self,
            text=f"Empleado: {empleado_descripcion}",
            anchor="w",
            wraplength=420,
            justify="left",
        ).grid(
            row=row,
            column=0,
            columnspan=4,
            sticky="ew",
            padx=pad_x,
            pady=(pad_x, pad_y),
        )
        row += 1

        # Turno actual (solo en modo cambiar).
        if modo_cambiar:
            ctk.CTkLabel(
                self,
                text=f"Turno actual: {turno_actual_descripcion}",
                anchor="w",
                text_color=("gray30", "gray70"),
                wraplength=420,
                justify="left",
            ).grid(
                row=row,
                column=0,
                columnspan=4,
                sticky="ew",
                padx=pad_x,
                pady=(0, pad_y),
            )
            row += 1

        # Combo de turno nuevo.
        label_turno = "Nuevo turno:" if modo_cambiar else "Turno:"
        ctk.CTkLabel(self, text=label_turno).grid(
            row=row, column=0, sticky="e", padx=(pad_x, 8), pady=pad_y
        )
        labels = [lbl for _id, lbl in turnos]
        self._combo_turno = ctk.CTkOptionMenu(self, values=labels)
        if initial_turno_id is not None:
            for id_, lbl in turnos:
                if id_ == initial_turno_id:
                    self._combo_turno.set(lbl)
                    break
        self._combo_turno.grid(
            row=row,
            column=1,
            columnspan=3,
            sticky="ew",
            padx=(0, pad_x),
            pady=pad_y,
        )
        row += 1

        # Fecha de inicio.
        ctk.CTkLabel(self, text="Fecha inicio:").grid(
            row=row, column=0, sticky="e", padx=(pad_x, 8), pady=pad_y
        )
        self._entry_fecha = ctk.CTkEntry(self, placeholder_text="YYYY-MM-DD", width=140)
        self._entry_fecha.insert(0, initial_fecha_inicio)
        self._entry_fecha.grid(row=row, column=1, sticky="w", pady=pad_y)
        row += 1

        # Mensaje de error.
        self._label_error = ctk.CTkLabel(
            self,
            text="",
            text_color=("red", "#ff6b6b"),
            wraplength=420,
            justify="left",
            anchor="w",
        )
        self._label_error.grid(
            row=row, column=0, columnspan=4, sticky="ew", padx=pad_x, pady=(pad_y, 4)
        )
        row += 1

        # Botones.
        botones = ctk.CTkFrame(self, fg_color="transparent")
        botones.grid(row=row, column=0, columnspan=4, sticky="e", padx=pad_x, pady=(4, pad_x))

        btn_cancel = ctk.CTkButton(
            botones,
            text="Cancelar",
            width=100,
            fg_color=("gray65", "gray35"),
            hover_color=("gray50", "gray45"),
            command=self._on_cancel,
        )
        btn_cancel.grid(row=0, column=0, padx=(0, 8))

        btn_save = ctk.CTkButton(botones, text="Guardar", width=100, command=self._on_save)
        btn_save.grid(row=0, column=1)

        # Enter dispara Guardar; Esc cancela.
        self._entry_fecha.bind("<Return>", lambda _e: self._on_save())
        self.bind("<Escape>", lambda _e: self._on_cancel())

        self._combo_turno.focus_set()

    def _configurar_modal(self) -> None:
        """Activa el grab modal una vez la ventana es visible."""
        try:
            self.grab_set()
        except Exception:  # noqa: BLE001 — si el widget ya fue destruido
            self._log.debug("grab_set falló (ventana destruida)")

    # ── Handlers ──────────────────────────────────────────────────────────

    def _on_cancel(self) -> None:
        """Cierra el diálogo sin invocar el callback."""
        self.destroy()

    def _on_save(self) -> None:
        """Arma el payload, invoca el callback y decide si cerrar."""
        payload_or_err = self._recolectar_payload()
        if isinstance(payload_or_err, str):
            self._mostrar_error(payload_or_err)
            return

        result = self._on_submit(payload_or_err)
        if result is None:
            self.destroy()
        else:
            self._mostrar_error(result)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _recolectar_payload(self) -> Union[AsignarTurnoPayload, str]:
        """Lee widgets y devuelve el payload o un mensaje de error UI."""
        tur_label = self._combo_turno.get()
        if tur_label not in self._tur_map:
            return "Seleccione un turno válido."
        fecha = self._entry_fecha.get().strip()
        if not fecha:
            return "Indique la fecha de inicio."
        return AsignarTurnoPayload(turno_id=self._tur_map[tur_label], fecha_inicio=fecha)

    def _mostrar_error(self, mensaje: str) -> None:
        """Muestra el mensaje de error debajo del form."""
        self._label_error.configure(text=mensaje)
