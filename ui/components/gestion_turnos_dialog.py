"""``GestionTurnosDialog``: turnos vigentes de un empleado (varios en paralelo).

    ┌ Turnos de Ana Lara ─────────────────────────────────────┐
    │ TURNOS VIGENTES                                          │
    │ ┌───────────────────────────────────────────────┐        │
    │ │ Administrativo L-V            [Quitar]        │        │
    │ │ Lun a Vie · 09:00–17:00 · desde 01/02/2024    │        │
    │ └───────────────────────────────────────────────┘        │
    │ AGREGAR OTRO TURNO                                       │
    │ Turno [ ▾ ]            Desde [📅]   [Agregar]            │
    │                                            [Listo]       │
    └──────────────────────────────────────────────────────────┘

Un empleado puede tener varios turnos vigentes siempre que cubran días
distintos (p. ej. L-V 9-17 y sábado 9-12); esa regla la valida el
servicio y su mensaje se muestra en el diálogo. Cada acción se guarda al
momento, por eso hay un solo botón "Listo".

Contrato sin cambios: recibe callables del controller por inyección.
"""

from __future__ import annotations

import datetime as _dt
import logging
from tkinter import messagebox
from typing import Callable, Dict, List, Optional

import customtkinter as ctk

from core.models.empleado import Empleado
from core.models.empleado_turno import EmpleadoTurno
from core.models.turno import Turno
from ui import formato, theme
from ui.components import piezas
from ui.components.dialogo import DialogoModal


