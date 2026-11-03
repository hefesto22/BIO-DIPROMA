"""Vista de Reportes.

    Reportes
    Exporte la asistencia a Excel para la planilla o para revisarla.
    [ Exportar | Historial de descargas ]
    ┌ Nuevo reporte ──────────┐ ┌──────┬──────┬──────┬──────────────┐
    │ [Semana|Mes|Mes pasado] │ │ días │ tarde│ ausen│ vac. y perm. │  ← totales del período
    │ [1.ª q.|2.ª q.|Todo]    │ └──────┴──────┴──────┴──────────────┘
    │ Desde [..] Hasta [..]   │
    │ Empleado [Todos ▾]      │ ┌ Vista previa del resumen ────────┐
    │ [ Exportar a Excel ]    │ │ EMPLEADO  PRESENTE  TARDE  …     │
    │ ✓ archivo.xlsx          │ │ …                                │
    │ [Abrir] [Abrir carpeta] │ └──────────────────────────────────┘
    └─────────────────────────┘

La vista previa es la misma hoja "Resumen" que llevará el Excel, para ver
antes de exportar. El export corre en segundo plano y se guarda solo en la
carpeta de reportes como ``Reporte_AAAA-MM-DD_HH-MM-SS[_Empleado].xlsx``:
nunca reemplaza un reporte anterior. El historial (permiso
``VIEW_EXPORT_HISTORY``) lista los archivos generados y permite abrirlos.
"""

from __future__ import annotations

import logging
import os
from datetime import date
from tkinter import messagebox
from typing import Callable, Dict, List, Optional, Tuple

import customtkinter as ctk

from core.models import permissions as perms
from core.models.descarga_reporte import DescargaReporte
from core.services.asistencia_exporter import ResumenEmpleado
from core.services.balance_tiempo import texto_duracion
from core.services.errors import (
    InvalidDateError,
    InvalidRangoError,
    ReporteIOError,
    ReporteSinDatosError,
)
from infrastructure import paths
from ui import formato, periodos, theme
from ui.abrir import abrir, abrir_carpeta_de
from ui.async_util import run_async_ui
from ui.components import piezas
from ui.components.data_table import Columna, DataTable, Fila
from ui.controllers.reporte_controller import ReporteController

_TAB_EXPORTAR = "Exportar"
_TAB_HISTORIAL = "Historial de descargas"
_TODOS = "Todos los empleados"

_DIAS = "DIAS"
_TARDE = "TARDE"
_AUSENTE = "AUSENTE"
_JUSTIFICADO = "JUSTIFICADO"
_EXTRA = "EXTRA"

_COLUMNAS_PREVIA = (
    Columna("empleado", "Empleado", ancho=200),
    Columna("presente", "A tiempo", ancho=70, alinear="center"),
    Columna("tarde", "Tarde", ancho=52, alinear="center"),
    Columna("ausente", "Faltas", ancho=56, alinear="center"),
    Columna("justificado", "Vac./perm.", ancho=78, alinear="center"),
    Columna("minutos", "Atraso", ancho=82, alinear="center"),
    Columna("extra", "Extra", ancho=82, alinear="center"),
    Columna("balance", "Balance", ancho=92, alinear="center"),
)

_COLUMNAS_HISTORIAL = (
    Columna("fecha", "Generado", ancho=135),
    Columna("periodo", "Período", ancho=150),
    Columna("empleado", "Empleado", ancho=170),
    Columna("filas", "Filas", ancho=60, alinear="center"),
)


def _justificados(r: ResumenEmpleado) -> int:
    return r.dias_vacaciones + r.dias_libre + r.dias_permiso + r.dias_incapacidad


def _atraso(r: ResumenEmpleado) -> int:
    return r.tiempo.tarde if r.tiempo is not None else r.total_minutos_tarde


def _balance_texto(minutos: int) -> str:
    """``+1 h 26 min`` (a favor), ``−45 min`` (debe), ``0`` (al día)."""
    if not minutos:
        return "0"
    return ("+" if minutos > 0 else "−") + formato.duracion(abs(minutos))


