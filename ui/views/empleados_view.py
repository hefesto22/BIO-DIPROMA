"""Vista de Empleados.

    Empleados                                             [+ Nuevo empleado]
    Registre al personal, su huella en el reloj y sus turnos.
    ┌─────────┬────────────┬───────────┬─────────┐
    │ Activos │ Sin huella │ Sin turno │ De baja │   ← tarjetas = filtro
    └─────────┴────────────┴───────────┴─────────┘
    [ Buscar por nombre, DNI o ID del reloj… ]  [ Todos los departamentos ▾ ]
    ┌──────────────────────────────────────────┐ ┌──────────────┐
    │ EMPLEADO  DEPTO  CARGO  TURNO  ID RELOJ   │ │ detalle +    │
    │ …                                         │ │ acciones     │
    └──────────────────────────────────────────┘ └──────────────┘

El permiso ``MANAGE_EMPLOYEES`` vive en el controller (cada método
decorado con ``@require_permission``): esta vista solo arma widgets e
invoca callables del controller. Los diálogos de alta/edición, turnos y
baja son los mismos de antes; acá cambia la presentación.

Carga: una sola pasada por refresco (empleados activos + archivados,
catálogos, turnos y el mapa de turnos vigentes); filtros y búsqueda se
resuelven en memoria.
"""

from __future__ import annotations

import logging
from datetime import date
from tkinter import messagebox
from typing import Callable, Dict, List, Optional, Tuple

import customtkinter as ctk

from core.models.ausencia import Ausencia
from core.models.empleado import Empleado
from core.models.empleado_turno import EmpleadoTurno
from core.models.turno import Turno
from core.services.errors import (
    AusenciaError,
    CatalogoNotFoundError,
    DuplicateDNIError,
    DuplicateZktecoIdError,
    EmpleadoAlreadyActiveError,
    EmpleadoAlreadyInactiveError,
    EmpleadoNotFoundError,
    InvalidDateError,
    InvalidDNIError,
    InvalidMotivoBajaError,
    MissingRequiredFieldError,
    TurnoInactiveError,
    TurnoNotFoundError,
)
from ui import formato, theme
from ui.components import piezas
from ui.components.ausencia_form_dialog import AusenciaFormDialog, AusenciaPayload
from ui.components.data_table import Columna, DataTable, Fila
from ui.components.dar_de_baja_dialog import DarDeBajaDialog, DarDeBajaPayload
from ui.components.empleado_form_dialog import EmpleadoFormDialog, EmpleadoFormPayload
from ui.components.gestion_turnos_dialog import GestionTurnosDialog
from ui.components.usuarios_format import iniciales
from ui.controllers.ausencias_controller import AusenciasController
from ui.controllers.empleados_controller import EmpleadosController

# Errores de vacaciones/permisos cuyo mensaje se muestra tal cual.
_ERRORES_AUSENCIA = (
    AusenciaError,
    EmpleadoNotFoundError,
    InvalidDateError,
    MissingRequiredFieldError,
)

_ACTIVOS = "ACTIVOS"
_SIN_HUELLA = "SIN_HUELLA"
_SIN_TURNO = "SIN_TURNO"
_DE_BAJA = "DE_BAJA"
_TODOS_DEPTOS = "Todos los departamentos"

_COLUMNAS = (
    Columna("empleado", "Empleado", ancho=225),
    Columna("depto", "Departamento", ancho=125),
    Columna("cargo", "Cargo", ancho=140),
    Columna("turno", "Turno", ancho=215),
    Columna("reloj", "ID reloj", ancho=75, alinear="center"),
)


