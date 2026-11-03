"""Diálogo modal ``DarDeBajaDialog``: archivar a un empleado con trazabilidad.

Emite ``(fecha_baja, motivo_baja, nota_baja)``. Reglas:
    * ``motivo_baja`` ∈ ``MotivoBaja``.
    * Si el motivo es ``OTRO``, la nota es obligatoria (se pre-valida acá
      para no hacer viajar al usuario; el resto lo valida el servicio).

Contrato sin cambios: ``on_submit(payload)`` devuelve ``None`` (cierra)
o un ``str`` con el error a mostrar.
"""

from __future__ import annotations

import datetime as _dt
from typing import Callable, Dict, Optional, Tuple, TypedDict, Union

import customtkinter as ctk

from core.models.empleado import MotivoBaja
from ui import theme
from ui.components.dialogo import DialogoModal
from ui.components.piezas import selector_fecha


class DarDeBajaPayload(TypedDict):
    """Payload del diálogo."""

    fecha_baja: str
    motivo_baja: str  # uno de ``MotivoBaja.*.value``
    nota_baja: Optional[str]


SubmitFn = Callable[[DarDeBajaPayload], Optional[str]]

# Orden visual y etiqueta en español; se persiste el ``.value`` del enum.
_MOTIVOS_LABELS: Tuple[Tuple[MotivoBaja, str], ...] = (
    (MotivoBaja.RENUNCIA, "Renuncia"),
    (MotivoBaja.DESPIDO, "Despido"),
    (MotivoBaja.FIN_CONTRATO, "Fin de contrato"),
    (MotivoBaja.JUBILACION, "Jubilación"),
    (MotivoBaja.FALLECIMIENTO, "Fallecimiento"),
    (MotivoBaja.OTRO, "Otro"),
)


class DarDeBajaDialog(DialogoModal):
    """Diálogo modal para dar de baja a un empleado."""

    def __init__(
        self,
        parent: ctk.CTkBaseClass,
        on_submit: SubmitFn,
        empleado_descripcion: str,
        initial_fecha_baja: Optional[str] = None,
    ) -> None:
        super().__init__(
            parent,
            titulo="Dar de baja",
            subtitulo=(
                f"{empleado_descripcion} dejará de aparecer entre los activos y se "
                "cierran sus turnos. Su historial de asistencia se conserva y puede "
                "reactivarlo después."
            ),
            ancho=500,
        )
        self._on_submit = on_submit
        self._motivo_map: Dict[str, str] = {label: m.value for m, label in _MOTIVOS_LABELS}

        c = self.cuerpo
        c.grid_columnconfigure((0, 1), weight=1, uniform="campo")
        negrita = theme.fuente(theme.TAM_PEQUENO, "bold")
        ctk.CTkLabel(c, text="Fecha de baja", font=negrita).grid(
            row=0, column=0, sticky="w", pady=(0, 3)
        )
        ctk.CTkLabel(c, text="Motivo", font=negrita).grid(
            row=0, column=1, sticky="w", padx=(12, 0), pady=(0, 3)
        )
        fecha = _dt.date.today()
        if initial_fecha_baja:
            try:
                fecha = _dt.date.fromisoformat(initial_fecha_baja)
            except ValueError:
                pass
        self._fecha = selector_fecha(c, fecha)
        self._fecha.grid(row=1, column=0, sticky="w")
        self._combo_motivo = ctk.CTkOptionMenu(
            c,
            values=list(self._motivo_map),
            height=34,
            fg_color=theme.SECUNDARIO,
            button_color=theme.SECUNDARIO_HOVER,
            button_hover_color=theme.BORDE,
            text_color=theme.TEXTO,
            command=lambda _v: self._actualizar_ayuda_nota(),
        )
        self._combo_motivo.grid(row=1, column=1, sticky="ew", padx=(12, 0))

        self._label_nota = ctk.CTkLabel(c, text="", font=negrita, anchor="w")
        self._label_nota.grid(row=2, column=0, columnspan=2, sticky="w", pady=(14, 3))
        self._nota = ctk.CTkTextbox(
            c,
            height=80,
            wrap="word",
            fg_color=theme.TARJETA_ALT,
            border_width=1,
            border_color=theme.BORDE,
        )
        self._nota.grid(row=3, column=0, columnspan=2, sticky="ew")
        self._actualizar_ayuda_nota()

        self.agregar_botones("Dar de baja", self._on_save, peligro=True)

    def _actualizar_ayuda_nota(self) -> None:
        obligatoria = self._motivo_map.get(self._combo_motivo.get()) == MotivoBaja.OTRO.value
        self._label_nota.configure(
            text="Nota (obligatoria para «Otro»)" if obligatoria else "Nota (opcional)"
        )

    def _on_save(self) -> None:
        payload = self._recolectar_payload()
        if isinstance(payload, str):
            self.mostrar_error(payload)
            return
        resultado = self._on_submit(payload)
        if resultado is None:
            self.cerrar()
        else:
            self.mostrar_error(resultado)

    def _recolectar_payload(self) -> Union[DarDeBajaPayload, str]:
        motivo = self._motivo_map.get(self._combo_motivo.get())
        if motivo is None:
            return "Seleccione un motivo."
        nota = self._nota.get("1.0", "end").strip() or None
        if motivo == MotivoBaja.OTRO.value and not nota:
            return "Para el motivo «Otro» escriba una nota que explique la baja."
        return DarDeBajaPayload(
            fecha_baja=self._fecha.get_date().isoformat(),
            motivo_baja=motivo,
            nota_baja=nota,
        )
