"""Vista de Sincronización del reloj ZKTeco.

    Sincronización del reloj
    Descargue las marcas del biométrico; la asistencia se calcula sola.
    ┌──────────────────┬─────────────────┬──────────────────┐
    │ Hoy 16:27        │ 211             │ 0                │   ← resumen
    │ Última correcta  │ Marcas (última) │ Fallidas recient.│
    └──────────────────┴─────────────────┴──────────────────┘
    ┌ Nueva sincronización ┐ ┌ Historial ─────────────────────────────┐
    │ Reloj [ … ▾ ]        │ │ FECHA  RELOJ  PERÍODO  MARCAS  ESTADO   │
    │ [Hoy|7 días|Última]  │ │ …                                      │
    │ Desde [..] Hasta [..]│ │                                        │
    │ [ Sincronizar ahora ]│ │                                        │
    │ resultado / error    │ └────────────────────────────────────────┘
    └──────────────────────┘

Threading: ``controller.ejecutar_sincronizacion`` puede tardar varios
segundos (pull TCP + consolidación); se envuelve en ``run_async_ui`` para
que la ventana siga respondiendo. El historial sale de la BD, así que se
ve también lo que se sincronizó en sesiones anteriores.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from tkinter import messagebox
from typing import Callable, Dict, List, Optional, Set

import customtkinter as ctk

from core.models.dispositivo import Dispositivo
from core.models.sincronizacion import EstadoSincronizacion, Sincronizacion
from core.services.errors import (
    DispositivoInactiveError,
    DispositivoNotFoundError,
    InvalidDateError,
    InvalidRangoError,
)
from core.services.sincronizacion_result import ResultadoSincronizacion
from infrastructure.zkteco.exceptions import (
    ZKAdapterError,
    ZKConnectionError,
    ZKProtocolError,
    ZKTimeoutError,
)
from ui import formato, theme
from ui.async_util import run_async_ui
from ui.components import piezas
from ui.components.data_table import Columna, DataTable, Fila
from ui.components.usuarios_format import hace
from ui.controllers.sincronizacion_controller import SincronizacionController

_RANGO_HOY = "Hoy"
_RANGO_SEMANA = "Últimos 7 días"
_RANGO_ULTIMA = "Desde la última"

_ESTADO_TEXTO = {
    EstadoSincronizacion.OK.value: "Correcta",
    EstadoSincronizacion.FALLIDA.value: "Fallida",
    EstadoSincronizacion.EN_CURSO.value: "En curso",
}

_COLUMNAS = (
    Columna("fecha", "Fecha", ancho=135),
    Columna("reloj", "Reloj", ancho=150),
    Columna("periodo", "Período", ancho=150),
    Columna("marcas", "Marcas", ancho=70, alinear="center"),
    Columna("estado", "Estado", ancho=85),
    Columna("duracion", "Duración", ancho=75),
)

_HISTORIAL_LIMITE = 50
_SIN_RELOJES = "(no hay relojes activos)"


def _label_dispositivo(d: Dispositivo) -> str:
    return f"{d.nombre}  ·  {d.ip}"


class SincronizacionView(ctk.CTkFrame):
    """Vista raíz del módulo Sincronización ZKTeco."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        controller: SincronizacionController,
        on_sync_completada: Optional[Callable[[ResultadoSincronizacion], None]] = None,
    ) -> None:
        """Construye la vista.

        Args:
            master: Contenedor Tk donde se monta.
            controller: Controller ya inyectado con session + servicios.
            on_sync_completada: Callback opcional tras una sincronización
                exitosa; el composition root lo usa para refrescar la
                barra de estado global.
        """
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self._controller = controller
        self._on_sync_completada = on_sync_completada
        self._log = logging.getLogger(self.__class__.__name__)
        self._dispositivos: Dict[str, Dispositivo] = {}
        self._historial: List[Sincronizacion] = []
        self._nombres_relojes: Dict[int, str] = {}
        self._relojes_push: Set[int] = set()
        self._ocupado = False
        self._habia_activos = False

        self._construir_ui()
        self._cargar_dispositivos()
        self._cargar_historial()
        self._pintar_aviso_recuperacion()

    # ── Construcción ──────────────────────────────────────────────────────

    def _construir_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)

        cabecera, _ = piezas.encabezado(
            self,
            "Sincronización del reloj",
            "Descargue las marcas del biométrico; la asistencia se calcula sola al terminar.",
        )
        cabecera.grid(row=0, column=0, sticky="ew", padx=28, pady=(24, 18))

        self._banner = ctk.CTkLabel(
            self,
            text="",
            fg_color=theme.ALERTA_FONDO,
            text_color=theme.ALERTA,
            corner_radius=8,
            anchor="w",
            justify="left",
            font=theme.fuente(),
            wraplength=900,
            padx=14,
            pady=8,
        )
        self._aviso = piezas.Aviso(self, row=2, column=0, sticky="ew", padx=28, pady=(0, 12))

        self._kpis = piezas.FilaKpis(
            self,
            (
                ("ultima", "Última sincronización correcta", theme.EXITO, False),
                ("marcas", "Marcas en esa sincronización", theme.PRIMARIO, False),
                ("fallidas", f"Fallidas (últimas {_HISTORIAL_LIMITE})", theme.ERROR, False),
            ),
        )
        self._kpis.grid(row=3, column=0, sticky="ew", padx=28, pady=(0, 14))

        cuerpo = ctk.CTkFrame(self, fg_color="transparent")
        cuerpo.grid(row=5, column=0, sticky="nsew", padx=28, pady=(0, 20))
        cuerpo.grid_columnconfigure(1, weight=1)
        cuerpo.grid_rowconfigure(0, weight=1)

        self._construir_formulario(cuerpo).grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        self._construir_historial(cuerpo).grid(row=0, column=1, sticky="nsew")

    def _construir_formulario(self, master: ctk.CTkFrame) -> ctk.CTkScrollableFrame:
        card = piezas.panel_detalle(master, ancho=330)
        self._card = card
        negrita = theme.fuente(theme.TAM_PEQUENO, "bold")

        ctk.CTkLabel(
            card, text="Nueva sincronización", font=theme.fuente(theme.TAM_SECCION, "bold")
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(18, 12))

        ctk.CTkLabel(card, text="Reloj", font=negrita).grid(
            row=1, column=0, sticky="w", padx=16, pady=(0, 4)
        )
        self._menu_reloj = piezas.menu_filtro(
            card, [_SIN_RELOJES], self._on_reloj_cambiado, ancho=290
        )
        self._menu_reloj.grid(row=2, column=0, sticky="ew", padx=16)

        ctk.CTkLabel(card, text="Período", font=negrita).grid(
            row=3, column=0, sticky="w", padx=16, pady=(14, 4)
        )
        self._rangos = ctk.CTkSegmentedButton(
            card,
            values=[_RANGO_HOY, _RANGO_SEMANA, _RANGO_ULTIMA],
            command=self._on_rango_rapido,
            height=32,
            font=theme.fuente(theme.TAM_PEQUENO),
            selected_color=theme.TARJETA,
            selected_hover_color=theme.TARJETA,
            unselected_color=theme.SECUNDARIO,
            unselected_hover_color=theme.SECUNDARIO_HOVER,
            fg_color=theme.SECUNDARIO,
            text_color=theme.TEXTO,
        )
        self._rangos.grid(row=4, column=0, sticky="ew", padx=16)

        fechas = ctk.CTkFrame(card, fg_color="transparent")
        fechas.grid(row=5, column=0, sticky="ew", padx=16, pady=(10, 0))
        fechas.grid_columnconfigure((0, 1), weight=1, uniform="fecha")
        for col, texto in enumerate(("Desde", "Hasta")):
            ctk.CTkLabel(
                fechas,
                text=texto,
                font=theme.fuente(theme.TAM_PEQUENO),
                text_color=theme.TEXTO_SUAVE,
            ).grid(row=0, column=col, sticky="w", padx=(0 if col == 0 else 6, 0))
        self._desde = piezas.selector_fecha(fechas, date.today())
        self._desde.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        self._hasta = piezas.selector_fecha(fechas, date.today())
        self._hasta.grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=(2, 0))
        for picker in (self._desde, self._hasta):
            picker.bind("<<DateEntrySelected>>", lambda _e: self._rangos.set(""))

        self._btn_sync = piezas.boton_primario(
            card, "Sincronizar ahora", self._on_sincronizar, alto=40
        )
        self._btn_sync.grid(row=6, column=0, sticky="ew", padx=16, pady=(16, 0))

        self._progreso = ctk.CTkProgressBar(
            card, mode="indeterminate", height=6, progress_color=theme.PRIMARIO
        )
        self._estado = ctk.CTkLabel(
            card,
            text="",
            font=theme.fuente(theme.TAM_PEQUENO),
            text_color=theme.TEXTO_SUAVE,
            anchor="w",
        )

        self._resultado = ctk.CTkFrame(card, fg_color="transparent")
        self._resultado.grid(row=9, column=0, sticky="ew", padx=16, pady=(16, 16))
        self._resultado.grid_columnconfigure(0, weight=1)
        return card

    def _construir_historial(self, master: ctk.CTkFrame) -> ctk.CTkFrame:
        marco = ctk.CTkFrame(master, fg_color="transparent")
        marco.grid_columnconfigure(0, weight=1)
        marco.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(
            marco,
            text="Historial",
            font=theme.fuente(theme.TAM_SECCION, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))
        self._tabla = DataTable(
            marco,
            _COLUMNAS,
            on_seleccion=lambda _iid: self._mostrar_detalle_historial(),
            texto_vacio="Todavía no se ha sincronizado ningún reloj.",
        )
        self._tabla.grid(row=1, column=0, sticky="nsew")
        self._tabla.color_tag("fallida", theme.ERROR)
        self._tabla.color_tag("en_curso", theme.ALERTA)
        self._detalle_historial = ctk.CTkLabel(
            marco,
            text="",
            font=theme.fuente(theme.TAM_PEQUENO),
            text_color=theme.TEXTO_SUAVE,
            anchor="w",
            justify="left",
            wraplength=640,
        )
        self._detalle_historial.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        return marco

    # ── Carga ─────────────────────────────────────────────────────────────

    def al_mostrar(self) -> None:
        """Al volver al módulo (la vista queda guardada): datos al día.

        Si los relojes son los mismos se conserva lo elegido (reloj y
        fechas); si cambiaron en Configuración, el formulario se rearma.
        """
        if not self._ocupado:
            self._cargar_dispositivos(conservar=True)
        self._cargar_historial()

    def _cargar_dispositivos(self, conservar: bool = False) -> None:
        try:
            activos = self._controller.list_dispositivos_activos()
        except Exception:  # noqa: BLE001 — último recurso, log + aviso
            self._log.exception("Error listando dispositivos")
            messagebox.showerror("Error", "No se pudo cargar la lista de relojes.")
            return
        # Los relojes push envían las marcas solos: no se sincronizan a mano.
        dispositivos = [d for d in activos if not d.es_push]
        if conservar:
            nuevos = {_label_dispositivo(d): d for d in dispositivos}
            if list(nuevos) == list(self._dispositivos) and bool(activos) == self._habia_activos:
                self._dispositivos = nuevos
                if self._rangos.get() == _RANGO_ULTIMA:
                    self._on_rango_rapido(_RANGO_ULTIMA)
                return
            piezas.limpiar(self._resultado)  # el aviso o resultado anterior ya no aplica
        self._habia_activos = bool(activos)
        if not dispositivos:
            self._menu_reloj.configure(values=[_SIN_RELOJES])
            self._menu_reloj.set(_SIN_RELOJES)
            self._btn_sync.configure(state="disabled")
            if activos:
                self._mostrar_mensaje(
                    "Sus relojes envían las marcas solos (modo push): no hace falta "
                    "sincronizar. Las marcas recibidas aparecen en el historial.",
                    theme.EXITO,
                    theme.EXITO_FONDO,
                )
            else:
                self._mostrar_mensaje(
                    "Registre un reloj en Configuración → Relojes para poder sincronizar.",
                    theme.ALERTA,
                    theme.ALERTA_FONDO,
                )
            return
        self._dispositivos = {_label_dispositivo(d): d for d in dispositivos}
        labels = list(self._dispositivos)
        self._menu_reloj.configure(values=labels)
        # Al volver al módulo se mantiene el reloj elegido si sigue existiendo.
        elegido = self._menu_reloj.get() if conservar else ""
        elegido = elegido if elegido in labels else labels[0]
        self._menu_reloj.set(elegido)
        self._btn_sync.configure(state="normal")
        self._on_reloj_cambiado(elegido)

    def _cargar_historial(self) -> None:
        try:
            self._historial = self._controller.historial(limit=_HISTORIAL_LIMITE)
            self._nombres_relojes = self._controller.nombres_dispositivos()
            self._relojes_push = self._controller.relojes_push()
        except Exception:  # noqa: BLE001
            self._log.exception("Error cargando historial de sincronizaciones")
            self._historial = []
        filas: List[Fila] = []
        for s in self._historial:
            if s.id is None:
                continue
            tag = {
                EstadoSincronizacion.FALLIDA.value: ("fallida",),
                EstadoSincronizacion.EN_CURSO.value: ("en_curso",),
            }.get(s.estado, ())
            filas.append(
                Fila(
                    iid=str(s.id),
                    valores={
                        "fecha": formato.fecha_hora(s.inicio),
                        "reloj": self._nombres_relojes.get(s.dispositivo_id, "—"),
                        "periodo": _periodo(s.rango_desde, s.rango_hasta),
                        "marcas": (
                            str(s.registros_recibidos)
                            if s.estado == EstadoSincronizacion.OK.value
                            else "—"
                        ),
                        "estado": _ESTADO_TEXTO.get(s.estado, s.estado),
                        "duracion": (
                            "Push"
                            if s.dispositivo_id in self._relojes_push
                            else formato.segundos_entre(s.inicio, s.fin)
                        ),
                    },
                    orden={"fecha": s.inicio, "marcas": s.registros_recibidos},
                    tags=tag,
                )
            )
        self._tabla.set_filas(filas)
        self._actualizar_kpis()
        self._mostrar_detalle_historial()

    def _actualizar_kpis(self) -> None:
        ahora = datetime.now(timezone.utc)
        ultima_ok = next(
            (s for s in self._historial if s.estado == EstadoSincronizacion.OK.value), None
        )
        fallidas = sum(1 for s in self._historial if s.estado == EstadoSincronizacion.FALLIDA.value)
        self._kpis.actualizar(
            {
                "ultima": hace(ultima_ok.fin or ultima_ok.inicio, ahora) if ultima_ok else "Nunca",
                "marcas": str(ultima_ok.registros_recibidos) if ultima_ok else "—",
                "fallidas": str(fallidas),
            }
        )

    def _pintar_aviso_recuperacion(self) -> None:
        cantidad = self._controller.consumir_aviso_recuperacion()
        if not cantidad:
            return
        if cantidad == 1:
            texto = "Se cerró 1 sincronización que quedó interrumpida"
        else:
            texto = f"Se cerraron {cantidad} sincronizaciones que quedaron interrumpidas"
        self._banner.configure(
            text=(
                f"{texto} porque la aplicación se cerró a medias. Puede repetirla sin "
                "riesgo: las marcas no se duplican."
            )
        )
        self._banner.grid(row=1, column=0, sticky="ew", padx=28, pady=(0, 12))

    # ── Período ───────────────────────────────────────────────────────────

    def _reloj_actual(self) -> Optional[Dispositivo]:
        return self._dispositivos.get(self._menu_reloj.get())

    def _on_reloj_cambiado(self, _label: str) -> None:
        self._rangos.set(_RANGO_ULTIMA)
        self._on_rango_rapido(_RANGO_ULTIMA)

    def _on_rango_rapido(self, opcion: str) -> None:
        hoy = date.today()
        if opcion == _RANGO_HOY:
            self._poner_rango(hoy, hoy)
        elif opcion == _RANGO_SEMANA:
            self._poner_rango(hoy - timedelta(days=6), hoy)
        elif opcion == _RANGO_ULTIMA:
            reloj = self._reloj_actual()
            if reloj is None or reloj.id is None:
                return
            try:
                desde_iso, hasta_iso = self._controller.calcular_rango_default(reloj.id)
            except Exception:  # noqa: BLE001
                self._log.exception("Error calculando rango por defecto reloj=%s", reloj.id)
                return
            # ``set_date`` necesita ``date``: un str rompe en el .exe (ver Sub-2.4b).
            self._poner_rango(date.fromisoformat(desde_iso), date.fromisoformat(hasta_iso))

    def _poner_rango(self, desde: date, hasta: date) -> None:
        self._desde.set_date(desde)
        self._hasta.set_date(hasta)

    # ── Sincronizar ───────────────────────────────────────────────────────

    def _on_sincronizar(self) -> None:
        reloj = self._reloj_actual()
        if reloj is None or reloj.id is None:
            return
        reloj_id = reloj.id
        desde = self._desde.get_date()
        hasta = self._hasta.get_date()
        if desde > hasta:
            self._mostrar_mensaje(
                "La fecha «Desde» no puede ser posterior a «Hasta».", theme.ERROR, theme.ERROR_FONDO
            )
            return
        self._bloquear(True, f"Descargando marcas de {reloj.nombre}…")
        run_async_ui(
            self,
            work=lambda: self._controller.ejecutar_sincronizacion(
                dispositivo_id=reloj_id,
                rango_desde=desde.isoformat(),
                rango_hasta=hasta.isoformat(),
            ),
            on_success=self._on_sync_ok,
            on_error=self._on_sync_err,
        )

    def _bloquear(self, ocupado: bool, texto: str = "") -> None:
        self._ocupado = ocupado
        if ocupado:
            self._btn_sync.configure(state="disabled", text="Sincronizando…")
            self._progreso.grid(row=7, column=0, sticky="ew", padx=16, pady=(12, 0))
            self._progreso.start()
            self._estado.configure(text=texto)
            self._estado.grid(row=8, column=0, sticky="w", padx=16, pady=(4, 0))
            piezas.limpiar(self._resultado)
        else:
            self._progreso.stop()
            self._progreso.grid_remove()
            self._estado.grid_remove()
            self._btn_sync.configure(state="normal", text="Sincronizar ahora")

    def _on_sync_ok(self, resultado: ResultadoSincronizacion) -> None:
        self._bloquear(False)
        self._pintar_resultado(resultado)
        self._cargar_historial()
        self._tabla.seleccionar(str(resultado.sincronizacion_id))
        self._aviso.mostrar(
            f"Sincronización completada: {resultado.registros_recibidos} marcas nuevas."
        )
        if self._on_sync_completada is not None:
            try:
                self._on_sync_completada(resultado)
            except Exception:  # noqa: BLE001 — el callback es del composition root
                self._log.exception("Error invocando on_sync_completada")

    def _on_sync_err(self, exc: BaseException) -> None:
        self._bloquear(False)
        self._log.error("Error en sincronización: %s", exc, exc_info=exc)
        self._mostrar_mensaje(
            _traducir_error(exc), theme.ERROR, theme.ERROR_FONDO, "No se pudo sincronizar"
        )
        self._cargar_historial()

    # ── Resultado ─────────────────────────────────────────────────────────

    def _bajar_hasta_resultado(self) -> None:
        """En pantallas bajas el resultado queda bajo el borde: desplazamos hasta él."""
        canvas = getattr(self._card, "_parent_canvas", None)
        if canvas is not None:
            self.after(50, lambda: canvas.yview_moveto(1.0))

    def _mostrar_mensaje(
        self, texto: str, color: theme.Color, fondo: theme.Color, titulo: str = ""
    ) -> None:
        self._bajar_hasta_resultado()
        piezas.limpiar(self._resultado)
        caja = ctk.CTkFrame(self._resultado, fg_color=fondo, corner_radius=8)
        caja.grid(row=0, column=0, sticky="ew")
        caja.grid_columnconfigure(0, weight=1)
        if titulo:
            ctk.CTkLabel(
                caja, text=titulo, text_color=color, font=theme.fuente(theme.TAM_NORMAL, "bold")
            ).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 0))
        ctk.CTkLabel(
            caja,
            text=texto,
            text_color=color,
            font=theme.fuente(theme.TAM_PEQUENO),
            anchor="w",
            justify="left",
            wraplength=260,
        ).grid(row=1, column=0, sticky="w", padx=12, pady=(4 if titulo else 10, 10))

    def _pintar_resultado(self, r: ResultadoSincronizacion) -> None:
        self._bajar_hasta_resultado()
        piezas.limpiar(self._resultado)
        ctk.CTkLabel(
            self._resultado,
            text="RESULTADO",
            font=theme.fuente(theme.TAM_PEQUENO - 1, "bold"),
            text_color=theme.TEXTO_SUAVE,
        ).grid(row=0, column=0, sticky="w", pady=(0, 6))

        cons = r.consolidacion
        numeros = [("Marcas nuevas", r.registros_recibidos)]
        if cons is not None:
            numeros += [
                ("Asistencias", cons.asistencias_upsertadas),
                ("Empleados", cons.empleados_procesados),
            ]
        grilla = ctk.CTkFrame(self._resultado, fg_color=theme.TARJETA_ALT, corner_radius=8)
        grilla.grid(row=1, column=0, sticky="ew")
        for col, (texto, valor) in enumerate(numeros):
            grilla.grid_columnconfigure(col, weight=1, uniform="res")
            ctk.CTkLabel(grilla, text=str(valor), font=theme.fuente(20, "bold")).grid(
                row=0, column=col, pady=(10, 0)
            )
            ctk.CTkLabel(
                grilla,
                text=texto,
                font=theme.fuente(theme.TAM_PEQUENO - 1),
                text_color=theme.TEXTO_SUAVE,
            ).grid(row=1, column=col, pady=(0, 10))

        if r.error_consolidacion is not None:
            caja = ctk.CTkFrame(self._resultado, fg_color="transparent")
            caja.grid(row=2, column=0, sticky="ew", pady=(10, 0))
            ctk.CTkLabel(
                caja,
                text=(
                    "Las marcas se guardaron, pero el cálculo de asistencia falló: "
                    f"{r.error_consolidacion}. Puede recalcularla desde Asistencia."
                ),
                text_color=theme.ERROR,
                font=theme.fuente(theme.TAM_PEQUENO),
                anchor="w",
                justify="left",
                wraplength=280,
            ).grid(row=0, column=0, sticky="w")
            return

        if cons is not None and cons.marcadas_desconocidas:
            total = sum(m.cantidad_marcadas for m in cons.marcadas_desconocidas)
            ids = ", ".join(
                f"{m.zkteco_user_id} ({m.cantidad_marcadas})" for m in cons.marcadas_desconocidas
            )
            caja = ctk.CTkFrame(self._resultado, fg_color=theme.ALERTA_FONDO, corner_radius=8)
            caja.grid(row=2, column=0, sticky="ew", pady=(10, 0))
            ctk.CTkLabel(
                caja,
                text=f"{total} marca(s) de IDs sin empleado",
                text_color=theme.ALERTA,
                font=theme.fuente(theme.TAM_PEQUENO, "bold"),
            ).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 0))
            ctk.CTkLabel(
                caja,
                text=(
                    f"ID del reloj (marcas): {ids}.\nAsigne ese ID al empleado en "
                    "Empleados → Editar datos y vuelva a sincronizar."
                ),
                text_color=theme.ALERTA,
                font=theme.fuente(theme.TAM_PEQUENO - 1),
                anchor="w",
                justify="left",
                wraplength=270,
            ).grid(row=1, column=0, sticky="w", padx=12, pady=(2, 10))

    def _mostrar_detalle_historial(self) -> None:
        iid = self._tabla.seleccionado()
        sync = next((s for s in self._historial if iid is not None and s.id == int(iid)), None)
        if sync is None:
            self._detalle_historial.configure(
                text="Seleccione una fila para ver el detalle.", text_color=theme.TEXTO_SUAVE
            )
            return
        reloj = self._nombres_relojes.get(sync.dispositivo_id, "—")
        periodo = f"del {formato.fecha(sync.rango_desde)} al {formato.fecha(sync.rango_hasta)}"
        if sync.estado == EstadoSincronizacion.FALLIDA.value:
            self._detalle_historial.configure(
                text=f"{reloj}, {periodo}: falló — {sync.error_mensaje or 'sin detalle'}",
                text_color=theme.ERROR,
            )
        elif sync.estado == EstadoSincronizacion.EN_CURSO.value:
            self._detalle_historial.configure(
                text=f"{reloj}, {periodo}: en curso…", text_color=theme.ALERTA
            )
        else:
            self._detalle_historial.configure(
                text=(
                    f"{reloj}, {periodo}: {sync.registros_recibidos} marcas nuevas, "
                    f"en {formato.segundos_entre(sync.inicio, sync.fin)}."
                ),
                text_color=theme.TEXTO_SUAVE,
            )