class EmpleadosView(ctk.CTkFrame):
    """Vista raíz del módulo Empleados."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        controller: EmpleadosController,
        ausencias: Optional[AusenciasController] = None,
    ) -> None:
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self._controller = controller
        self._ausencias_ctrl = ausencias
        self._log = logging.getLogger(self.__class__.__name__)
        # empleado_id → sus ausencias vigentes o próximas (para el panel).
        self._ausencias: Dict[int, List[Ausencia]] = {}

        self._empleados: List[Empleado] = []
        self._deps: Dict[int, str] = {}
        self._cargos: Dict[int, str] = {}
        self._turnos: Dict[int, Turno] = {}
        self._vigentes: Dict[int, List[EmpleadoTurno]] = {}
        self._filtro = _ACTIVOS

        self._construir_ui()
        self._refrescar()

    # ── Construcción ──────────────────────────────────────────────────────

    def _construir_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)

        cabecera, _ = piezas.encabezado(
            self,
            "Empleados",
            "Registre al personal, su ID en el reloj biométrico y sus turnos.",
            "+  Nuevo empleado",
            self._on_nuevo,
        )
        cabecera.grid(row=0, column=0, sticky="ew", padx=28, pady=(24, 18))

        self._aviso = piezas.Aviso(self, row=1, column=0, sticky="ew", padx=28, pady=(0, 12))

        self._kpis = piezas.FilaKpis(
            self,
            (
                (_ACTIVOS, "Activos", theme.PRIMARIO, True),
                (_SIN_HUELLA, "Sin ID en el reloj", theme.ALERTA, True),
                (_SIN_TURNO, "Sin turno asignado", theme.ALERTA, True),
                (_DE_BAJA, "De baja", theme.NEUTRO, True),
            ),
            on_click=self._set_filtro,
        )
        self._kpis.grid(row=2, column=0, sticky="ew", padx=28, pady=(0, 14))

        barra = ctk.CTkFrame(self, fg_color="transparent")
        barra.grid(row=3, column=0, sticky="ew", padx=28, pady=(0, 10))
        barra.grid_columnconfigure(0, weight=1)
        self._buscador = piezas.buscador(
            barra, "Buscar por nombre, DNI o ID del reloj…", self._render
        )
        self._buscador.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self._menu_depto = piezas.menu_filtro(barra, [_TODOS_DEPTOS], lambda _v: self._render())
        self._menu_depto.grid(row=0, column=1)

        cuerpo = ctk.CTkFrame(self, fg_color="transparent")
        cuerpo.grid(row=5, column=0, sticky="nsew", padx=28, pady=(0, 20))
        cuerpo.grid_columnconfigure(0, weight=1)
        cuerpo.grid_rowconfigure(0, weight=1)

        self._tabla = DataTable(
            cuerpo,
            _COLUMNAS,
            on_seleccion=lambda _iid: self._mostrar_detalle(),
            on_doble_click=lambda iid: self._accion_por_id(iid, self._on_editar),
            texto_vacio="No hay empleados que coincidan con el filtro.",
        )
        self._tabla.grid(row=0, column=0, sticky="nsew")
        self._tabla.color_tag("baja", theme.TEXTO_SUAVE)
        self._tabla.color_tag("pendiente", theme.ALERTA)

        self._detalle = piezas.panel_detalle(cuerpo)
        self._detalle.grid(row=0, column=1, sticky="nsew", padx=(14, 0))

    # ── Datos ─────────────────────────────────────────────────────────────

    def al_mostrar(self) -> None:
        """Al volver al módulo (la vista queda guardada): datos al día."""
        self._refrescar()

    def _refrescar(self, seleccionar: Optional[int] = None) -> None:
        try:
            self._empleados = self._controller.list_empleados(solo_activos=False)
            self._deps = {
                d.id: d.nombre
                for d in self._controller.list_departamentos(solo_activos=False)
                if d.id is not None
            }
            self._cargos = {
                c.id: c.nombre
                for c in self._controller.list_cargos(solo_activos=False)
                if c.id is not None
            }
            self._turnos = {
                t.id: t
                for t in self._controller.list_turnos(solo_activos=False)
                if t.id is not None
            }
            self._vigentes = self._controller.mapa_turnos_vigentes()
            self._ausencias = self._cargar_ausencias()
        except Exception:  # noqa: BLE001
            self._log.exception("Error cargando empleados")
            messagebox.showerror("Error", "No se pudo cargar la lista de empleados.")
            return

        actual = self._menu_depto.get()
        nombres = sorted(set(self._deps.values()), key=str.casefold)
        self._menu_depto.configure(values=[_TODOS_DEPTOS, *nombres])
        if actual not in nombres:
            self._menu_depto.set(_TODOS_DEPTOS)

        self._render()
        if seleccionar is not None:
            self._tabla.seleccionar(str(seleccionar))
        self._mostrar_detalle()

    def _cargar_ausencias(self) -> Dict[int, List[Ausencia]]:
        """Ausencias que no terminaron (en curso o próximas), por empleado."""
        if self._ausencias_ctrl is None:
            return {}
        hoy = date.today().isoformat()
        por_empleado: Dict[int, List[Ausencia]] = {}
        for a in self._ausencias_ctrl.listar():
            if a.fecha_hasta >= hoy:
                por_empleado.setdefault(a.empleado_id, []).append(a)
        for lista in por_empleado.values():
            lista.sort(key=lambda a: a.fecha_desde)
        return por_empleado

    def _categorias(self, emp: Empleado) -> Tuple[str, ...]:
        if not emp.is_active:
            return (_DE_BAJA,)
        cats = [_ACTIVOS]
        if emp.zkteco_id is None:
            cats.append(_SIN_HUELLA)
        if emp.id is not None and not self._vigentes.get(emp.id):
            cats.append(_SIN_TURNO)
        return tuple(cats)

    def _render(self) -> None:
        texto = str(self._buscador.get()).strip().casefold()
        depto = self._menu_depto.get()
        conteo = {_ACTIVOS: 0, _SIN_HUELLA: 0, _SIN_TURNO: 0, _DE_BAJA: 0}
        filas: List[Fila] = []
        for emp in self._empleados:
            if emp.id is None:
                continue
            cats = self._categorias(emp)
            for c in cats:
                conteo[c] += 1
            if self._filtro not in cats:
                continue
            nombre_depto = self._deps.get(emp.departamento_id, "—")
            if depto != _TODOS_DEPTOS and nombre_depto != depto:
                continue
            reloj = str(emp.zkteco_id) if emp.zkteco_id is not None else ""
            if texto and not any(
                texto in campo.casefold()
                for campo in (
                    emp.nombres,
                    emp.apellidos,
                    emp.dni,
                    reloj,
                    f"{emp.nombres} {emp.apellidos}",
                )
            ):
                continue
            pendiente = emp.is_active and (emp.zkteco_id is None or not self._vigentes.get(emp.id))
            filas.append(
                Fila(
                    iid=str(emp.id),
                    valores={
                        "empleado": f"{emp.apellidos}, {emp.nombres}",
                        "depto": nombre_depto,
                        "cargo": self._cargos.get(emp.cargo_id, "—"),
                        "turno": self._texto_turno_corto(emp.id) if emp.is_active else "De baja",
                        "reloj": reloj or "—",
                    },
                    orden={"reloj": emp.zkteco_id if emp.zkteco_id is not None else 10**9},
                    tags=("baja",) if not emp.is_active else (("pendiente",) if pendiente else ()),
                )
            )
        self._tabla.set_filas(filas)
        self._kpis.actualizar({k: str(v) for k, v in conteo.items()}, activa=self._filtro)

    def _set_filtro(self, clave: str) -> None:
        self._filtro = clave
        self._render()
        self._mostrar_detalle()

    def _texto_turno_corto(self, empleado_id: int) -> str:
        vigentes = self._vigentes.get(empleado_id, [])
        if not vigentes:
            return "Sin turno"
        # Horario compacto en vez del nombre (los nombres suelen ser largos);
        # el de más días va primero (p. ej. L-V antes que sábado).
        turnos = sorted(
            (self._turnos[a.turno_id] for a in vigentes if a.turno_id in self._turnos),
            key=lambda t: bin(t.dias_totales).count("1"),
            reverse=True,
        )
        if not turnos:
            return "—"
        t = turnos[0]
        texto = f"{t.nombre}  ·  {formato.dias_texto(t.dias_totales)}"
        return texto + (f"  (+{len(turnos) - 1})" if len(turnos) > 1 else "")

    def _seleccionado(self) -> Optional[Empleado]:
        iid = self._tabla.seleccionado()
        if iid is None:
            return None
        return next((e for e in self._empleados if e.id == int(iid)), None)

    # ── Panel de detalle ──────────────────────────────────────────────────

    def _mostrar_detalle(self) -> None:
        panel = self._detalle
        piezas.limpiar(panel)
        emp = self._seleccionado()
        if emp is None or emp.id is None:
            piezas.detalle_vacio(panel, "Seleccione un empleado\npara ver su ficha y acciones.")
            return
        emp_id = emp.id
        depto = self._deps.get(emp.departamento_id, "—")
        cargo = self._cargos.get(emp.cargo_id, "—")
        vigentes = self._vigentes.get(emp_id, [])

        fila = piezas.cabecera_detalle(
            panel,
            0,
            iniciales(f"{emp.nombres} {emp.apellidos}"),
            piezas.color_avatar(emp.departamento_id),
            f"{emp.nombres} {emp.apellidos}",
            f"{cargo}  ·  {depto}",
        )
        insignias = []
        if emp.is_active:
            insignias.append(("Activo", theme.EXITO, theme.EXITO_FONDO))
            if emp.zkteco_id is None:
                insignias.append(("Sin ID en el reloj", theme.ALERTA, theme.ALERTA_FONDO))
            if not vigentes:
                insignias.append(("Sin turno", theme.ALERTA, theme.ALERTA_FONDO))
            hoy = date.today().isoformat()
            fuera = next((a for a in self._ausencias.get(emp_id, []) if a.cubre(hoy)), None)
            if fuera is not None:
                color, fondo = theme.COLOR_AUSENCIA.get(
                    fuera.tipo, (theme.NEUTRO, theme.NEUTRO_FONDO)
                )
                insignias.append((f"{fuera.tipo_texto} hoy", color, fondo))
        else:
            insignias.append(("De baja", theme.NEUTRO, theme.NEUTRO_FONDO))
        fila = piezas.fila_insignias(panel, fila, insignias)

        fila = piezas.titulo_seccion(panel, fila, "Acciones", arriba=14)
        if emp.is_active:
            fila = piezas.boton_accion(
                panel, fila, "Editar datos", lambda: self._on_editar(emp), primario=True
            )
            fila = piezas.boton_accion(
                panel, fila, "Gestionar turnos", lambda: self._on_turnos(emp)
            )
            if self._ausencias_ctrl is not None:
                fila = piezas.boton_accion(
                    panel, fila, "Vacaciones o permiso", lambda: self._on_ausencia(emp)
                )
            fila = piezas.boton_accion(
                panel, fila, "Dar de baja", lambda: self._on_dar_de_baja(emp), peligro=True
            )
        else:
            fila = piezas.boton_accion(
                panel, fila, "Reactivar empleado", lambda: self._on_reactivar(emp), primario=True
            )

        fila = piezas.titulo_seccion(panel, fila, "Datos", arriba=12)
        fila = piezas.bloque_datos(
            panel,
            fila,
            [
                ("DNI", emp.dni),
                (
                    "ID en el reloj",
                    str(emp.zkteco_id) if emp.zkteco_id is not None else "Sin asignar",
                ),
                ("Teléfono", emp.telefono or "—"),
                ("Correo", emp.email or "—"),
                ("Ingreso", formato.fecha(emp.fecha_ingreso)),
                ("Antigüedad", formato.antiguedad(emp.fecha_ingreso, date.today())),
            ],
        )

        if not emp.is_active:
            fila = piezas.titulo_seccion(panel, fila, "Baja")
            lineas = [
                ("Fecha", formato.fecha(emp.fecha_baja)),
                (
                    "Motivo",
                    formato.MOTIVO_BAJA_TEXTO.get(emp.motivo_baja or "", emp.motivo_baja or "—"),
                ),
            ]
            if emp.nota_baja:
                lineas.append(("Nota", emp.nota_baja))
            fila = piezas.bloque_datos(panel, fila, lineas)
            ctk.CTkFrame(panel, fg_color="transparent", height=12).grid(row=fila, column=0)
            return

        fila = piezas.titulo_seccion(panel, fila, "Turnos vigentes")
        if not vigentes:
            piezas.texto_suave(panel, fila, "No tiene turno asignado: su asistencia no se calcula.")
            return
        for asignacion in vigentes:
            turno = self._turnos.get(asignacion.turno_id)
            if turno is None:
                continue
            tarjeta = ctk.CTkFrame(panel, fg_color=theme.TARJETA_ALT, corner_radius=8)
            tarjeta.grid(row=fila, column=0, sticky="ew", padx=16, pady=(0, 6))
            tarjeta.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                tarjeta,
                text=turno.nombre,
                font=theme.fuente(theme.TAM_PEQUENO, "bold"),
                anchor="w",
                justify="left",
                wraplength=260,
            ).grid(row=0, column=0, sticky="w", padx=12, pady=(8, 0))
            ctk.CTkLabel(
                tarjeta,
                text=(
                    f"{formato.horario_texto(turno, con_dias=True)}\n"
                    f"Desde {formato.fecha(asignacion.fecha_inicio)}"
                ),
                font=theme.fuente(theme.TAM_PEQUENO - 1),
                text_color=theme.TEXTO_SUAVE,
                anchor="w",
                justify="left",
                wraplength=260,
            ).grid(row=1, column=0, sticky="w", padx=12, pady=(0, 8))
            fila += 1

        if self._ausencias_ctrl is not None:
            fila = self._seccion_ausencias(panel, fila, emp_id)
        ctk.CTkFrame(panel, fg_color="transparent", height=10).grid(row=fila, column=0)

    def _seccion_ausencias(self, panel: ctk.CTkScrollableFrame, fila: int, emp_id: int) -> int:
        """Vacaciones y permisos en curso o próximos del empleado."""
        proximas = self._ausencias.get(emp_id, [])
        fila = piezas.titulo_seccion(panel, fila, "Vacaciones y permisos")
        if not proximas:
            return piezas.texto_suave(
                panel, fila, "Nada programado. Use «Vacaciones o permiso» para registrar.", abajo=4
            )
        for a in proximas[:4]:
            color, fondo = theme.COLOR_AUSENCIA.get(a.tipo, (theme.NEUTRO, theme.NEUTRO_FONDO))
            linea = ctk.CTkFrame(panel, fg_color="transparent")
            linea.grid(row=fila, column=0, sticky="ew", padx=16, pady=(0, 6))
            linea.grid_columnconfigure(1, weight=1)
            piezas.insignia(linea, a.tipo_texto, color, fondo).grid(row=0, column=0, sticky="w")
            ctk.CTkLabel(
                linea,
                text=formato.rango_fechas(a.fecha_desde, a.fecha_hasta),
                font=theme.fuente(theme.TAM_PEQUENO),
                anchor="e",
            ).grid(row=0, column=1, sticky="e")
            fila += 1
        return fila

    # ── Acciones ──────────────────────────────────────────────────────────

    def _accion_por_id(self, iid: str, accion: Callable[[Empleado], None]) -> None:
        emp = next((e for e in self._empleados if e.id == int(iid)), None)
        if emp is not None and emp.is_active:
            accion(emp)

    def _cargos_por_depto_provider(self, departamento_id: int) -> List[Tuple[int, str]]:
        try:
            cargos = self._controller.list_cargos_para_departamento(departamento_id)
        except Exception:  # noqa: BLE001
            self._log.exception("No se pudieron cargar cargos para depto=%s", departamento_id)
            return []
        return [(c.id, c.nombre) for c in cargos if c.id is not None]

    def _catalogos_activos(
        self,
    ) -> Optional[Tuple[List[Tuple[int, str]], List[Tuple[int, str]], List[Tuple[int, str]]]]:
        try:
            deps = [
                (d.id, d.nombre)
                for d in self._controller.list_departamentos(solo_activos=True)
                if d.id is not None
            ]
            cargos = [
                (c.id, c.nombre)
                for c in self._controller.list_cargos(solo_activos=True)
                if c.id is not None
            ]
            turnos = [
                (t.id, t.nombre)
                for t in self._controller.list_turnos(solo_activos=True)
                if t.id is not None
            ]
        except Exception:  # noqa: BLE001
            self._log.exception("Error cargando catálogos")
            messagebox.showerror("Error", "No se pudieron cargar los catálogos.")
            return None
        return deps, cargos, turnos

    def _on_nuevo(self) -> None:
        catalogos = self._catalogos_activos()
        if catalogos is None:
            return
        deps, cargos, turnos = catalogos
        if not deps or not cargos:
            messagebox.showwarning(
                "Faltan catálogos",
                "Antes de registrar empleados cree al menos un departamento y un cargo "
                "en el módulo Configuración.",
            )
            return
        if not turnos:
            messagebox.showwarning(
                "Sin turnos activos",
                "Antes de registrar empleados cree al menos un turno en el módulo Turnos.",
            )
            return
        antes = {e.id for e in self._empleados}
        dialog = EmpleadoFormDialog(
            self,
            title="Nuevo empleado",
            on_submit=self._handle_create_con_turno,
            departamentos=deps,
            cargos=cargos,
            turnos=turnos,
            modo_alta=True,
            cargos_por_depto_provider=self._cargos_por_depto_provider,
        )
        self.wait_window(dialog)
        self._refrescar()
        nuevos = [e for e in self._empleados if e.id not in antes]
        if nuevos and nuevos[0].id is not None:
            self._filtro = _ACTIVOS
            self._render()
            self._tabla.seleccionar(str(nuevos[0].id))
            self._mostrar_detalle()
            self._aviso.mostrar(f"{nuevos[0].nombres} {nuevos[0].apellidos} fue registrado.")

    def _on_editar(self, emp: Empleado) -> None:
        assert emp.id is not None
        emp_id = emp.id
        catalogos = self._catalogos_activos()
        if catalogos is None:
            return
        deps, cargos, _turnos = catalogos
        guardado = {"ok": False}

        def submit(payload: EmpleadoFormPayload) -> Optional[str]:
            error = self._handle_update(emp_id, payload)
            guardado["ok"] = error is None
            return error

        dialog = EmpleadoFormDialog(
            self,
            title=f"Editar empleado — {emp.nombres} {emp.apellidos}",
            on_submit=submit,
            departamentos=deps,
            cargos=cargos,
            turnos=None,
            modo_alta=False,
            initial_dni=emp.dni,
            initial_nombres=emp.nombres,
            initial_apellidos=emp.apellidos,
            initial_departamento_id=emp.departamento_id,
            initial_cargo_id=emp.cargo_id,
            initial_fecha_ingreso=emp.fecha_ingreso,
            initial_telefono=emp.telefono or "",
            initial_email=emp.email or "",
            initial_zkteco_id=emp.zkteco_id,
            cargos_por_depto_provider=self._cargos_por_depto_provider,
        )
        self.wait_window(dialog)
        self._refrescar(seleccionar=emp_id)
        if guardado["ok"]:
            self._aviso.mostrar("Cambios guardados.")

    def _on_turnos(self, emp: Empleado) -> None:
        assert emp.id is not None
        emp_id = emp.id

        def asignar(empleado_id: int, turno_id: int, fecha_inicio: str) -> None:
            self._controller.asignar_turno(empleado_id, turno_id, fecha_inicio)

        def cerrar(asignacion_id: int, fecha_fin: str) -> None:
            self._controller.cerrar_asignacion(asignacion_id, fecha_fin)

        dialog = GestionTurnosDialog(
            self,
            empleado=emp,
            list_vigentes_fn=self._controller.list_turnos_vigentes,
            list_turnos_fn=self._controller.list_turnos,
            asignar_fn=asignar,
            cerrar_fn=cerrar,
            on_done=lambda: None,
        )
        self.wait_window(dialog)
        self._refrescar(seleccionar=emp_id)

    def _on_dar_de_baja(self, emp: Empleado) -> None:
        assert emp.id is not None
        emp_id = emp.id
        hecho = {"ok": False}

        def submit(payload: DarDeBajaPayload) -> Optional[str]:
            error = self._handle_dar_de_baja(emp_id, payload)
            hecho["ok"] = error is None
            return error

        dialog = DarDeBajaDialog(
            self,
            on_submit=submit,
            empleado_descripcion=f"{emp.nombres} {emp.apellidos} (DNI {emp.dni})",
        )
        self.wait_window(dialog)
        self._refrescar()
        if hecho["ok"]:
            self._aviso.mostrar(
                f"{emp.nombres} {emp.apellidos} fue dado de baja. Lo encuentra en «De baja»."
            )

    def _on_ausencia(self, emp: Empleado) -> None:
        ctrl = self._ausencias_ctrl
        if ctrl is None or emp.id is None:
            return
        creada: Dict[str, Ausencia] = {}

        def submit(payload: AusenciaPayload) -> Optional[str]:
            try:
                creada["a"] = ctrl.registrar(**payload)
            except _ERRORES_AUSENCIA as exc:
                return str(exc)
            except Exception:  # noqa: BLE001
                self._log.exception("Error registrando ausencia de empleado id=%s", emp.id)
                return "Ocurrió un error inesperado al registrar."
            return None

        dialog = AusenciaFormDialog(
            self,
            empleados=[emp],
            on_submit=submit,
            contar_dias=ctrl.dias_laborables,
            empleado_fijo=emp,
        )
        self.wait_window(dialog)
        if "a" in creada:
            a = creada["a"]
            self._refrescar(seleccionar=emp.id)
            self._aviso.mostrar(
                f"Registrado: {a.tipo_texto} de {emp.nombres} {emp.apellidos} "
                f"({formato.rango_fechas(a.fecha_desde, a.fecha_hasta)}). "
                "Se edita en Turnos › Vacaciones y permisos."
            )

    def _on_reactivar(self, emp: Empleado) -> None:
        assert emp.id is not None
        confirmado = messagebox.askyesno(
            "Reactivar empleado",
            (
                f"¿Reactivar a {emp.nombres} {emp.apellidos}?\n\n"
                "Vuelve a la lista de activos, pero sin turno: tendrá que "
                "asignarle uno desde «Gestionar turnos»."
            ),
        )
        if not confirmado:
            return
        try:
            self._controller.reactivate_empleado(emp.id)
        except EmpleadoNotFoundError as exc:
            messagebox.showerror("Empleado no encontrado", str(exc))
            return
        except EmpleadoAlreadyActiveError as exc:
            messagebox.showwarning("Ya está activo", str(exc))
            return
        except Exception:  # noqa: BLE001
            self._log.exception("Error reactivando empleado id=%s", emp.id)
            messagebox.showerror("Error", "No se pudo reactivar el empleado.")
            return
        self._filtro = _ACTIVOS
        self._refrescar(seleccionar=emp.id)
        self._aviso.mostrar("Empleado reactivado. Recuerde asignarle un turno.")

    # ── Callbacks de los diálogos (delegan al controller) ─────────────────

    def _handle_create_con_turno(self, payload: EmpleadoFormPayload) -> Optional[str]:
        turno_id = payload.get("turno_id")
        fecha_inicio_turno = payload.get("fecha_inicio_turno")
        if turno_id is None or fecha_inicio_turno is None:
            return "Falta seleccionar el turno inicial."
        try:
            resultado = self._controller.create_empleado_con_turno(
                dni=payload["dni"],
                nombres=payload["nombres"],
                apellidos=payload["apellidos"],
                departamento_id=payload["departamento_id"],
                cargo_id=payload["cargo_id"],
                fecha_ingreso=payload["fecha_ingreso"],
                turno_id=turno_id,
                fecha_inicio_turno=fecha_inicio_turno,
                telefono=payload["telefono"],
                email=payload["email"],
                zkteco_id=payload["zkteco_id"],
            )
        except (
            MissingRequiredFieldError,
            InvalidDNIError,
            InvalidDateError,
            DuplicateDNIError,
            DuplicateZktecoIdError,
            CatalogoNotFoundError,
            TurnoNotFoundError,
            TurnoInactiveError,
        ) as exc:
            return str(exc)
        except Exception:  # noqa: BLE001
            self._log.exception("Error creando empleado")
            return "Ocurrió un error inesperado al crear el empleado."
        if resultado.warning:
            messagebox.showwarning("Empleado creado con aviso", resultado.warning)
        return None

    def _handle_update(self, emp_id: int, payload: EmpleadoFormPayload) -> Optional[str]:
        try:
            self._controller.update_empleado(
                empleado_id=emp_id,
                dni=payload["dni"],
                nombres=payload["nombres"],
                apellidos=payload["apellidos"],
                departamento_id=payload["departamento_id"],
                cargo_id=payload["cargo_id"],
                fecha_ingreso=payload["fecha_ingreso"],
                telefono=payload["telefono"],
                email=payload["email"],
                zkteco_id=payload["zkteco_id"],
            )
        except (
            MissingRequiredFieldError,
            InvalidDNIError,
            InvalidDateError,
            DuplicateDNIError,
            DuplicateZktecoIdError,
            CatalogoNotFoundError,
            EmpleadoNotFoundError,
        ) as exc:
            return str(exc)
        except Exception:  # noqa: BLE001
            self._log.exception("Error actualizando empleado id=%s", emp_id)
            return "Ocurrió un error inesperado al guardar el empleado."
        return None

    def _handle_dar_de_baja(self, emp_id: int, payload: DarDeBajaPayload) -> Optional[str]:
        try:
            self._controller.deactivate_empleado(
                empleado_id=emp_id,
                fecha_baja=payload["fecha_baja"],
                motivo_baja=payload["motivo_baja"],
                nota_baja=payload["nota_baja"],
            )
        except (
            EmpleadoNotFoundError,
            EmpleadoAlreadyInactiveError,
            InvalidMotivoBajaError,
            InvalidDateError,
            MissingRequiredFieldError,
        ) as exc:
            return str(exc)
        except Exception:  # noqa: BLE001
            self._log.exception("Error dando de baja empleado id=%s", emp_id)
            return "Ocurrió un error inesperado al dar de baja al empleado."
        return None
