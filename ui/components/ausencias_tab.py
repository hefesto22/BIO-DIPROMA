"""Pestaña "Vacaciones y permisos" del módulo Turnos.

    ┌────────────┬───────────┬─────────────┬──────────────────────┐
    │ Fuera hoy  │ Próximas  │ Finalizadas │ Días de vacaciones…  │  ← las tres primeras filtran
    └────────────┴───────────┴─────────────┴──────────────────────┘
    [Buscar empleado…]  [Todos los tipos ▾]
    ┌──────────────────────────────────────────────┐ ┌──────────────┐
    │ EMPLEADO  TIPO  DESDE  HASTA  DÍAS  ESTADO    │ │ detalle y    │
    │ …                                             │ │ acciones     │
    └──────────────────────────────────────────────┘ └──────────────┘

Los permisos los valida el controller (``MANAGE_EMPLOYEES``).
"""

from __future__ import annotations

import logging
from datetime import date
from tkinter import messagebox
from typing import Callable, Dict, List, Optional

import customtkinter as ctk

from core.models.ausencia import TIPO_AUSENCIA_TEXTO, Ausencia, TipoAusencia
from core.models.empleado import Empleado
from core.services.errors import (
    AusenciaError,
    AusenciaNotFoundError,
    EmpleadoNotFoundError,
    InvalidDateError,
    MissingRequiredFieldError,
)
from ui import formato, theme
from ui.components import piezas
from ui.components.ausencia_form_dialog import (
    AusenciaFormDialog,
    AusenciaPayload,
    nombre_empleado,
)
from ui.components.data_table import Columna, DataTable, Fila
from ui.components.usuarios_format import iniciales
from ui.controllers.ausencias_controller import AusenciasController

_EN_CURSO = "EN_CURSO"
_PROXIMAS = "PROXIMAS"
_FINALIZADAS = "FINALIZADAS"
_VACACIONES_ANIO = "VACACIONES_ANIO"

_ESTADO_TEXTO = {_EN_CURSO: "En curso", _PROXIMAS: "Próxima", _FINALIZADAS: "Finalizada"}
_TODOS_LOS_TIPOS = "Todos los tipos"

_COLUMNAS = (
    Columna("empleado", "Empleado", ancho=230),
    Columna("tipo", "Tipo", ancho=110),
    Columna("desde", "Desde", ancho=95),
    Columna("hasta", "Hasta", ancho=95),
    Columna("dias", "Días", ancho=60, alinear="center"),
    Columna("estado", "Estado", ancho=95),
)

# Errores de dominio cuyo mensaje se muestra tal cual en el diálogo.
_ERRORES_ESPERADOS = (
    AusenciaError,
    EmpleadoNotFoundError,
    InvalidDateError,
    MissingRequiredFieldError,
)


