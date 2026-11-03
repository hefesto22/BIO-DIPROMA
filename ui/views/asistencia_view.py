"""Vista de Asistencia.

    Asistencia
    Entradas y salidas de cada día, calculadas con las marcas del reloj.
    [Hoy|Ayer|Esta semana|Este mes]  Desde [..] Hasta [..]        [Recalcular]
    ┌─────────┬──────────┬─────────┬─────────────┬──────────────────────┐
    │ A tiempo│ Tarde o… │ Ausentes│ Incompletas │ Vacaciones y permisos│  ← filtran
    └─────────┴──────────┴─────────┴─────────────┴──────────────────────┘
    [Buscar empleado…]  [Todos los estados ▾]  [x] Ocultar días sin turno
    ┌───────────────────────────────────────────────┐ ┌──────────────┐
    │ FECHA  EMPLEADO  TURNO  ENTRADA  SALIDA  ESTADO│ │ detalle del  │
    │ …                                              │ │ día y acciones│
    └───────────────────────────────────────────────┘ └──────────────┘

Carga sola al entrar (esta semana) y al cambiar el período. "Recalcular"
(permiso ``RUN_ZKTECO_SYNC``) vuelve a calcular el período con las marcas
del reloj, conservando las observaciones; corre en segundo plano. Desde
un día "Ausente" se puede registrar el permiso o las vacaciones directo
(si el usuario gestiona empleados).
"""

from __future__ import annotations

import logging
from datetime import date
from tkinter import messagebox
from typing import Callable, Dict, List, Optional, Tuple

import customtkinter as ctk

from core.models import permissions as perms
from core.models.asistencia import EstadoAsistencia
from core.models.ausencia import TipoAusencia
from core.models.turno import Turno
from core.services.asistencia_service import AsistenciaVista
from core.services.errors import (
    AsistenciaNotFoundError,
    AusenciaError,
    EmpleadoNotFoundError,
    InvalidDateError,
    InvalidRangoError,
    MissingRequiredFieldError,
)
from core.services.sincronizacion_result import ResultadoConsolidacion
from ui import asistencia_formato as af
from ui import formato, periodos, theme
from ui.async_util import run_async_ui
from ui.components import piezas
from ui.components.ausencia_form_dialog import AusenciaFormDialog, AusenciaPayload
from ui.components.data_table import Columna, DataTable, Fila
from ui.components.observacion_dialog import ObservacionDialog
from ui.components.usuarios_format import iniciales
from ui.controllers.asistencia_controller import AsistenciaController
from ui.controllers.ausencias_controller import AusenciasController

# Tope de filas: un mes de toda la empresa cabe de sobra.
_LIMITE_FILAS = 20000

_TODOS_LOS_ESTADOS = "Todos los estados"

_COLUMNAS = (
    Columna("fecha", "Fecha", ancho=88),
    Columna("empleado", "Empleado", ancho=200),
    Columna("entrada", "Entrada", ancho=72, alinear="center"),
    Columna("salida", "Salida", ancho=68, alinear="center"),
    Columna("estado", "Estado", ancho=175),
)

_ESTADO_COLOR: Dict[str, Tuple[theme.Color, theme.Color]] = {
    "alerta": (theme.ALERTA, theme.ALERTA_FONDO),
    "ausente": (theme.ERROR, theme.ERROR_FONDO),
    "justificada": theme.COLOR_AUSENCIA["VACACIONES"],
    "apagado": (theme.NEUTRO, theme.NEUTRO_FONDO),
}


