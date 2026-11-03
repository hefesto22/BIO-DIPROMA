"""Pestaña "Feriados" de Configuración.

Días no laborables para todos: la asistencia muestra «Feriado» y no se
descuentan de las vacaciones. Lista por año, con un botón para cargar de
una vez los feriados nacionales de fecha fija (art. 339 del Código del
Trabajo); los que se trasladan por decreto se agregan a mano.
"""

from __future__ import annotations

from datetime import date
from tkinter import messagebox
from typing import Callable, Dict, List, Optional, Tuple

import customtkinter as ctk

from core.models.feriado import Feriado
from core.services.errors import FeriadoError, InvalidDateError, MissingRequiredFieldError
from ui import formato, theme
from ui.components import piezas
from ui.components.config_dialogos import FeriadoDialog
from ui.components.data_table import Columna, Fila
from ui.components.lista_detalle import ListaDetalle
from ui.controllers.configuracion_controller import ConfiguracionController

_TOTAL = "TOTAL"
_PROXIMOS = "PROXIMOS"
_PASADOS = "PASADOS"

_COLUMNAS = (
    Columna("fecha", "Fecha", ancho=110),
    Columna("dia", "Día", ancho=100),
    Columna("descripcion", "Feriado", ancho=300),
    Columna("estado", "Estado", ancho=110),
)

_ERRORES = (FeriadoError, InvalidDateError, MissingRequiredFieldError)


