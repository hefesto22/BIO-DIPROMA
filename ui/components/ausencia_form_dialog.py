"""Diálogo modal para registrar o editar vacaciones, días libres y permisos.

    ┌ Registrar ausencia ───────────────────────────────────────┐
    │ Empleado  [ Karla Yessenia Villeda Romero          ▾ ]     │
    │ Tipo      [ Vacaciones | Día libre | Permiso | Incapacidad ]│
    │ Desde [02/11/2026]   Hasta [06/11/2026]   [ ] Un solo día  │
    │ Nota      [ Opcional: motivo, número de constancia…     ]  │
    │ [ 5 días laborables · lunes 2 de nov. al viernes 6 de nov.]│
    │                                     [Cancelar] [Registrar] │
    └────────────────────────────────────────────────────────────┘

El resumen cuenta los días en que el empleado debía trabajar según su
turno (sin domingos ni feriados): es lo que realmente se descuenta.

Contrato: ``on_submit(payload)`` devuelve ``None`` (cierra) o un ``str``
con el error para mostrar. Las reglas las valida ``AusenciaService``.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Callable, Dict, List, Optional, TypedDict

import customtkinter as ctk

from core.models.ausencia import TIPO_AUSENCIA_TEXTO, Ausencia, TipoAusencia
from core.models.empleado import Empleado
from ui import formato, theme
from ui.components import piezas
from ui.components.dialogo import DialogoModal


class AusenciaPayload(TypedDict):
    """Lo que devuelve el form al pulsar Registrar / Guardar."""

    empleado_id: int
    tipo: str
    fecha_desde: str
    fecha_hasta: str
    nota: Optional[str]


SubmitFn = Callable[[AusenciaPayload], Optional[str]]
ContarDiasFn = Callable[[int, str, str], int]

_TEXTO_A_TIPO: Dict[str, str] = {texto: tipo for tipo, texto in TIPO_AUSENCIA_TEXTO.items()}


def nombre_empleado(e: Empleado) -> str:
    return f"{e.nombres} {e.apellidos}"


class AusenciaFormDialog(DialogoModal):
    """Registrar (empleado a elegir o fijo) o editar (empleado fijo) una ausencia."""

    def __init__(
        self,
        parent: ctk.CTkBaseClass,
        empleados: List[Empleado],
        on_submit: SubmitFn,
        contar_dias: ContarDiasFn,
        inicial: Optional[Ausencia] = None,
        empleado_fijo: Optional[Empleado] = None,
        tipo_inicial: str = TipoAusencia.VACACIONES.value,
        hoy: Optional[date] = None,
        fecha_inicial: Optional[date] = None,
    ) -> None:
        editando = inicial is not None
        super().__init__(
            parent,
            titulo="Editar ausencia" if editando else "Registrar vacaciones o permiso",
            subtitulo=(
                "En estos días el empleado no debe marcar: la asistencia mostrará el motivo "
                "en vez de «Ausente». Los domingos y feriados no se cuentan."
            ),
            ancho=620,
        )
        self._on_submit = on_submit
        self._contar_dias = contar_dias
        hoy = hoy or date.today()

        self._por_nombre: Dict[str, Empleado] = {}
        for e in sorted(empleados, key=lambda x: (x.nombres, x.apellidos)):
            nombre = nombre_empleado(e)
            if nombre in self._por_nombre:  # homónimos: se distinguen por DNI
                nombre = f"{nombre} ({e.dni})"
            self._por_nombre[nombre] = e
        self._nombres = list(self._por_nombre)
        self._fijo = empleado_fijo
        if inicial is not None and self._fijo is None:
            self._fijo = next((e for e in empleados if e.id == inicial.empleado_id), None)

        c = self.cuerpo
        c.grid_columnconfigure(0, weight=0)
        c.grid_columnconfigure(1, weight=1)
        negrita = theme.fuente(theme.TAM_PEQUENO, "bold")

        def etiqueta(texto: str, fila: int) -> None:
            ctk.CTkLabel(c, text=texto, font=negrita, anchor="w", width=78).grid(
                row=fila, column=0, sticky="w", pady=(0 if fila == 0 else 12, 0)
            )

        # ── Empleado ──
        etiqueta("Empleado", 0)
        self._combo: Optional[ctk.CTkComboBox] = None
        if self._fijo is not None:
            ctk.CTkLabel(
                c,
                text=nombre_empleado(self._fijo),
                font=theme.fuente(theme.TAM_NORMAL, "bold"),
                anchor="w",
                height=34,
            ).grid(row=0, column=1, sticky="w")
        else:
            self._combo = ctk.CTkComboBox(
                c,
                values=self._nombres,
                height=34,
                command=lambda _v: self._actualizar_resumen(),
                button_color=theme.SECUNDARIO,
                button_hover_color=theme.SECUNDARIO_HOVER,
                border_color=theme.BORDE,
            )
            self._combo.set("")
            self._combo.grid(row=0, column=1, sticky="ew")
            # Escribir filtra la lista (se abre con la flecha).
            self._combo.bind("<KeyRelease>", lambda _e: self._filtrar())

        # ── Tipo ──
        etiqueta("Tipo", 1)
        self._tipo = ctk.CTkSegmentedButton(
            c,
            values=list(TIPO_AUSENCIA_TEXTO.values()),
            height=32,
            font=theme.fuente(theme.TAM_PEQUENO),
            # Mismo estilo "pastilla" que el resto de la app.
            selected_color=theme.TARJETA,
            selected_hover_color=theme.TARJETA,
            unselected_color=theme.SECUNDARIO,
            unselected_hover_color=theme.SECUNDARIO_HOVER,
            fg_color=theme.SECUNDARIO,
            text_color=theme.TEXTO,
            command=lambda _v: self._actualizar_resumen(),
        )
        self._tipo.grid(row=1, column=1, sticky="w", pady=(12, 0))
        tipo = inicial.tipo if inicial is not None else tipo_inicial
        self._tipo.set(TIPO_AUSENCIA_TEXTO.get(tipo, "Vacaciones"))

        # ── Fechas ──
        etiqueta("Fechas", 2)
        fechas = ctk.CTkFrame(c, fg_color="transparent")
        fechas.grid(row=2, column=1, sticky="w", pady=(12, 0))
        if inicial is not None:
            desde = date.fromisoformat(inicial.fecha_desde)
        else:
            desde = fecha_inicial or _proximo_habil(hoy)
        hasta = date.fromisoformat(inicial.fecha_hasta) if inicial else desde
        ctk.CTkLabel(fechas, text="Desde", font=theme.fuente(theme.TAM_PEQUENO)).grid(
            row=0, column=0, padx=(0, 6)
        )
        self._desde = piezas.selector_fecha(fechas, desde)
        self._desde.grid(row=0, column=1)
        self._lbl_hasta = ctk.CTkLabel(fechas, text="Hasta", font=theme.fuente(theme.TAM_PEQUENO))
        self._lbl_hasta.grid(row=0, column=2, padx=(14, 6))
        self._hasta = piezas.selector_fecha(fechas, hasta)
        self._hasta.grid(row=0, column=3)
        self._un_dia = ctk.CTkCheckBox(
            fechas,
            text="Un solo día",
            font=theme.fuente(theme.TAM_PEQUENO),
            checkbox_width=18,
            checkbox_height=18,
            command=self._alternar_un_dia,
        )
        self._un_dia.grid(row=0, column=4, padx=(16, 0))
        for selector in (self._desde, self._hasta):
            selector.bind("<<DateEntrySelected>>", lambda _e: self._al_cambiar_fecha())
            selector.bind("<FocusOut>", lambda _e: self._al_cambiar_fecha())
            selector.bind("<KeyRelease>", lambda _e: self._actualizar_resumen())

        # ── Nota ──
        etiqueta("Nota", 3)
        self._nota = ctk.CTkEntry(
            c, height=34, placeholder_text="Opcional: motivo, número de constancia…"
        )
        if inicial is not None and inicial.nota:
            self._nota.insert(0, inicial.nota)
        self._nota.grid(row=3, column=1, sticky="ew", pady=(12, 0))
        self._nota.bind("<Return>", lambda _e: self._on_save())

        self._resumen = ctk.CTkLabel(
            c,
            text="",
            fg_color=theme.TARJETA_ALT,
            corner_radius=8,
            anchor="w",
            justify="left",
            wraplength=500,
            font=theme.fuente(theme.TAM_PEQUENO),
            padx=12,
            pady=8,
        )
        self._resumen.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(16, 0))

        if desde == hasta:
            self._un_dia.select()
            self._alternar_un_dia()  # también pinta el resumen
        else:
            self._actualizar_resumen()
        self.agregar_botones("Guardar cambios" if editando else "Registrar", self._on_save)
        if self._combo is not None:
            self.after(80, self._combo.focus_set)

    # ── Interacción ───────────────────────────────────────────────────────

    def _filtrar(self) -> None:
        assert self._combo is not None
        texto = str(self._combo.get()).strip().lower()
        coincidencias = [n for n in self._nombres if texto in n.lower()] if texto else self._nombres
        self._combo.configure(values=coincidencias or self._nombres)
        self._actualizar_resumen()

    def _alternar_un_dia(self) -> None:
        if self._un_dia.get():
            self._lbl_hasta.grid_remove()
            self._hasta.grid_remove()
        else:
            self._lbl_hasta.grid()
            self._hasta.grid()
            rango = self._rango()
            if rango is not None and rango[1] < rango[0]:
                self._hasta.set_date(rango[0])
        self._actualizar_resumen()

    def _al_cambiar_fecha(self) -> None:
        # Si "Desde" queda después de "Hasta", "Hasta" lo acompaña.
        rango = self._rango()
        if rango is not None and not self._un_dia.get() and rango[1] < rango[0]:
            self._hasta.set_date(rango[0])
        self._actualizar_resumen()

    # ── Lectura de campos ─────────────────────────────────────────────────

    def _empleado(self) -> Optional[Empleado]:
        if self._fijo is not None:
            return self._fijo
        assert self._combo is not None
        texto = str(self._combo.get()).strip()
        if texto in self._por_nombre:
            return self._por_nombre[texto]
        coincidencias = [n for n in self._nombres if texto and texto.lower() in n.lower()]
        return self._por_nombre[coincidencias[0]] if len(coincidencias) == 1 else None

    def _rango(self) -> Optional[tuple[date, date]]:
        # ``parse_date`` y no ``get_date``: este último "valida" y, con una
        # fecha a medio escribir, repone la anterior en cada tecla.
        try:
            desde: date = self._desde.parse_date(self._desde.get())
            hasta: date = desde if self._un_dia.get() else self._hasta.parse_date(self._hasta.get())
        except (ValueError, IndexError):
            return None
        return desde, hasta

    # ── Resumen en vivo ───────────────────────────────────────────────────

    def _actualizar_resumen(self) -> None:
        rango = self._rango()
        if rango is None:
            self._mostrar("Escriba las fechas como dd/mm/aaaa.", error=True)
            return
        desde, hasta = rango
        if hasta < desde:
            self._mostrar("La fecha final no puede ser anterior a la inicial.", error=True)
            return
        cuando = (
            formato.fecha_larga(desde.isoformat())
            if desde == hasta
            else f"del {formato.fecha_larga(desde.isoformat())} "
            f"al {formato.fecha_larga(hasta.isoformat())}"
        )
        empleado = self._empleado()
        if empleado is None or empleado.id is None:
            self._mostrar(f"{cuando.capitalize()}. Elija el empleado.", error=False)
            return
        try:
            laborables = self._contar_dias(empleado.id, desde.isoformat(), hasta.isoformat())
        except Exception:  # noqa: BLE001 — el resumen es informativo, no bloquea
            self._log.exception("No se pudieron contar los días laborables")
            self._mostrar(cuando.capitalize(), error=False)
            return
        calendario = (hasta - desde).days + 1
        if laborables == 0:
            texto = (
                f"{cuando.capitalize()}: el empleado no trabaja en esas fechas según su turno "
                "(o son feriado). Revise las fechas."
            )
            self._mostrar(texto, error=True)
            return
        detalle = f"{formato.dias_cantidad(laborables)} laborable{'s' if laborables != 1 else ''}"
        if calendario != laborables:
            detalle += f" de {formato.dias_cantidad(calendario)} en total"
        self._mostrar(f"{detalle}  ·  {cuando}", error=False)

    def _mostrar(self, texto: str, error: bool) -> None:
        self._resumen.configure(text=texto, text_color=theme.ERROR if error else theme.TEXTO)

    # ── Guardar ───────────────────────────────────────────────────────────

    def _on_save(self) -> None:
        empleado = self._empleado()
        if empleado is None or empleado.id is None:
            self.mostrar_error("Elija un empleado de la lista.")
            return
        rango = self._rango()
        if rango is None:
            self.mostrar_error("Escriba las fechas como dd/mm/aaaa.")
            return
        desde, hasta = rango
        if hasta < desde:
            self.mostrar_error("La fecha final no puede ser anterior a la inicial.")
            return
        nota = str(self._nota.get()).strip() or None
        resultado = self._on_submit(
            AusenciaPayload(
                empleado_id=empleado.id,
                tipo=_TEXTO_A_TIPO.get(str(self._tipo.get()), TipoAusencia.VACACIONES.value),
                fecha_desde=desde.isoformat(),
                fecha_hasta=hasta.isoformat(),
                nota=nota,
            )
        )
        if resultado is None:
            self.cerrar()
        else:
            self.mostrar_error(resultado)


def _proximo_habil(dia: date) -> date:
    """Hoy si es de lunes a viernes; si es fin de semana, el lunes siguiente."""
    while dia.weekday() >= 5:
        dia += timedelta(days=1)
    return dia