class GestionTurnosDialog(DialogoModal):
    """Lista, agrega y quita turnos vigentes de un empleado."""

    def __init__(
        self,
        parent: ctk.CTkBaseClass,
        empleado: Empleado,
        list_vigentes_fn: Callable[[int], List[EmpleadoTurno]],
        list_turnos_fn: Callable[..., List[Turno]],
        asignar_fn: Callable[[int, int, str], None],
        cerrar_fn: Callable[[int, str], None],
        on_done: Optional[Callable[[], None]] = None,
    ) -> None:
        """Construye el diálogo.

        Args:
            parent: Widget padre.
            empleado: Empleado cuyos turnos se gestionan.
            list_vigentes_fn: ``controller.list_turnos_vigentes(emp_id)``.
            list_turnos_fn: ``controller.list_turnos(solo_activos=...)``.
            asignar_fn: ``controller.asignar_turno(emp_id, turno_id, fecha_inicio)``.
            cerrar_fn: ``controller.cerrar_asignacion(asignacion_id, fecha_fin)``.
            on_done: Se invoca tras cada cambio confirmado.
        """
        assert empleado.id is not None
        super().__init__(
            parent,
            titulo=f"Turnos de {empleado.nombres} {empleado.apellidos}",
            subtitulo=(
                "Puede tener varios turnos a la vez si cubren días distintos "
                "(por ejemplo, lunes a viernes y sábado). Los cambios se guardan al momento."
            ),
            ancho=620,
        )
        self._empleado = empleado
        self._list_vigentes = list_vigentes_fn
        self._list_turnos = list_turnos_fn
        self._asignar = asignar_fn
        self._cerrar = cerrar_fn
        self._on_done = on_done
        self._log = logging.getLogger(self.__class__.__name__)
        self._todos: Dict[int, Turno] = {}
        self._opciones: Dict[str, int] = {}
        self._vigentes: List[EmpleadoTurno] = []

        c = self.cuerpo
        ctk.CTkLabel(
            c,
            text="TURNOS VIGENTES",
            font=theme.fuente(theme.TAM_PEQUENO - 1, "bold"),
            text_color=theme.TEXTO_SUAVE,
        ).grid(row=0, column=0, sticky="w", pady=(0, 6))
        self._lista = ctk.CTkFrame(c, fg_color="transparent")
        self._lista.grid(row=1, column=0, sticky="ew")
        self._lista.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            c,
            text="AGREGAR OTRO TURNO",
            font=theme.fuente(theme.TAM_PEQUENO - 1, "bold"),
            text_color=theme.TEXTO_SUAVE,
        ).grid(row=2, column=0, sticky="w", pady=(16, 6))
        agregar = ctk.CTkFrame(c, fg_color=theme.TARJETA_ALT, corner_radius=8)
        agregar.grid(row=3, column=0, sticky="ew")
        agregar.grid_columnconfigure(0, weight=1)
        for col, texto in enumerate(("Turno", "Desde")):
            ctk.CTkLabel(agregar, text=texto, font=theme.fuente(theme.TAM_PEQUENO, "bold")).grid(
                row=0, column=col, sticky="w", padx=(12, 0), pady=(10, 3)
            )
        self._combo = ctk.CTkOptionMenu(
            agregar,
            values=[""],
            height=34,
            fg_color=theme.TARJETA,
            button_color=theme.SECUNDARIO,
            button_hover_color=theme.SECUNDARIO_HOVER,
            text_color=theme.TEXTO,
            dynamic_resizing=False,
        )
        self._combo.grid(row=1, column=0, sticky="ew", padx=(12, 0), pady=(0, 12))
        self._desde = piezas.selector_fecha(agregar, _dt.date.today())
        self._desde.grid(row=1, column=1, sticky="w", padx=(10, 0), pady=(0, 12))
        self._btn_agregar = piezas.boton_primario(
            agregar, "Agregar", self._on_agregar, ancho=100, alto=34
        )
        self._btn_agregar.grid(row=1, column=2, padx=12, pady=(0, 12))

        self.agregar_boton_cerrar("Listo")
        self._refrescar()

    # ── Datos ─────────────────────────────────────────────────────────────

    def _refrescar(self) -> None:
        assert self._empleado.id is not None
        try:
            vigentes = self._list_vigentes(self._empleado.id)
            todos = self._list_turnos(solo_activos=False)
        except Exception as exc:  # noqa: BLE001
            self._log.exception("Error refrescando GestionTurnosDialog")
            self.mostrar_error(f"No se pudieron cargar los turnos: {exc}")
            return
        self._todos = {t.id: t for t in todos if t.id is not None}
        self._vigentes = vigentes
        piezas.limpiar(self._lista)
        if not vigentes:
            ctk.CTkLabel(
                self._lista,
                text="No tiene turnos vigentes: su asistencia no se calcula hasta asignarle uno.",
                font=theme.fuente(theme.TAM_PEQUENO),
                text_color=theme.ALERTA,
                anchor="w",
                justify="left",
                wraplength=500,
            ).grid(row=0, column=0, sticky="w", pady=(0, 4))
        for i, asignacion in enumerate(vigentes):
            self._tarjeta(asignacion, i)

        asignados = {a.turno_id for a in vigentes}
        disponibles = [
            t for t in todos if t.is_active and t.id is not None and t.id not in asignados
        ]
        self._opciones = {t.nombre: t.id for t in disponibles if t.id is not None}
        etiquetas = list(self._opciones) or ["(no hay más turnos activos)"]
        self._combo.configure(values=etiquetas)
        self._combo.set(etiquetas[0])
        estado = "normal" if self._opciones else "disabled"
        self._combo.configure(state=estado)
        self._btn_agregar.configure(state=estado)

    def _tarjeta(self, asignacion: EmpleadoTurno, fila: int) -> None:
        turno = self._todos.get(asignacion.turno_id)
        tarjeta = ctk.CTkFrame(
            self._lista,
            fg_color=theme.TARJETA,
            corner_radius=8,
            border_width=1,
            border_color=theme.BORDE,
        )
        tarjeta.grid(row=fila, column=0, sticky="ew", pady=(0, 6))
        tarjeta.grid_columnconfigure(0, weight=1)
        nombre = turno.nombre if turno else f"Turno #{asignacion.turno_id}"
        detalle = formato.horario_texto(turno, con_dias=True) if turno else "Turno no disponible"
        ctk.CTkLabel(
            tarjeta, text=nombre, font=theme.fuente(theme.TAM_NORMAL, "bold"), anchor="w"
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 0))
        ctk.CTkLabel(
            tarjeta,
            text=f"{detalle}  ·  desde {formato.fecha(asignacion.fecha_inicio)}",
            font=theme.fuente(theme.TAM_PEQUENO),
            text_color=theme.TEXTO_SUAVE,
            anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=12, pady=(0, 10))
        ctk.CTkButton(
            tarjeta,
            text="Quitar",
            width=80,
            height=30,
            fg_color=theme.SECUNDARIO,
            hover_color=theme.SECUNDARIO_HOVER,
            text_color=theme.PELIGRO,
            command=lambda a=asignacion, n=nombre: self._on_quitar(a, n),
        ).grid(row=0, column=1, rowspan=2, padx=12)

    # ── Acciones ──────────────────────────────────────────────────────────

    def _on_quitar(self, asignacion: EmpleadoTurno, nombre: str) -> None:
        assert asignacion.id is not None
        if not messagebox.askyesno(
            "Quitar turno",
            f"¿Quitar «{nombre}»?\n\nLa asignación termina hoy: desde mañana ya no se "
            "calcula asistencia con este turno. El historial se conserva.",
            parent=self,
        ):
            return
        try:
            self._cerrar(asignacion.id, _dt.date.today().isoformat())
        except Exception as exc:  # noqa: BLE001
            self.mostrar_error(f"No se pudo quitar el turno: {exc}")
            return
        self.mostrar_error("")
        self._notificar()
        self._refrescar()

    def _on_agregar(self) -> None:
        assert self._empleado.id is not None
        turno_id = self._opciones.get(self._combo.get())
        if turno_id is None:
            self.mostrar_error("Seleccione un turno.")
            return
        choque = self._turno_que_choca(turno_id)
        if choque is not None:
            nuevo = self._todos[turno_id]
            self.mostrar_error(
                f"«{nuevo.nombre}» ({formato.dias_texto(nuevo.dias_totales)}) se cruza con "
                f"«{choque.nombre}» ({formato.dias_texto(choque.dias_totales)}), que ya tiene "
                "asignado. Quite primero ese turno o elija uno de otros días."
            )
            return
        try:
            self._asignar(self._empleado.id, turno_id, self._desde.get_date().isoformat())
        except Exception as exc:  # noqa: BLE001 — el servicio explica el motivo
            self.mostrar_error(str(exc))
            return
        self.mostrar_error("")
        self._notificar()
        self._refrescar()

    def _turno_que_choca(self, turno_id: int) -> Optional[Turno]:
        """Turno vigente que comparte algún día con ``turno_id`` (el servicio lo rechazaría)."""
        nuevo = self._todos.get(turno_id)
        if nuevo is None:
            return None
        for asignacion in self._vigentes:
            actual = self._todos.get(asignacion.turno_id)
            if actual is not None and actual.dias_totales & nuevo.dias_totales:
                return actual
        return None

    def _notificar(self) -> None:
        if self._on_done is not None:
            try:
                self._on_done()
            except Exception:  # noqa: BLE001
                self._log.exception("on_done falló (silenciado)")