class FeriadosTab(ListaDetalle):
    texto_sin_seleccion = "Seleccione un feriado\npara ver su detalle y acciones."

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        controller: ConfiguracionController,
        hoy: Callable[[], date] = date.today,
    ) -> None:
        self._c = controller
        self._hoy = hoy
        self._anio = hoy().year
        self._feriados: List[Feriado] = []
        super().__init__(
            master,
            _COLUMNAS,
            (
                (_TOTAL, f"Feriados en {self._anio}", theme.PRIMARIO, False),
                (_PROXIMOS, "Próximos", theme.EXITO, True),
                (_PASADOS, "Ya pasaron", theme.NEUTRO, True),
            ),
            placeholder_busqueda="Buscar feriado…",
            texto_vacio="No hay feriados registrados en este año.",
            filtro_alternable=True,
        )
        anios = [str(a) for a in range(self._anio - 1, self._anio + 3)]
        self._menu_anio = piezas.menu_filtro(self.barra, anios, self._cambiar_anio, ancho=110)
        self._menu_anio.set(str(self._anio))
        self._menu_anio.grid(row=0, column=1, padx=(10, 0))
        self._btn_cargar = piezas.boton_secundario(
            self.barra, "", self._cargar_nacionales, ancho=230
        )
        self._btn_cargar.grid(row=0, column=2, padx=(10, 0))
        self.refrescar()

    # ── Datos ─────────────────────────────────────────────────────────────

    def _cambiar_anio(self, valor: str) -> None:
        self._anio = int(valor)
        self.filtro = None
        self.kpis.etiqueta(_TOTAL, f"Feriados en {self._anio}")
        self.refrescar()

    def _cargar(self) -> None:
        self._feriados = self._c.list_feriados(self._anio)
        self._btn_cargar.configure(text=f"Cargar feriados nacionales {self._anio}")

    def _estado(self, f: Feriado) -> str:
        hoy = self._hoy().isoformat()
        if f.fecha == hoy:
            return "Hoy"
        return "Próximo" if f.fecha > hoy else "Pasado"

    def _filas(self) -> Tuple[List[Fila], Dict[str, str]]:
        texto = self.texto_busqueda()
        hoy = self._hoy().isoformat()
        proximos = [f for f in self._feriados if f.fecha >= hoy]
        filas: List[Fila] = []
        for f in self._feriados:
            if f.id is None:
                continue
            pasado = f.fecha < hoy
            if self.filtro == _PROXIMOS and pasado or self.filtro == _PASADOS and not pasado:
                continue
            if texto and texto not in f.descripcion.casefold():
                continue
            dia = date.fromisoformat(f.fecha)
            filas.append(
                Fila(
                    iid=str(f.id),
                    valores={
                        "fecha": formato.fecha(f.fecha),
                        "dia": formato.DIAS[dia.weekday()][2],
                        "descripcion": f.descripcion,
                        "estado": self._estado(f),
                    },
                    orden={"fecha": f.fecha, "dia": dia.weekday(), "estado": f.fecha},
                    tags=("apagado",) if pasado else (),
                )
            )
        return filas, {
            _TOTAL: str(len(self._feriados)),
            _PROXIMOS: str(len(proximos)),
            _PASADOS: str(len(self._feriados) - len(proximos)),
        }

    def _buscar(self, iid: str) -> Optional[Feriado]:
        return next((f for f in self._feriados if f.id == int(iid)), None)

    # ── Panel ─────────────────────────────────────────────────────────────

    def _detalle(self, panel: ctk.CTkScrollableFrame, iid: str) -> None:
        f = self._buscar(iid)
        if f is None:
            return
        dia = date.fromisoformat(f.fecha)
        ctk.CTkLabel(
            panel,
            text=f.descripcion,
            font=theme.fuente(theme.TAM_SECCION + 2, "bold"),
            anchor="w",
            justify="left",
            wraplength=280,
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(18, 0))
        ctk.CTkLabel(
            panel,
            text=f"{dia.day} de {formato.fecha_larga(f.fecha).split(' de ')[1]}",
            font=theme.fuente(24, "bold"),
            text_color=theme.PRIMARIO,
            anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=16, pady=(6, 0))
        ctk.CTkLabel(
            panel,
            text=formato.fecha_larga(f.fecha).capitalize(),
            font=theme.fuente(theme.TAM_PEQUENO),
            text_color=theme.TEXTO_SUAVE,
            anchor="w",
        ).grid(row=2, column=0, sticky="w", padx=16)
        faltan = (dia - self._hoy()).days
        if faltan > 0:
            insignia = (f"Faltan {formato.dias_cantidad(faltan)}", theme.EXITO, theme.EXITO_FONDO)
        elif faltan == 0:
            insignia = ("Es hoy", theme.PRIMARIO, theme.PRIMARIO_SUAVE)
        else:
            insignia = ("Ya pasó", theme.NEUTRO, theme.NEUTRO_FONDO)
        fila = piezas.fila_insignias(panel, 3, [insignia])

        fila = piezas.titulo_seccion(panel, fila, "Acciones", arriba=14)
        fila = piezas.boton_accion(panel, fila, "Editar", lambda: self._editar(f), primario=True)
        fila = piezas.boton_accion(panel, fila, "Eliminar", lambda: self._eliminar(f), peligro=True)
        piezas.texto_suave(
            panel,
            fila + 1,
            "Ese día la asistencia de todos muestra «Feriado» en vez de «Ausente», y no se "
            "descuenta de las vacaciones.",
        )

    # ── Acciones ──────────────────────────────────────────────────────────

    def _doble_click(self, iid: str) -> None:
        f = self._buscar(iid)
        if f is not None:
            self._editar(f)

    def nuevo(self) -> None:
        creados: List[Feriado] = []

        def submit(fecha: str, descripcion: str) -> Optional[str]:
            try:
                creados.append(self._c.create_feriado(fecha, descripcion))
            except _ERRORES as exc:
                return str(exc)
            return None

        hoy = self._hoy()
        inicial = hoy if hoy.year == self._anio else date(self._anio, 1, 1)
        self.wait_window(
            FeriadoDialog(
                self,
                titulo="Nuevo feriado",
                on_submit=submit,
                inicial_fecha=inicial,
                texto_ok="Agregar feriado",
                permitir_varios=True,
            )
        )
        if not creados:
            return
        self._ir_al_anio_de(creados[-1])
        if len(creados) == 1:
            f = creados[0]
            self.aviso.mostrar(f"Feriado agregado: {f.descripcion} ({formato.fecha(f.fecha)}).")
        else:
            self.aviso.mostrar(f"{len(creados)} feriados agregados.")

    def _suprimir(self, iid: str) -> None:
        f = self._buscar(iid)
        if f is not None:
            self._eliminar(f)

    def _editar(self, f: Feriado) -> None:
        assert f.id is not None
        feriado_id = f.id
        guardado: Dict[str, Feriado] = {}

        def submit(fecha: str, descripcion: str) -> Optional[str]:
            try:
                guardado["f"] = self._c.update_feriado(feriado_id, fecha, descripcion)
            except _ERRORES as exc:
                return str(exc)
            return None

        self.wait_window(
            FeriadoDialog(
                self,
                titulo="Editar feriado",
                on_submit=submit,
                inicial_fecha=date.fromisoformat(f.fecha),
                inicial_descripcion=f.descripcion,
            )
        )
        if "f" in guardado:
            self._ir_al_anio_de(guardado["f"])
            self.aviso.mostrar("Cambios guardados.")

    def _ir_al_anio_de(self, f: Feriado) -> None:
        anio = int(f.fecha[:4])
        if anio != self._anio:
            self._anio = anio
            self._menu_anio.set(str(anio))
            self.kpis.etiqueta(_TOTAL, f"Feriados en {anio}")
        self.filtro = None
        self.refrescar(seleccionar=str(f.id))

    def _eliminar(self, f: Feriado) -> None:
        assert f.id is not None
        if not messagebox.askyesno(
            "Eliminar feriado",
            f"¿Eliminar «{f.descripcion}» ({formato.fecha(f.fecha)})?\n\n"
            "Si ese día ya pasó, la asistencia se vuelve a calcular con las marcas del reloj.",
            parent=self,
        ):
            return
        try:
            self._c.delete_feriado(f.id)
        except _ERRORES as exc:
            self.aviso.mostrar(str(exc), error=True)
            return
        self.refrescar()
        self.aviso.mostrar(f"Se eliminó «{f.descripcion}».")

    def _cargar_nacionales(self) -> None:
        if not messagebox.askyesno(
            "Feriados nacionales",
            f"Se agregarán los feriados de fecha fija de {self._anio} (art. 339 del Código "
            "del Trabajo): Año Nuevo, Jueves, Viernes y Sábado Santo, 1 de mayo, 15 de "
            "septiembre y 25 de diciembre. Los que ya estén registrados no se repiten.\n\n"
            "El 14 de abril y la Semana Morazánica cambian de fecha cada año por decreto: "
            "agréguelos con «+ Nuevo feriado».\n\n¿Continuar?",
            parent=self,
        ):
            return
        try:
            agregados = self._c.cargar_feriados_nacionales(self._anio)
        except _ERRORES as exc:
            self.aviso.mostrar(str(exc), error=True)
            return
        self.filtro = None
        self.refrescar()
        if agregados:
            n = len(agregados)
            self.aviso.mostrar(
                f"Se agregaron {n} feriado{'s' if n != 1 else ''} de {self._anio}. "
                "Faltan el 14 de abril y la Semana Morazánica."
            )
        else:
            self.aviso.mostrar(f"Los feriados nacionales de {self._anio} ya estaban registrados.")