class ReportesView(ctk.CTkFrame):
    """Vista raíz del módulo Reportes (export + historial)."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        controller: ReporteController,
        hoy: Callable[[], date] = date.today,
    ) -> None:
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self._controller = controller
        self._hoy = hoy
        self._log = logging.getLogger(self.__class__.__name__)
        self._empleados: Dict[str, Optional[int]] = {_TODOS: None}
        self._nombres_por_id: Dict[int, str] = {}
        self._historial: List[DescargaReporte] = []
        self._tab_historial: Optional[ctk.CTkFrame] = None
        self._puede_historial = controller.session.has_permission(perms.VIEW_EXPORT_HISTORY)
        self._construir_ui()
        self._cargar_empleados()
        self._usar_periodo(periodos.ESTE_MES)

    # ── Construcción ──────────────────────────────────────────────────────

    def _construir_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        cabecera, _ = piezas.encabezado(
            self,
            "Reportes",
            "Exporte la asistencia a Excel para la planilla o para revisarla.",
        )
        cabecera.grid(row=0, column=0, sticky="ew", padx=28, pady=(24, 0))

        if self._puede_historial:
            selector = ctk.CTkSegmentedButton(
                self,
                values=[_TAB_EXPORTAR, _TAB_HISTORIAL],
                command=self._cambiar_tab,
                height=34,
                font=theme.fuente(theme.TAM_NORMAL),
                selected_color=theme.TARJETA,
                selected_hover_color=theme.TARJETA,
                unselected_color=theme.SECUNDARIO,
                unselected_hover_color=theme.SECUNDARIO_HOVER,
                fg_color=theme.SECUNDARIO,
                text_color=theme.TEXTO,
            )
            selector.set(_TAB_EXPORTAR)
            selector.grid(row=1, column=0, sticky="w", padx=28, pady=(18, 0))

        self._tab_exportar = ctk.CTkFrame(self, fg_color="transparent")
        self._tab_exportar.grid(row=2, column=0, sticky="nsew", padx=28, pady=(16, 20))
        self._tab_exportar.grid_columnconfigure(1, weight=1)
        self._tab_exportar.grid_rowconfigure(1, weight=1)
        self._construir_formulario(self._tab_exportar).grid(
            row=0, column=0, rowspan=2, sticky="nsw", padx=(0, 14)
        )
        self._kpis = piezas.FilaKpis(
            self._tab_exportar,
            (
                (_DIAS, "Días a tiempo", theme.EXITO, False),
                (_TARDE, "Llegadas tarde", theme.ALERTA, False),
                (_AUSENTE, "Faltas", theme.ERROR, False),
                (
                    _JUSTIFICADO,
                    "Vac. y permisos",
                    theme.COLOR_AUSENCIA["VACACIONES"][0],
                    False,
                ),
                (_EXTRA, "Extra neto", theme.PRIMARIO, False),
            ),
        )
        self._kpis.grid(row=0, column=1, sticky="ew", pady=(0, 14))

        previa = ctk.CTkFrame(self._tab_exportar, fg_color="transparent")
        previa.grid(row=1, column=1, sticky="nsew")
        previa.grid_columnconfigure(0, weight=1)
        previa.grid_rowconfigure(1, weight=1)
        self._titulo_previa = ctk.CTkLabel(
            previa,
            text="Vista previa del resumen",
            font=theme.fuente(theme.TAM_SECCION, "bold"),
            anchor="w",
        )
        self._titulo_previa.grid(row=0, column=0, sticky="w", pady=(0, 8))
        self._tabla_previa = DataTable(
            previa,
            _COLUMNAS_PREVIA,
            texto_vacio="No hay asistencia calculada en este período.",
        )
        self._tabla_previa.grid(row=1, column=0, sticky="nsew")

    def _construir_formulario(self, master: ctk.CTkFrame) -> ctk.CTkScrollableFrame:
        card = piezas.panel_detalle(master, ancho=270)
        self._card = card
        negrita = theme.fuente(theme.TAM_PEQUENO, "bold")
        ctk.CTkLabel(card, text="Nuevo reporte", font=theme.fuente(theme.TAM_SECCION, "bold")).grid(
            row=0, column=0, sticky="w", padx=16, pady=(18, 12)
        )

        ctk.CTkLabel(card, text="Período", font=negrita).grid(
            row=1, column=0, sticky="w", padx=16, pady=(0, 4)
        )
        self._rapidos = ctk.CTkSegmentedButton(
            card,
            values=[periodos.ESTA_SEMANA, periodos.ESTE_MES, periodos.MES_PASADO],
            command=self._usar_periodo,
            height=32,
            font=theme.fuente(theme.TAM_PEQUENO),
            selected_color=theme.TARJETA,
            selected_hover_color=theme.TARJETA,
            unselected_color=theme.SECUNDARIO,
            unselected_hover_color=theme.SECUNDARIO_HOVER,
            fg_color=theme.SECUNDARIO,
            text_color=theme.TEXTO,
        )
        self._rapidos.grid(row=2, column=0, sticky="ew", padx=16)
        self._quincenas = ctk.CTkSegmentedButton(
            card,
            values=[periodos.QUINCENA_1, periodos.QUINCENA_2, periodos.TODO],
            command=self._usar_periodo,
            height=32,
            font=theme.fuente(theme.TAM_PEQUENO),
            selected_color=theme.TARJETA,
            selected_hover_color=theme.TARJETA,
            unselected_color=theme.SECUNDARIO,
            unselected_hover_color=theme.SECUNDARIO_HOVER,
            fg_color=theme.SECUNDARIO,
            text_color=theme.TEXTO,
        )
        self._quincenas.grid(row=3, column=0, sticky="ew", padx=16, pady=(6, 0))

        fechas = ctk.CTkFrame(card, fg_color="transparent")
        fechas.grid(row=4, column=0, sticky="ew", padx=16, pady=(10, 0))
        fechas.grid_columnconfigure((0, 1), weight=1, uniform="fecha")
        for col, texto in enumerate(("Desde", "Hasta")):
            ctk.CTkLabel(
                fechas,
                text=texto,
                font=theme.fuente(theme.TAM_PEQUENO),
                text_color=theme.TEXTO_SUAVE,
            ).grid(row=0, column=col, sticky="w", padx=(0 if col == 0 else 6, 0))
        hoy = self._hoy()
        self._desde = piezas.selector_fecha(fechas, hoy)
        self._desde.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        self._hasta = piezas.selector_fecha(fechas, hoy)
        self._hasta.grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=(2, 0))
        for picker in (self._desde, self._hasta):
            picker.bind("<<DateEntrySelected>>", lambda _e: self._fechas_a_mano())

        ctk.CTkLabel(card, text="Empleado", font=negrita).grid(
            row=5, column=0, sticky="w", padx=16, pady=(14, 4)
        )
        self._menu_empleado = piezas.menu_filtro(
            card, [_TODOS], lambda _v: self._actualizar_previa(), ancho=238
        )
        self._menu_empleado.grid(row=6, column=0, sticky="ew", padx=16)

        ctk.CTkLabel(
            card,
            text=(
                "El Excel trae: Resumen, Estadísticas (asistencia, puntualidad, "
                "jornada), Balance de tiempo (atrasos contra extra), Ausencias y "
                "permisos con sus fechas, Detalle por día, todas las Marcas e "
                "Información. Se guarda como Reporte_fecha_hora."
            ),
            font=theme.fuente(theme.TAM_PEQUENO),
            text_color=theme.TEXTO_SUAVE,
            anchor="w",
            justify="left",
            wraplength=235,
        ).grid(row=7, column=0, sticky="w", padx=16, pady=(12, 0))
        self._btn_exportar = piezas.boton_primario(
            card, "Exportar a Excel", self._on_exportar, alto=40
        )
        self._btn_exportar.grid(row=8, column=0, sticky="ew", padx=16, pady=(16, 0))
        self._progreso = ctk.CTkProgressBar(
            card, mode="indeterminate", height=6, progress_color=theme.PRIMARIO
        )
        self._resultado = ctk.CTkFrame(card, fg_color="transparent")
        self._resultado.grid(row=10, column=0, sticky="ew", padx=16, pady=(14, 16))
        self._resultado.grid_columnconfigure(0, weight=1)
        return card

    # ── Datos ─────────────────────────────────────────────────────────────

    def al_mostrar(self) -> None:
        """Al volver al módulo (la vista queda guardada): datos al día.

        Se conserva lo elegido (empleado y período); un período rápido se
        recalcula por si cambió el día desde la última vez.
        """
        self._cargar_empleados(conservar=True)
        rapido = str(self._rapidos.get()) or str(self._quincenas.get())
        if rapido:
            self._usar_periodo(rapido)
        else:
            self._actualizar_previa()
        if self._tab_historial is not None and self._tab_historial.winfo_ismapped():
            self._cargar_historial()

    def _cargar_empleados(self, conservar: bool = False) -> None:
        try:
            tuplas = self._controller.list_empleados_para_filtro()
        except Exception:  # noqa: BLE001
            self._log.exception("Error cargando empleados")
            tuplas = []
        actual = str(self._menu_empleado.get())
        self._nombres_por_id = dict(tuplas)
        self._empleados = {_TODOS: None, **{nombre: i for i, nombre in tuplas}}
        self._menu_empleado.configure(values=list(self._empleados))
        self._menu_empleado.set(actual if conservar and actual in self._empleados else _TODOS)

    def _usar_periodo(self, nombre: str) -> None:
        if nombre == periodos.TODO:
            desde, hasta = periodos.rango_todo(self._primera_fecha(), self._hoy())
        else:
            desde, hasta = periodos.rango(nombre, self._hoy())
        rapidos = (periodos.ESTA_SEMANA, periodos.ESTE_MES, periodos.MES_PASADO)
        self._rapidos.set(nombre if nombre in rapidos else "")
        self._quincenas.set(
            nombre if nombre in (periodos.QUINCENA_1, periodos.QUINCENA_2, periodos.TODO) else ""
        )
        self._desde.set_date(desde)
        self._hasta.set_date(hasta)
        self._actualizar_previa()

    def _primera_fecha(self) -> Optional[date]:
        """Primer día con asistencia calculada (para "Todo")."""
        try:
            iso = self._controller.primera_fecha()
        except Exception:  # noqa: BLE001 — sin el dato, "Todo" cae en hoy
            self._log.exception("Error leyendo la primera fecha con datos")
            return None
        return date.fromisoformat(iso) if iso else None

    def _fechas_a_mano(self) -> None:
        self._rapidos.set("")
        self._quincenas.set("")
        self._actualizar_previa()

    def _parametros(self) -> Optional[Tuple[date, date, Optional[int]]]:
        try:
            desde, hasta = self._desde.get_date(), self._hasta.get_date()
        except ValueError:
            return None
        return desde, hasta, self._empleados.get(str(self._menu_empleado.get()))

    def _actualizar_previa(self) -> None:
        parametros = self._parametros()
        if parametros is None or parametros[0] > parametros[1]:
            self._titulo_previa.configure(text="Revise las fechas del período")
            self._tabla_previa.set_filas([])
            return
        desde, hasta, empleado_id = parametros
        try:
            resumen = self._controller.vista_previa(
                desde.isoformat(), hasta.isoformat(), empleado_id
            )
        except Exception:  # noqa: BLE001
            self._log.exception("Error calculando la vista previa")
            resumen = []
        filas: List[Fila] = []
        for r in resumen:
            justificados = _justificados(r)
            extra = r.tiempo.extra_total if r.tiempo is not None else 0
            balance = r.tiempo.balance if r.tiempo is not None else 0
            filas.append(
                Fila(
                    iid=str(r.empleado_id),
                    valores={
                        "empleado": r.empleado_nombre,
                        "presente": str(r.dias_presente),
                        "tarde": str(r.dias_tarde),
                        "ausente": str(r.dias_ausente),
                        "justificado": str(justificados),
                        "minutos": formato.duracion(_atraso(r)) if _atraso(r) else "—",
                        "extra": formato.duracion(extra) if extra else "—",
                        "balance": _balance_texto(balance),
                    },
                    orden={
                        "presente": r.dias_presente,
                        "tarde": r.dias_tarde,
                        "ausente": r.dias_ausente,
                        "justificado": justificados,
                        "minutos": _atraso(r),
                        "extra": extra,
                        "balance": balance,
                    },
                )
            )
        self._tabla_previa.set_filas(filas)
        self._kpis.actualizar(
            {
                _DIAS: str(sum(r.dias_presente for r in resumen)),
                _TARDE: str(sum(r.dias_tarde for r in resumen)),
                _AUSENTE: str(sum(r.dias_ausente for r in resumen)),
                _JUSTIFICADO: str(sum(_justificados(r) for r in resumen)),
                # Lo que queda a favor de cada uno, ya restados sus atrasos.
                _EXTRA: texto_duracion(
                    sum(max(0, r.tiempo.balance) for r in resumen if r.tiempo is not None)
                )
                + " h",
            }
        )
        cuantos = len(resumen)
        self._titulo_previa.configure(
            text=(
                f"Vista previa · {cuantos} empleado{'s' if cuantos != 1 else ''} · "
                f"{periodos.descripcion(desde, hasta)}"
            )
        )

    # ── Exportar ──────────────────────────────────────────────────────────

    def _on_exportar(self) -> None:
        parametros = self._parametros()
        if parametros is None or parametros[0] > parametros[1]:
            self._mostrar_resultado("Revise las fechas del período.", error=True)
            return
        desde, hasta, empleado_id = parametros
        # Se guarda solo, como Reporte_fecha_hora[_empleado].xlsx: nunca pisa
        # un reporte anterior. "Abrir carpeta" lleva a donde quedó.
        carpeta = str(paths.exports_dir())
        self._btn_exportar.configure(state="disabled", text="Generando…")
        self._progreso.grid(row=9, column=0, sticky="ew", padx=16, pady=(10, 0))
        self._progreso.start()
        piezas.limpiar(self._resultado)
        run_async_ui(
            self,
            work=lambda: self._controller.exportar_asistencia_en_carpeta(
                desde=desde.isoformat(),
                hasta=hasta.isoformat(),
                carpeta=carpeta,
                empleado_id=empleado_id,
            ),
            on_success=self._on_export_ok,
            on_error=self._on_export_err,
        )

    def _fin_export(self) -> None:
        self._progreso.stop()
        self._progreso.grid_remove()
        self._btn_exportar.configure(state="normal", text="Exportar a Excel")

    def _on_export_ok(self, descarga: DescargaReporte) -> None:
        self._fin_export()
        self._mostrar_resultado(
            f"Reporte listo: {os.path.basename(descarga.ruta_archivo)} "
            f"({descarga.filas_exportadas} filas).",
            ruta=descarga.ruta_archivo,
        )
        if self._tab_historial is not None:
            self._cargar_historial()

    def _on_export_err(self, exc: BaseException) -> None:
        self._fin_export()
        self._log.error("Error exportando: %s", exc)
        if isinstance(exc, (ReporteSinDatosError, ReporteIOError, InvalidRangoError)):
            texto = str(exc)
        elif isinstance(exc, InvalidDateError):
            texto = "Revise las fechas del período."
        else:
            texto = "No se pudo generar el reporte. Intente de nuevo."
        self._mostrar_resultado(texto, error=True)

    def _mostrar_resultado(self, texto: str, error: bool = False, ruta: str = "") -> None:
        caja = self._resultado
        piezas.limpiar(caja)
        ctk.CTkLabel(
            caja,
            text=texto,
            fg_color=theme.ERROR_FONDO if error else theme.EXITO_FONDO,
            text_color=theme.ERROR if error else theme.EXITO,
            corner_radius=8,
            anchor="w",
            justify="left",
            wraplength=215,
            font=theme.fuente(theme.TAM_PEQUENO),
            padx=10,
            pady=8,
        ).grid(row=0, column=0, columnspan=2, sticky="ew")
        # En pantallas bajas el resultado queda bajo el borde: bajamos hasta él.
        canvas = getattr(self._card, "_parent_canvas", None)
        if canvas is not None:
            self.after(60, lambda: canvas.yview_moveto(1.0))
        if not ruta:
            return
        caja.grid_columnconfigure((0, 1), weight=1, uniform="abrir")
        piezas.boton_secundario(caja, "Abrir archivo", lambda: self._abrir(ruta), alto=32).grid(
            row=1, column=0, sticky="ew", pady=(8, 0), padx=(0, 4)
        )
        piezas.boton_secundario(
            caja, "Abrir carpeta", lambda: self._abrir(ruta, carpeta=True), alto=32
        ).grid(row=1, column=1, sticky="ew", pady=(8, 0), padx=(4, 0))

    # ── Historial ─────────────────────────────────────────────────────────

    def _cambiar_tab(self, nombre: str) -> None:
        if nombre == _TAB_HISTORIAL:
            self._tab_exportar.grid_remove()
            if self._tab_historial is None:
                self._tab_historial = self._construir_historial()
            self._tab_historial.grid(row=2, column=0, sticky="nsew", padx=28, pady=(16, 20))
            self._cargar_historial()
        else:
            if self._tab_historial is not None:
                self._tab_historial.grid_remove()
            self._tab_exportar.grid()
            self._actualizar_previa()

    def _construir_historial(self) -> ctk.CTkFrame:
        marco = ctk.CTkFrame(self, fg_color="transparent")
        marco.grid_columnconfigure(0, weight=1)
        marco.grid_rowconfigure(0, weight=1)
        self._tabla_historial = DataTable(
            marco,
            _COLUMNAS_HISTORIAL,
            on_seleccion=lambda _iid: self._detalle_historial(),
            on_doble_click=lambda _iid: self._abrir_seleccionado(),
            texto_vacio="Todavía no se ha exportado ningún reporte.",
        )
        self._tabla_historial.grid(row=0, column=0, sticky="nsew")
        self._tabla_historial.color_tag("apagado", theme.TEXTO_SUAVE)
        self._panel_historial = piezas.panel_detalle(marco)
        self._panel_historial.grid(row=0, column=1, sticky="nsew", padx=(14, 0))
        return marco

    def _cargar_historial(self) -> None:
        try:
            self._historial = self._controller.list_historial_descargas(limit=200)
        except Exception:  # noqa: BLE001
            self._log.exception("Error cargando historial")
            self._historial = []
        filas: List[Fila] = []
        for d in self._historial:
            if d.id is None:
                continue
            existe = os.path.exists(d.ruta_archivo)
            filas.append(
                Fila(
                    iid=str(d.id),
                    valores={
                        "fecha": formato.fecha_hora(d.fecha_hora_utc),
                        "periodo": formato.rango_fechas(d.rango_desde, d.rango_hasta),
                        "empleado": self._nombre_filtro(d),
                        "filas": str(d.filas_exportadas),
                    },
                    orden={"fecha": d.fecha_hora_utc, "filas": d.filas_exportadas},
                    tags=() if existe else ("apagado",),
                )
            )
        seleccion = self._tabla_historial.seleccionado()
        self._tabla_historial.set_filas(filas)
        if seleccion is not None:
            self._tabla_historial.seleccionar(seleccion)
        self._detalle_historial()

    def _nombre_filtro(self, d: DescargaReporte) -> str:
        if d.empleado_id_filtro is None:
            return "Todos"
        return self._nombres_por_id.get(d.empleado_id_filtro, f"Empleado #{d.empleado_id_filtro}")

    def _seleccionado(self) -> Optional[DescargaReporte]:
        iid = self._tabla_historial.seleccionado()
        if iid is None:
            return None
        return next((d for d in self._historial if d.id == int(iid)), None)

    def _abrir_seleccionado(self) -> None:
        d = self._seleccionado()
        if d is not None:
            self._abrir(d.ruta_archivo)

    def _abrir(self, ruta: str, carpeta: bool = False) -> None:
        ok = abrir_carpeta_de(ruta) if carpeta else abrir(ruta)
        if not ok:
            messagebox.showwarning(
                "No se pudo abrir",
                f"No se encontró o no se pudo abrir:\n{ruta}",
                parent=self,
            )

    def _detalle_historial(self) -> None:
        panel = self._panel_historial
        piezas.limpiar(panel)
        d = self._seleccionado()
        if d is None:
            piezas.detalle_vacio(panel, "Seleccione un reporte\npara abrirlo o ver su detalle.")
            return
        existe = os.path.exists(d.ruta_archivo)
        ctk.CTkLabel(
            panel,
            text=os.path.basename(d.ruta_archivo),
            font=theme.fuente(theme.TAM_SECCION, "bold"),
            anchor="w",
            justify="left",
            wraplength=280,
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(18, 0))
        insignia = (
            ("Disponible", theme.EXITO, theme.EXITO_FONDO)
            if existe
            else ("Ya no está en esa carpeta", theme.NEUTRO, theme.NEUTRO_FONDO)
        )
        fila = piezas.fila_insignias(panel, 1, [insignia])
        fila = piezas.titulo_seccion(panel, fila, "Acciones", arriba=14)
        motivo = None if existe else "El archivo se movió o se borró."
        fila = piezas.boton_accion(
            panel,
            fila,
            "Abrir archivo",
            lambda: self._abrir(d.ruta_archivo),
            primario=True,
            motivo_deshabilitado=motivo,
        )
        fila = piezas.boton_accion(
            panel,
            fila,
            "Abrir carpeta",
            lambda: self._abrir(d.ruta_archivo, carpeta=True),
            motivo_deshabilitado=motivo,
        )
        fila = piezas.titulo_seccion(panel, fila, "Detalle", arriba=12)
        fila = piezas.bloque_datos(
            panel,
            fila,
            [
                ("Generado", formato.fecha_hora(d.fecha_hora_utc)),
                ("Período", formato.rango_fechas(d.rango_desde, d.rango_hasta)),
                ("Empleado", self._nombre_filtro(d)),
                ("Filas", str(d.filas_exportadas)),
            ],
        )
        piezas.texto_suave(panel, fila + 1, d.ruta_archivo)