class AusenciasTab(ctk.CTkFrame):
    """Lista de vacaciones, días libres, permisos e incapacidades."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        controller: AusenciasController,
        hoy: Callable[[], date] = date.today,
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self._controller = controller
        self._hoy = hoy
        self._log = logging.getLogger(self.__class__.__name__)
        self._ausencias: List[Ausencia] = []
        self._empleados: Dict[int, Empleado] = {}
        self._dias: Dict[int, int] = {}
        self._filtro: Optional[str] = None
        self._construir_ui()
        self.refrescar()

    # ── Construcción ──────────────────────────────────────────────────────

    def _construir_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        self._aviso = piezas.Aviso(self, row=0, column=0, sticky="ew", pady=(0, 12))

        anio = self._hoy().year
        self._kpis = piezas.FilaKpis(
            self,
            (
                (_EN_CURSO, "Fuera hoy", theme.COLOR_AUSENCIA["INCAPACIDAD"][0], True),
                (_PROXIMAS, "Próximas", theme.PRIMARIO, True),
                (_FINALIZADAS, "Finalizadas", theme.NEUTRO, True),
                (
                    _VACACIONES_ANIO,
                    f"Días de vacaciones en {anio}",
                    theme.COLOR_AUSENCIA["VACACIONES"][0],
                    False,
                ),
            ),
            on_click=self._set_filtro,
        )
        self._kpis.grid(row=1, column=0, sticky="ew", pady=(0, 14))

        barra = ctk.CTkFrame(self, fg_color="transparent")
        barra.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        barra.grid_columnconfigure(0, weight=1)
        self._buscar = piezas.buscador(barra, "Buscar empleado…", self._render)
        self._buscar.grid(row=0, column=0, sticky="ew")
        self._menu_tipo = piezas.menu_filtro(
            barra,
            [_TODOS_LOS_TIPOS, *TIPO_AUSENCIA_TEXTO.values()],
            lambda _v: self._render(),
            ancho=190,
        )
        self._menu_tipo.set(_TODOS_LOS_TIPOS)
        self._menu_tipo.grid(row=0, column=1, padx=(10, 0))

        cuerpo = ctk.CTkFrame(self, fg_color="transparent")
        cuerpo.grid(row=3, column=0, sticky="nsew")
        cuerpo.grid_columnconfigure(0, weight=1)
        cuerpo.grid_rowconfigure(0, weight=1)

        self._tabla = DataTable(
            cuerpo,
            _COLUMNAS,
            on_seleccion=lambda _iid: self._mostrar_detalle(),
            on_doble_click=lambda _iid: self._editar_seleccionada(),
            texto_vacio="No hay vacaciones ni permisos registrados en esta lista.",
        )
        self._tabla.grid(row=0, column=0, sticky="nsew")
        self._tabla.color_tag("finalizada", theme.TEXTO_SUAVE)

        self._detalle = piezas.panel_detalle(cuerpo)
        self._detalle.grid(row=0, column=1, sticky="nsew", padx=(14, 0))

    # ── Datos ─────────────────────────────────────────────────────────────

    def refrescar(self, seleccionar: Optional[int] = None) -> None:
        try:
            self._ausencias = self._controller.listar()
            self._empleados = {
                e.id: e for e in self._controller.empleados(solo_activos=False) if e.id is not None
            }
            self._dias = self._controller.dias_laborables_de(self._ausencias)
        except Exception:  # noqa: BLE001 — último recurso, log + aviso
            self._log.exception("Error listando ausencias")
            messagebox.showerror("Error", "No se pudo cargar la lista de vacaciones y permisos.")
            return
        self._render()
        if seleccionar is not None:
            self._tabla.seleccionar(str(seleccionar))
        self._mostrar_detalle()

    def _estado(self, a: Ausencia) -> str:
        hoy = self._hoy().isoformat()
        if a.fecha_desde > hoy:
            return _PROXIMAS
        if a.fecha_hasta < hoy:
            return _FINALIZADAS
        return _EN_CURSO

    def _nombre(self, empleado_id: int) -> str:
        e = self._empleados.get(empleado_id)
        return nombre_empleado(e) if e is not None else f"Empleado #{empleado_id}"

    def _nombre_tabla(self, empleado_id: int) -> str:
        """Como en la lista de Empleados: ``"Apellidos, Nombres"``."""
        e = self._empleados.get(empleado_id)
        return f"{e.apellidos}, {e.nombres}" if e is not None else f"Empleado #{empleado_id}"

    def _render(self) -> None:
        texto = str(self._buscar.get()).strip().lower()
        tipo_sel = str(self._menu_tipo.get())
        conteo = {_EN_CURSO: 0, _PROXIMAS: 0, _FINALIZADAS: 0}
        anio = str(self._hoy().year)
        dias_vacaciones = 0
        filas: List[Fila] = []
        prioridad = {_EN_CURSO: 0, _PROXIMAS: 1, _FINALIZADAS: 2}

        def orden_natural(a: Ausencia) -> tuple[int, int]:
            # En curso y próximas: la más cercana arriba. Finalizadas: la más reciente arriba.
            estado = self._estado(a)
            dia = date.fromisoformat(a.fecha_desde).toordinal()
            return prioridad[estado], -dia if estado == _FINALIZADAS else dia

        for a in sorted(self._ausencias, key=orden_natural):
            if a.id is None:
                continue
            estado = self._estado(a)
            conteo[estado] += 1
            if a.tipo == TipoAusencia.VACACIONES.value and a.fecha_desde[:4] == anio:
                dias_vacaciones += self._dias.get(a.id, 0)
            if self._filtro is not None and estado != self._filtro:
                continue
            if tipo_sel != _TODOS_LOS_TIPOS and a.tipo_texto != tipo_sel:
                continue
            nombre = self._nombre_tabla(a.empleado_id)
            if (
                texto
                and texto not in nombre.lower()
                and texto not in self._nombre(a.empleado_id).lower()
            ):
                continue
            dias = self._dias.get(a.id, 0)
            filas.append(
                Fila(
                    iid=str(a.id),
                    valores={
                        "empleado": nombre,
                        "tipo": a.tipo_texto,
                        "desde": formato.fecha(a.fecha_desde),
                        "hasta": formato.fecha(a.fecha_hasta),
                        "dias": str(dias),
                        "estado": _ESTADO_TEXTO[estado],
                    },
                    orden={
                        "desde": a.fecha_desde,
                        "hasta": a.fecha_hasta,
                        "dias": dias,
                        "estado": prioridad[estado],
                    },
                    tags=("finalizada",) if estado == _FINALIZADAS else (),
                )
            )
        self._tabla.set_filas(filas)
        self._kpis.actualizar(
            {
                _EN_CURSO: str(conteo[_EN_CURSO]),
                _PROXIMAS: str(conteo[_PROXIMAS]),
                _FINALIZADAS: str(conteo[_FINALIZADAS]),
                _VACACIONES_ANIO: str(dias_vacaciones),
            },
            activa=self._filtro,
        )

    def _set_filtro(self, clave: str) -> None:
        self._filtro = None if self._filtro == clave else clave
        self._render()
        self._mostrar_detalle()

    def _seleccionada(self) -> Optional[Ausencia]:
        iid = self._tabla.seleccionado()
        if iid is None:
            return None
        return next((a for a in self._ausencias if a.id == int(iid)), None)

    # ── Panel de detalle ──────────────────────────────────────────────────

    def _mostrar_detalle(self) -> None:
        panel = self._detalle
        piezas.limpiar(panel)
        a = self._seleccionada()
        if a is None or a.id is None:
            piezas.detalle_vacio(
                panel, "Seleccione un registro\npara ver el detalle,\neditarlo o eliminarlo."
            )
            return
        empleado = self._empleados.get(a.empleado_id)
        nombre = self._nombre(a.empleado_id)
        color, fondo = theme.COLOR_AUSENCIA.get(a.tipo, (theme.PRIMARIO, theme.PRIMARIO_SUAVE))
        estado = self._estado(a)

        fila = piezas.cabecera_detalle(
            panel,
            0,
            iniciales(nombre),
            piezas.color_avatar(empleado.departamento_id if empleado else a.empleado_id),
            nombre,
            formato.rango_fechas(a.fecha_desde, a.fecha_hasta),
        )
        insignias = [(a.tipo_texto, color, fondo)]
        if estado == _EN_CURSO:
            insignias.append(("En curso", theme.EXITO, theme.EXITO_FONDO))
        elif estado == _PROXIMAS:
            insignias.append(("Próxima", theme.PRIMARIO, theme.PRIMARIO_SUAVE))
        else:
            insignias.append(("Finalizada", theme.NEUTRO, theme.NEUTRO_FONDO))
        fila = piezas.fila_insignias(panel, fila, insignias)

        fila = piezas.titulo_seccion(panel, fila, "Acciones", arriba=14)
        inactivo = empleado is not None and not empleado.is_active
        fila = piezas.boton_accion(
            panel,
            fila,
            "Editar",
            lambda: self._editar(a),
            primario=True,
            motivo_deshabilitado="El empleado está dado de baja." if inactivo else None,
        )
        fila = piezas.boton_accion(
            panel, fila, "Eliminar registro", lambda: self._eliminar(a), peligro=True
        )

        dias = self._dias.get(a.id, 0)
        calendario = (date.fromisoformat(a.fecha_hasta) - date.fromisoformat(a.fecha_desde)).days
        fila = piezas.titulo_seccion(panel, fila, "Detalle", arriba=12)
        lineas = [
            ("Desde", formato.fecha_larga(a.fecha_desde)),
            ("Hasta", formato.fecha_larga(a.fecha_hasta)),
            ("Días laborables", formato.dias_cantidad(dias)),
            ("Días en total", formato.dias_cantidad(calendario + 1)),
            ("Registrado", formato.fecha_hora(a.creado_en)),
        ]
        if a.nota:
            lineas.append(("Nota", a.nota))
        fila = piezas.bloque_datos(panel, fila, lineas)

        anio = self._hoy().year
        try:
            resumen = self._controller.resumen_anual(a.empleado_id, anio)
        except Exception:  # noqa: BLE001 — informativo
            self._log.exception("Error calculando resumen anual")
            return
        fila = piezas.titulo_seccion(panel, fila, f"{nombre.split()[0]} en {anio}")
        tomados = [
            (TIPO_AUSENCIA_TEXTO[tipo], formato.dias_cantidad(n))
            for tipo, n in resumen.items()
            if n > 0
        ]
        if tomados:
            fila = piezas.bloque_datos(panel, fila, tomados)
            ctk.CTkFrame(panel, height=16, fg_color="transparent").grid(row=fila, column=0)
        else:
            piezas.texto_suave(panel, fila, "Sin vacaciones ni permisos este año.")

    # ── Acciones ──────────────────────────────────────────────────────────

    def nuevo(self, empleado: Optional[Empleado] = None) -> None:
        """Abre el formulario de registro (con el empleado ya elegido, si se pasa)."""
        activos = [e for e in self._empleados.values() if e.is_active]
        creada: Dict[str, Ausencia] = {}

        def submit(payload: AusenciaPayload) -> Optional[str]:
            try:
                creada["a"] = self._controller.registrar(**payload)
            except _ERRORES_ESPERADOS as exc:
                return str(exc)
            except Exception:  # noqa: BLE001
                self._log.exception("Error registrando ausencia")
                return "Ocurrió un error inesperado al registrar."
            return None

        dialog = AusenciaFormDialog(
            self,
            empleados=activos,
            on_submit=submit,
            contar_dias=self._controller.dias_laborables,
            empleado_fijo=empleado,
            hoy=self._hoy(),
        )
        self.wait_window(dialog)
        if "a" in creada:
            nueva = creada["a"]
            self._filtro = None
            self.refrescar(seleccionar=nueva.id)
            self._aviso.mostrar(
                f"Registrado: {nueva.tipo_texto} de {self._nombre(nueva.empleado_id)} "
                f"({formato.rango_fechas(nueva.fecha_desde, nueva.fecha_hasta)})."
            )

    def _editar_seleccionada(self) -> None:
        a = self._seleccionada()
        if a is not None:
            empleado = self._empleados.get(a.empleado_id)
            if empleado is None or empleado.is_active:
                self._editar(a)

    def _editar(self, a: Ausencia) -> None:
        assert a.id is not None
        ausencia_id = a.id
        guardado = {"ok": False}

        def submit(payload: AusenciaPayload) -> Optional[str]:
            try:
                self._controller.actualizar(
                    ausencia_id=ausencia_id,
                    tipo=payload["tipo"],
                    fecha_desde=payload["fecha_desde"],
                    fecha_hasta=payload["fecha_hasta"],
                    nota=payload["nota"],
                )
            except _ERRORES_ESPERADOS as exc:
                return str(exc)
            except Exception:  # noqa: BLE001
                self._log.exception("Error actualizando ausencia id=%s", ausencia_id)
                return "Ocurrió un error inesperado al guardar."
            guardado["ok"] = True
            return None

        dialog = AusenciaFormDialog(
            self,
            empleados=list(self._empleados.values()),
            on_submit=submit,
            contar_dias=self._controller.dias_laborables,
            inicial=a,
            hoy=self._hoy(),
        )
        self.wait_window(dialog)
        self.refrescar(seleccionar=ausencia_id)
        if guardado["ok"]:
            self._aviso.mostrar("Cambios guardados.")

    def _eliminar(self, a: Ausencia) -> None:
        assert a.id is not None
        nombre = self._nombre(a.empleado_id)
        rango = formato.rango_fechas(a.fecha_desde, a.fecha_hasta)
        if not messagebox.askyesno(
            "Eliminar registro",
            f"¿Eliminar «{a.tipo_texto}» de {nombre} ({rango})?\n\n"
            "Si esos días ya pasaron, se vuelven a calcular con las marcas del reloj: "
            "sin marca, quedarán como «Ausente».",
            parent=self,
        ):
            return
        try:
            self._controller.eliminar(a.id)
        except AusenciaNotFoundError as exc:
            messagebox.showerror("No se pudo eliminar", str(exc), parent=self)
            self.refrescar()
            return
        except Exception:  # noqa: BLE001
            self._log.exception("Error eliminando ausencia id=%s", a.id)
            messagebox.showerror("Error", "No se pudo eliminar el registro.", parent=self)
            return
        self.refrescar()
        self._aviso.mostrar(f"Se eliminó «{a.tipo_texto}» de {nombre} ({rango}).")