def _periodo(desde: str, hasta: str) -> str:
    if desde == hasta:
        return formato.fecha(desde)
    if desde[:4] == hasta[:4]:
        return f"{formato.fecha(desde)[:5]} – {formato.fecha(hasta)}"
    return f"{formato.fecha(desde)} – {formato.fecha(hasta)}"


def _traducir_error(exc: BaseException) -> str:
    """Convierte una excepción conocida en un mensaje claro para el operador."""
    if isinstance(exc, ZKConnectionError):
        return (
            "No se pudo conectar con el reloj. Verifique que esté encendido, conectado "
            "a la red y que la IP y el puerto sean correctos (Configuración → Dispositivos)."
        )
    if isinstance(exc, ZKTimeoutError):
        return (
            "El reloj dejó de responder. Intente de nuevo; si persiste, revise la red "
            "o reinicie el reloj."
        )
    if isinstance(exc, ZKProtocolError):
        return "El reloj respondió con un formato inesperado. Verifique la versión del firmware."
    if isinstance(exc, ZKAdapterError):
        return f"Error del reloj: {exc}"
    if isinstance(
        exc,
        (DispositivoNotFoundError, DispositivoInactiveError, InvalidRangoError, InvalidDateError),
    ):
        return str(exc)
    return "Ocurrió un error inesperado. Consulte el registro de la aplicación."