class AsistenciaView(ctk.CTkFrame):
    """Vista raíz del módulo Asistencia."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        controller: AsistenciaController,
        ausencias: Optional[AusenciasController] = None,
        hoy: Callable[[], date] = date.today,
    ) -> None:
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self._controller = controller
        self._ausencias = (
            ausencias
            if ausencias is not None and controller.session.has_permission(perms.MANAGE_EMPLOYEES)
            else None
        )
        self._hoy = hoy
        self._log = logging.getLogger(self.__class__.__name__)
        self._items: List[AsistenciaVista] = []
        self._turnos: Dict[int, Turno] = {}
        self._filtro: Optional[str] = None
        self._ocupado = False
        self._construir_ui()
        self._usar_periodo(periodos.ESTA_SEMANA)

    # ── Construcción ──────────────────────────────────────────────────────

    def _construir_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)

        cabecera, _ = piezas.encabezado(
            self,
            "Asistencia",
            "Entradas y salidas de cada día, calculadas con las marcas del reloj.",
        )
        cabecera.grid(row=0, column=0, sticky="ew", padx=28, pady=(24, 14))

        periodo = ctk.CTkFrame(self, fg_color="transparent")
        periodo.grid(row=1, column=0, sticky="ew", padx=28, pady=(0, 12))
        periodo.grid_columnconfigure(5, weight=1)
        self._rapidos = ctk.CTkSegmentedButton(
            periodo,
            values=[periodos.HOY, periodos.AYER, periodos.ESTA_SEMANA, periodos.ESTE_MES],
            command=self._usar_periodo,
            height=34,
            font=theme.fuente(theme.TAM_PEQUENO),
            selected_color=theme.TARJETA,
            selected_hover_color=theme.TARJETA,
            unselected_color=theme.SECUNDARIO,
            unselected_hover_color=theme.SECUNDARIO_HOVER,
            fg_color=theme.SECUNDARIO,
            text_color=theme.TEXTO,
        )
        self._rapidos.grid(row=0, column=0, sticky="w")
        hoy = self._hoy()
        for col, texto in ((1, "Desde"), (3, "Hasta")):
            ctk.CTkLabel(
                periodo,
                text=texto,
                font=theme.fuente(theme.TAM_PEQUENO),
                text_color=theme.TEXTO_SUAVE,
            ).grid(row=0, column=col, padx=(16 if col == 1 else 10, 6))
        self._desde = piezas.selector_fecha(periodo, hoy)
        self._desde.grid(row=0, column=2)
        self._hasta = piezas.selector_fecha(periodo, hoy)
        self._hasta.grid(row=0, column=4)
        for picker in (self._desde, self._hasta):
            picker.bind("<<DateEntrySelected>>", lambda _e: self._fechas_a_mano())

        self._btn_recalcular: Optional[ctk.CTkButton] = None
        if self._controller.puede_re_consolidar():
            self._btn_recalcular = piezas.boton_secundario(
                periodo, "Recalcular período", self._on_recalcular, ancho=160
            )
            self._btn_recalcular.grid(row=0, column=6, sticky="e")
        self._progreso = ctk.CTkProgressBar(
            periodo, mode="indeterminate", height=4, progress_color=theme.PRIMARIO
        )

        self._aviso = piezas.Aviso(self, row=2, column=0, sticky="ew", padx=28, pady=(0, 12))

        self._kpis = piezas.FilaKpis(
            self,
            (
                (af.A_TIEMPO, "A tiempo", theme.EXITO, True),
                (af.CON_ATRASO, "Tarde o salida antes", theme.ALERTA, True),
                (af.AUSENTES, "Ausentes", theme.ERROR, True),
                (af.INCOMPLETAS, "Sin marca de salida", theme.NEUTRO, True),
                (
                    af.JUSTIFICADAS,
                    "Vacaciones y permisos",
                    theme.COLOR_AUSENCIA["VACACIONES"][0],
                    True,
                ),
            ),
            on_click=self._set_filtro,
        )
        self._kpis.grid(row=3, column=0, sticky="ew", padx=28, pady=(0, 14))

        barra = ctk.CTkFrame(self, fg_color="transparent")
        barra.grid(row=4, column=0, sticky="ew", padx=28, pady=(0, 12))
        barra.grid_columnconfigure(0, weight=1)
        self._buscador = piezas.buscador(barra, "Buscar empleado o turno…", self._render)
        self._buscador.grid(row=0, column=0, sticky="ew")
        self._menu_estado = piezas.menu_filtro(
            barra,
            [_TODOS_LOS_ESTADOS, *af.ESTADO_TEXTO.values()],
            lambda _v: self._render_y_detalle(),
            ancho=200,
        )
        self._menu_estado.set(_TODOS_LOS_ESTADOS)
        self._menu_estado.grid(row=0, column=1, padx=(10, 0))
        self._ocultar_sin_turno = ctk.CTkCheckBox(
            barra,
            text="Ocultar días sin turno",
            font=theme.fuente(theme.TAM_PEQUENO),
            checkbox_width=18,
            checkbox_height=18,
            command=self._render_y_detalle,
        )
        self._ocultar_sin_turno.select()
        self._ocultar_sin_turno.grid(row=0, column=2, padx=(14, 0))

        cuerpo = ctk.CTkFrame(self, fg_color="transparent")
        cuerpo.grid(row=5, column=0, sticky="nsew", padx=28, pady=(0, 20))
        cuerpo.grid_columnconfigure(0, weight=1)
        cuerpo.grid_rowconfigure(0, weight=1)
        self._tabla = DataTable(
            cuerpo,
            _COLUMNAS,
            on_seleccion=lambda _iid: self._mostrar_detalle(),
            on_doble_click=lambda _iid: self._editar_observacion(),
            texto_vacio="No hay asistencia calculada en este período.",
        )
        self._tabla.grid(row=0, column=0, sticky="nsew")
        self._tabla.color_tag("alerta", theme.ALERTA)
        self._tabla.color_tag("ausente", theme.ERROR)
        self._tabla.color_tag("justificada", theme.COLOR_AUSENCIA["VACACIONES"][0])
        self._tabla.color_tag("apagado", theme.TEXTO_SUAVE)
        self._detalle = piezas.panel_detalle(cuerpo, ancho=270)
        self._detalle.grid(row=0, column=1, sticky="nsew", padx=(14, 0))

    # ── Período ───────────────────────────────────────────────────────────

    def al_mostrar(self) -> None:
        """Al volver al módulo (la vista queda guardada): datos al día.

        Si hay un período rápido elegido («Esta semana»…) se recalculan sus
        fechas, por si cambió el día desde la última vez.
        """
        if self._ocupado:
            return  # se está recalculando: al terminar ya recarga
        rapido = str(self._rapidos.get())
        if rapido:
            desde, hasta = periodos.rango(rapido, self._hoy())
            self._desde.set_date(desde)
            self._hasta.set_date(hasta)
        self._cargar()

    def _usar_periodo(self, nombre: str) -> None:
        desde, hasta = periodos.rango(nombre, self._hoy())
        self._rapidos.set(nombre)
        self._desde.set_date(desde)
        self._hasta.set_date(hasta)
        self._cargar()

    def _fechas_a_mano(self) -> None:
        self._rapidos.set("")
        self._cargar()

    def _rango(self) -> Optional[Tuple[date, date]]:
        try:
            return self._desde.get_date(), self._hasta.get_date()
        except ValueError:
            return None

    # ── Datos ─────────────────────────────────────────────────────────────

    def _cargar(self, seleccionar: Optional[str] = None) -> None:
        rango = self._rango()
        if rango is None:
            self._aviso.mostrar("Escriba las fechas como dd/mm/aaaa.", error=True)
            return
        desde, hasta = rango
        if desde > hasta:
            self._aviso.mostrar("La fecha «Desde» no puede ser posterior a «Hasta».", error=True)
            return
        try:
            resultado = self._controller.list_asistencias(
                desde.isoformat(), hasta.isoformat(), limit=_LIMITE_FILAS
            )
            self._turnos = self._controller.turnos()
        except Exception:  # noqa: BLE001 — último recurso: log + aviso
            self._log.exception("Error cargando asistencias")
            self._aviso.mostrar("No se pudo cargar la asistencia. Intente de nuevo.", error=True)
            return
        self._items = resultado.items
        if resultado.truncado:
            self._aviso.mostrar(
                f"El período es muy grande: se muestran las primeras {_LIMITE_FILAS} filas. "
                "Elija un rango más corto.",
                error=True,
                segundos=8,
            )
        self._render()
        if seleccionar is not None:
            self._tabla.seleccionar(seleccionar)
        self._mostrar_detalle()

    def _visible(self, v: AsistenciaVista, texto: str, estado_sel: str) -> bool:
        a = v.asistencia
        if self._ocultar_sin_turno.get() and a.estado == EstadoAsistencia.SIN_TURNO.value:
            return False
        if self._filtro is not None and af.CATEGORIA.get(a.estado) != self._filtro:
            return False
        if estado_sel != _TODOS_LOS_ESTADOS and af.ESTADO_TEXTO.get(a.estado) != estado_sel:
            return False
        if texto:
            donde = f"{v.empleado_nombre_completo} {v.turno_nombre or ''}".casefold()
            if texto not in donde:
                return False
        return True

    def _render(self) -> None:
        texto = str(self._buscador.get()).strip().casefold()
        estado_sel = str(self._menu_estado.get())
        conteo = {k: 0 for k in (af.A_TIEMPO, af.CON_ATRASO, af.AUSENTES, af.INCOMPLETAS)}
        conteo[af.JUSTIFICADAS] = 0
        filas: List[Fila] = []
        for v in self._items:
            a = v.asistencia
            categoria = af.CATEGORIA.get(a.estado)
            if categoria is not None:
                conteo[categoria] += 1
            if a.id is None or not self._visible(v, texto, estado_sel):
                continue
            tag = af.TAG.get(a.estado)
            filas.append(
                Fila(
                    iid=str(a.id),
                    valores={
                        "fecha": af.fecha_con_dia(a.fecha),
                        "empleado": v.empleado_nombre_completo,
                        "entrada": af.hora(a.hora_entrada_real),
                        "salida": af.salida(a),
                        "estado": af.texto_estado(a) + ("  ✎" if af.tiene_nota(a) else ""),
                    },
                    orden={
                        "fecha": f"{a.fecha} {v.empleado_nombre_completo}",
                        "entrada": a.hora_entrada_real or "",
                        "salida": a.hora_salida_real or "",
                        "estado": f"{a.estado} {a.minutos_tarde:05d}",
                    },
                    tags=(tag,) if tag else (),
                )
            )
        self._tabla.set_filas(filas)
        self._kpis.actualizar({k: str(n) for k, n in conteo.items()}, activa=self._filtro)

    def _render_y_detalle(self) -> None:
        self._render()
        self._mostrar_detalle()

    def _set_filtro(self, clave: str) -> None:
        self._filtro = None if self._filtro == clave else clave
        self._render_y_detalle()

    def _seleccionada(self) -> Optional[AsistenciaVista]:
        iid = self._tabla.seleccionado()
        if iid is None:
            return None
        return next((v for v in self._items if v.asistencia.id == int(iid)), None)

    # ── Panel de detalle ──────────────────────────────────────────────────

    def _mostrar_detalle(self) -> None:
        panel = self._detalle
        piezas.limpiar(panel)
        v = self._seleccionada()
        if v is None:
            piezas.detalle_vacio(
                panel, "Seleccione un día\npara ver el detalle,\nanotar o justificar."
            )
            return
        a = v.asistencia
        fila = piezas.cabecera_detalle(
            panel,
            0,
            iniciales(v.empleado_nombre_completo),
            piezas.color_avatar(a.empleado_id),
            v.empleado_nombre_completo,
            formato.fecha_larga(a.fecha).capitalize(),
            ancho_texto=150,
        )
        color, fondo = _ESTADO_COLOR.get(af.TAG.get(a.estado, ""), (theme.EXITO, theme.EXITO_FONDO))
        fila = piezas.fila_insignias(
            panel, fila, [(af.ESTADO_TEXTO.get(a.estado, a.estado), color, fondo)]
        )

        fila = piezas.titulo_seccion(panel, fila, "Acciones", arriba=14)
        fila = piezas.boton_accion(
            panel,
            fila,
            "Editar observación" if a.observaciones else "Agregar observación",
            self._editar_observacion,
            primario=True,
        )
        justificable = a.estado in (
            EstadoAsistencia.AUSENTE.value,
            EstadoAsistencia.INCOMPLETO.value,
            EstadoAsistencia.TARDE.value,
            EstadoAsistencia.SALIDA_TEMPRANA.value,
            EstadoAsistencia.TARDE_Y_SALIDA_TEMPRANA.value,
        )
        if self._ausencias is not None and justificable:
            fila = piezas.boton_accion(
                panel, fila, "Registrar permiso o vacaciones", lambda: self._justificar(v)
            )
        if self._btn_recalcular is not None:
            fila = piezas.boton_accion(
                panel, fila, "Recalcular este día", lambda: self._recalcular_dia(v)
            )

        fila = piezas.titulo_seccion(panel, fila, "Marcas del día", arriba=12)
        lineas = [
            ("Turno", v.turno_nombre or "—"),
            ("Horario del día", self._horario_del_dia(v)),
            ("Entrada", af.hora(a.hora_entrada_real)),
            (
                "Salida",
                (
                    f"{af.hora(a.hora_salida_real)} (asumida)"
                    if af.salida_asumida(a)
                    else af.hora(a.hora_salida_real)
                ),
            ),
        ]
        if a.minutos_tarde:
            lineas.append(("Atraso", formato.duracion(a.minutos_tarde)))
        if a.minutos_salida_temprana:
            lineas.append(("Salió antes", formato.duracion(a.minutos_salida_temprana)))
        fila = piezas.bloque_datos(panel, fila, lineas)

        fila = piezas.titulo_seccion(panel, fila, "Observación")
        ctk.CTkLabel(
            panel,
            text=a.observaciones or "Sin observaciones.",
            font=theme.fuente(theme.TAM_PEQUENO),
            text_color=theme.TEXTO if a.observaciones else theme.TEXTO_SUAVE,
            anchor="w",
            justify="left",
            wraplength=230,
        ).grid(row=fila, column=0, sticky="w", padx=16, pady=(0, 16))

    def _horario_del_dia(self, v: AsistenciaVista) -> str:
        a = v.asistencia
        if a.turno_id_aplicado is None:
            return "—"
        turno = self._turnos.get(a.turno_id_aplicado)
        if turno is None:
            return "—"
        del_dia = turno.horario_para(date.fromisoformat(a.fecha).weekday())
        if del_dia is None:
            return "—"
        return f"{del_dia.hora_entrada} – {del_dia.hora_salida}"

    # ── Acciones ──────────────────────────────────────────────────────────

    def _editar_observacion(self) -> None:
        v = self._seleccionada()
        if v is None or v.asistencia.id is None:
            return
        asistencia_id = v.asistencia.id
        guardado = {"ok": False}

        def submit(texto: Optional[str]) -> Optional[str]:
            try:
                self._controller.update_observaciones(asistencia_id, texto)
            except AsistenciaNotFoundError:
                return "Ese día ya no existe. Actualice la lista."
            except Exception:  # noqa: BLE001
                self._log.exception("Error guardando observación id=%s", asistencia_id)
                return "No se pudo guardar la observación."
            guardado["ok"] = True
            return None

        self.wait_window(
            ObservacionDialog(
                self,
                subtitle=(
                    f"{formato.fecha_larga(v.asistencia.fecha).capitalize()} · "
                    f"{v.empleado_nombre_completo}"
                ),
                on_submit=submit,
                initial_text=v.asistencia.observaciones,
            )
        )
        if guardado["ok"]:
            self._cargar(seleccionar=str(asistencia_id))
            self._aviso.mostrar("Observación guardada.")

    def _justificar(self, v: AsistenciaVista) -> None:
        ctrl = self._ausencias
        if ctrl is None:
            return
        try:
            empleado = next(
                (e for e in ctrl.empleados(solo_activos=True) if e.id == v.asistencia.empleado_id),
                None,
            )
        except Exception:  # noqa: BLE001
            self._log.exception("Error buscando empleado")
            empleado = None
        if empleado is None:
            self._aviso.mostrar("El empleado ya no está activo.", error=True)
            return
        creada = {"ok": False}

        def submit(payload: AusenciaPayload) -> Optional[str]:
            try:
                ctrl.registrar(**payload)
            except (
                AusenciaError,
                EmpleadoNotFoundError,
                InvalidDateError,
                MissingRequiredFieldError,
            ) as exc:
                return str(exc)
            except Exception:  # noqa: BLE001
                self._log.exception("Error registrando ausencia")
                return "Ocurrió un error inesperado al registrar."
            creada["ok"] = True
            return None

        self.wait_window(
            AusenciaFormDialog(
                self,
                empleados=[empleado],
                on_submit=submit,
                contar_dias=ctrl.dias_laborables,
                empleado_fijo=empleado,
                tipo_inicial=TipoAusencia.PERMISO.value,
                fecha_inicial=date.fromisoformat(v.asistencia.fecha),
            )
        )
        if creada["ok"]:
            self._cargar(seleccionar=str(v.asistencia.id))
            self._aviso.mostrar("Registrado. El día ya no cuenta como falta.")

    def _recalcular_dia(self, v: AsistenciaVista) -> None:
        a = v.asistencia
        self._recalcular(a.fecha, a.fecha, a.empleado_id, seleccionar=str(a.id))

    def _on_recalcular(self) -> None:
        rango = self._rango()
        if rango is None or rango[0] > rango[1]:
            self._aviso.mostrar("Revise las fechas del período.", error=True)
            return
        desde, hasta = rango
        if not messagebox.askyesno(
            "Recalcular período",
            f"Se volverá a calcular la asistencia de todos los empleados del "
            f"{periodos.descripcion(desde, hasta)} con las marcas del reloj, los turnos, "
            "feriados y vacaciones actuales.\n\nLas observaciones se conservan. ¿Continuar?",
            parent=self,
        ):
            return
        self._recalcular(desde.isoformat(), hasta.isoformat(), None)

    def _recalcular(
        self,
        desde: str,
        hasta: str,
        empleado_id: Optional[int],
        seleccionar: Optional[str] = None,
    ) -> None:
        if self._ocupado:
            return
        self._ocupado = True
        if self._btn_recalcular is not None:
            self._btn_recalcular.configure(state="disabled", text="Recalculando…")
        self._progreso.grid(row=1, column=0, columnspan=7, sticky="ew", pady=(8, 0))
        self._progreso.start()

        def ok(resultado: ResultadoConsolidacion) -> None:
            self._fin_recalculo()
            self._cargar(seleccionar=seleccionar)
            dias = resultado.dias_procesados
            self._aviso.mostrar(
                f"Listo: se recalcularon {dias} día{'s' if dias != 1 else ''}"
                + ("." if empleado_id is not None else " de todos los empleados.")
            )

        def error(exc: BaseException) -> None:
            self._fin_recalculo()
            self._log.error("Error recalculando: %s", exc)
            if isinstance(exc, (InvalidRangoError, InvalidDateError)):
                texto = str(exc)
            elif isinstance(exc, EmpleadoNotFoundError):
                texto = "El empleado ya no está activo."
            else:
                texto = "No se pudo recalcular. Intente de nuevo."
            self._aviso.mostrar(texto, error=True)

        run_async_ui(
            self,
            work=lambda: self._controller.re_consolidar(
                desde=desde, hasta=hasta, empleado_id=empleado_id
            ),
            on_success=ok,
            on_error=error,
        )

    def _fin_recalculo(self) -> None:
        self._ocupado = False
        self._progreso.stop()
        self._progreso.grid_remove()
        if self._btn_recalcular is not None:
            self._btn_recalcular.configure(state="normal", text="Recalcular período")
