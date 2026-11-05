"""Vista de Turnos.

    Turnos                                                    [+ Nuevo turno]
    Horarios de trabajo contra los que se calcula la asistencia.
    [ Turnos | Vacaciones y permisos ]
    ┌──────────┬────────────┬─────────────────────┐
    │ Activos  │ Archivados │ Empleados asignados │   ← las dos primeras filtran
    └──────────┴────────────┴─────────────────────┘
    ┌──────────────────────────────────────────────┐ ┌──────────────┐
    │ TURNO  HORARIO  DÍAS  JORNADA  DESC.  EMPL.   │ │ 09:00–17:00  │
    │ …                                             │ │ L M M J V S D│
    └──────────────────────────────────────────────┘ │ datos, acc., │
                                                      │ empleados    │
                                                      └──────────────┘

Un turno puede tener varios horarios (p. ej. lunes a viernes de 9 a 5 y
sábado de 9 a 12). La pestaña "Vacaciones y permisos" vive en
``ui/components/ausencias_tab.py``.

El permiso (``MANAGE_EMPLOYEES``) lo valida el controller en cada método.
"""

from __future__ import annotations

import logging
from tkinter import messagebox
from typing import Dict, List, Optional

import customtkinter as ctk

from core.models.empleado import Empleado
from core.models.turno import Turno
from core.services.errors import (
    DuplicateTurnoNombreError,
    HorariosSolapadosError,
    InvalidAlmuerzoError,
    InvalidBitmaskError,
    InvalidDescansoError,
    InvalidTimeError,
    MissingRequiredFieldError,
    TurnoConflictoEmpleadosError,
    TurnoNotFoundError,
)
from ui import formato, theme
from ui.components import piezas
from ui.components.ausencias_tab import AusenciasTab
from ui.components.data_table import Columna, DataTable, Fila
from ui.components.turno_form_dialog import TurnoFormDialog, TurnoFormPayload
from ui.controllers.ausencias_controller import AusenciasController
from ui.controllers.turnos_controller import TurnosController

_TAB_TURNOS = "Turnos"
_TAB_AUSENCIAS = "Vacaciones y permisos"

_ERRORES_TURNO = (
    MissingRequiredFieldError,
    InvalidTimeError,
    InvalidBitmaskError,
    InvalidDescansoError,
    InvalidAlmuerzoError,
    DuplicateTurnoNombreError,
    HorariosSolapadosError,
    TurnoNotFoundError,
)

_ACTIVOS = "ACTIVOS"
_ARCHIVADOS = "ARCHIVADOS"
_ASIGNADOS = "ASIGNADOS"

# Cuántos nombres de empleados se listan en el panel antes de resumir.
_MAX_EMPLEADOS_EN_PANEL = 12

_COLUMNAS = (
    Columna("nombre", "Turno", ancho=200),
    Columna("horario", "Horario", ancho=230),
    Columna("dias", "Días", ancho=110),
    Columna("semana", "Por semana", ancho=100, alinear="center"),
    Columna("empleados", "Empleados", ancho=85, alinear="center"),
)


class TurnosView(ctk.CTkFrame):
    """Vista raíz del módulo Turnos."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        controller: TurnosController,
        ausencias: Optional[AusenciasController] = None,
    ) -> None:
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self._controller = controller
        self._ausencias_ctrl = ausencias
        self._tab_ausencias: Optional[AusenciasTab] = None
        self._log = logging.getLogger(self.__class__.__name__)
        self._turnos: List[Turno] = []
        self._empleados: Dict[int, List[Empleado]] = {}
        self._filtro = _ACTIVOS
        self._conflictos_resueltos = 0
        self._construir_ui()
        self._refrescar()

    # ── Construcción ──────────────────────────────────────────────────────

    def _construir_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        cabecera, boton = piezas.encabezado(
            self,
            "Turnos",
            "Horarios de trabajo, vacaciones y permisos: contra esto se calcula la asistencia.",
            "+  Nuevo turno",
            self._on_boton_principal,
        )
        cabecera.grid(row=0, column=0, sticky="ew", padx=28, pady=(24, 0))
        assert boton is not None
        self._boton_principal = boton

        self._selector: Optional[ctk.CTkSegmentedButton] = None
        if self._ausencias_ctrl is not None:
            self._selector = ctk.CTkSegmentedButton(
                self,
                values=[_TAB_TURNOS, _TAB_AUSENCIAS],
                command=self._cambiar_tab,
                height=34,
                font=theme.fuente(theme.TAM_NORMAL),
                # Estilo "pastilla", igual que en Usuarios y roles.
                selected_color=theme.TARJETA,
                selected_hover_color=theme.TARJETA,
                unselected_color=theme.SECUNDARIO,
                unselected_hover_color=theme.SECUNDARIO_HOVER,
                fg_color=theme.SECUNDARIO,
                text_color=theme.TEXTO,
            )
            self._selector.set(_TAB_TURNOS)
            self._selector.grid(row=1, column=0, sticky="w", padx=28, pady=(18, 0))

        # Contenido de la pestaña "Turnos".
        self._tab_turnos = ctk.CTkFrame(self, fg_color="transparent")
        self._tab_turnos.grid(row=2, column=0, sticky="nsew", padx=28, pady=(16, 20))
        self._tab_turnos.grid_columnconfigure(0, weight=1)
        self._tab_turnos.grid_rowconfigure(4, weight=1)
        tab = self._tab_turnos

        self._aviso = piezas.Aviso(tab, row=1, column=0, sticky="ew", pady=(0, 12))

        self._kpis = piezas.FilaKpis(
            tab,
            (
                (_ACTIVOS, "Turnos activos", theme.PRIMARIO, True),
                (_ARCHIVADOS, "Archivados", theme.NEUTRO, True),
                (_ASIGNADOS, "Empleados con turno", theme.EXITO, False),
            ),
            on_click=self._set_filtro,
        )
        self._kpis.grid(row=2, column=0, sticky="ew", pady=(0, 14))

        cuerpo = ctk.CTkFrame(tab, fg_color="transparent")
        cuerpo.grid(row=4, column=0, sticky="nsew")
        cuerpo.grid_columnconfigure(0, weight=1)
        cuerpo.grid_rowconfigure(0, weight=1)

        self._tabla = DataTable(
            cuerpo,
            _COLUMNAS,
            on_seleccion=lambda _iid: self._mostrar_detalle(),
            on_doble_click=self._doble_click,
            texto_vacio="No hay turnos en esta lista.",
        )
        self._tabla.grid(row=0, column=0, sticky="nsew")
        self._tabla.color_tag("archivado", theme.TEXTO_SUAVE)
        self._tabla.color_tag("sin_empleados", theme.TEXTO_SUAVE)

        self._detalle = piezas.panel_detalle(cuerpo)
        self._detalle.grid(row=0, column=1, sticky="nsew", padx=(14, 0))

    # ── Pestañas ──────────────────────────────────────────────────────────

    def _cambiar_tab(self, nombre: str) -> None:
        if nombre == _TAB_AUSENCIAS and self._ausencias_ctrl is not None:
            self._tab_turnos.grid_remove()
            if self._tab_ausencias is None:
                self._tab_ausencias = AusenciasTab(self, controller=self._ausencias_ctrl)
            else:
                self._tab_ausencias.refrescar()
            self._tab_ausencias.grid(row=2, column=0, sticky="nsew", padx=28, pady=(16, 20))
            self._boton_principal.configure(text="+  Registrar ausencia")
        else:
            if self._tab_ausencias is not None:
                self._tab_ausencias.grid_remove()
            self._tab_turnos.grid()
            self._boton_principal.configure(text="+  Nuevo turno")
            self._refrescar(seleccionar=self._id_seleccionado())

    def al_mostrar(self) -> None:
        """Al volver al módulo (la vista queda guardada): datos al día."""
        if self._tab_ausencias is not None and self._tab_ausencias.winfo_ismapped():
            self._tab_ausencias.refrescar()
        else:
            self._refrescar(seleccionar=self._id_seleccionado())

    def _on_boton_principal(self) -> None:
        if self._tab_ausencias is not None and self._tab_ausencias.winfo_ismapped():
            self._tab_ausencias.nuevo()
        else:
            self._on_nuevo()

    def _id_seleccionado(self) -> Optional[int]:
        iid = self._tabla.seleccionado()
        return int(iid) if iid is not None else None

    # ── Datos ─────────────────────────────────────────────────────────────

    def _refrescar(self, seleccionar: Optional[int] = None) -> None:
        try:
            self._turnos = self._controller.list_turnos(solo_activos=False)
            self._empleados = self._controller.empleados_por_turno()
        except Exception:  # noqa: BLE001 — último recurso, log + aviso
            self._log.exception("Error listando turnos")
            messagebox.showerror("Error", "No se pudo cargar la lista de turnos.")
            return
        self._render()
        if seleccionar is not None:
            self._tabla.seleccionar(str(seleccionar))
        self._mostrar_detalle()

    def _render(self) -> None:
        activos = [t for t in self._turnos if t.is_active]
        archivados = [t for t in self._turnos if not t.is_active]
        con_turno = {e.id for lista in self._empleados.values() for e in lista}
        visibles = activos if self._filtro == _ACTIVOS else archivados
        filas: List[Fila] = []
        for t in visibles:
            if t.id is None:
                continue
            semana = formato.minutos_por_semana(t.bloques())
            cantidad = len(self._empleados.get(t.id, []))
            tags = ("archivado",) if not t.is_active else (() if cantidad else ("sin_empleados",))
            filas.append(
                Fila(
                    iid=str(t.id),
                    valores={
                        "nombre": t.nombre,
                        "horario": formato.horario_texto(t),
                        "dias": formato.dias_texto(t.dias_totales),
                        "semana": formato.duracion(semana),
                        "empleados": str(cantidad),
                    },
                    orden={"semana": semana, "empleados": cantidad, "horario": t.hora_entrada},
                    tags=tags,
                )
            )
        self._tabla.set_filas(filas)
        self._kpis.actualizar(
            {
                _ACTIVOS: str(len(activos)),
                _ARCHIVADOS: str(len(archivados)),
                _ASIGNADOS: str(len(con_turno)),
            },
            activa=self._filtro,
        )

    def _set_filtro(self, clave: str) -> None:
        self._filtro = clave
        self._render()
        self._mostrar_detalle()

    def _seleccionado(self) -> Optional[Turno]:
        iid = self._tabla.seleccionado()
        if iid is None:
            return None
        return next((t for t in self._turnos if t.id == int(iid)), None)

    # ── Panel de detalle ──────────────────────────────────────────────────

    def _mostrar_detalle(self) -> None:
        panel = self._detalle
        piezas.limpiar(panel)
        t = self._seleccionado()
        if t is None or t.id is None:
            piezas.detalle_vacio(panel, "Seleccione un turno\npara ver su detalle y acciones.")
            return
        turno_id = t.id

        ctk.CTkLabel(
            panel,
            text=t.nombre,
            font=theme.fuente(theme.TAM_SECCION + 1, "bold"),
            anchor="w",
            justify="left",
            wraplength=280,
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(18, 0))
        bloques = t.bloques()
        if len(bloques) == 1:
            ctk.CTkLabel(
                panel,
                text=f"{t.hora_entrada} – {t.hora_salida}",
                font=theme.fuente(26, "bold"),
                text_color=theme.PRIMARIO,
                anchor="w",
            ).grid(row=1, column=0, sticky="w", padx=16, pady=(6, 0))
        else:
            # Un renglón por horario: "Lun a Vie   09:00 – 17:00".
            horarios = ctk.CTkFrame(panel, fg_color="transparent")
            horarios.grid(row=1, column=0, sticky="ew", padx=16, pady=(8, 0))
            horarios.grid_columnconfigure(1, weight=1)
            for i, h in enumerate(bloques):
                ctk.CTkLabel(
                    horarios,
                    text=formato.dias_texto(h.dias_semana),
                    font=theme.fuente(theme.TAM_NORMAL),
                    text_color=theme.TEXTO_SUAVE,
                    anchor="w",
                ).grid(row=i, column=0, sticky="w", pady=1)
                ctk.CTkLabel(
                    horarios,
                    text=f"{h.hora_entrada} – {h.hora_salida}",
                    font=theme.fuente(19, "bold"),
                    text_color=theme.PRIMARIO,
                    anchor="e",
                ).grid(row=i, column=1, sticky="e", pady=1)
        piezas.chips_dias(panel, t.dias_totales).grid(
            row=2, column=0, sticky="w", padx=16, pady=(10, 0)
        )
        insignias = [
            (
                ("Activo", theme.EXITO, theme.EXITO_FONDO)
                if t.is_active
                else ("Archivado", theme.NEUTRO, theme.NEUTRO_FONDO)
            )
        ]
        if t.cruza_medianoche:
            insignias.append(("Cruza medianoche", theme.ALERTA, theme.ALERTA_FONDO))
        fila = piezas.fila_insignias(panel, 3, insignias)

        fila = piezas.titulo_seccion(panel, fila, "Acciones", arriba=14)
        if t.is_active:
            fila = piezas.boton_accion(
                panel, fila, "Editar turno", lambda: self._on_editar(t), primario=True
            )
            fila = piezas.boton_accion(
                panel, fila, "Archivar turno", lambda: self._on_archivar(t), peligro=True
            )
        else:
            fila = piezas.boton_accion(
                panel, fila, "Reactivar turno", lambda: self._on_reactivar(t), primario=True
            )

        fila = piezas.titulo_seccion(panel, fila, "Horario", arriba=12)
        lineas = [("Días", formato.dias_texto(t.dias_totales))]
        for h in bloques:
            jornada = formato.horas_efectivas(h.hora_entrada, h.hora_salida, h.minutos_descanso)
            etiqueta = "Jornada" if len(bloques) == 1 else formato.dias_corto(h.dias_semana)
            lineas.append((etiqueta, f"{formato.duracion(jornada)}{formato.almuerzo_texto(h)}"))
        lineas += [
            ("Por semana", formato.duracion(formato.minutos_por_semana(bloques))),
            ("Gracia al entrar", formato.duracion(t.minutos_tolerancia_entrada)),
            ("Gracia al salir", formato.duracion(t.minutos_tolerancia_salida)),
        ]
        fila = piezas.bloque_datos(panel, fila, lineas)

        empleados = sorted(
            self._empleados.get(turno_id, []), key=lambda e: (e.apellidos, e.nombres)
        )
        fila = piezas.titulo_seccion(panel, fila, f"Empleados con este turno ({len(empleados)})")
        if not empleados:
            piezas.texto_suave(panel, fila, "Ningún empleado tiene este turno asignado.")
            return
        lista = ctk.CTkFrame(panel, fg_color="transparent")
        lista.grid(row=fila, column=0, sticky="ew", padx=16, pady=(0, 16))
        for i, emp in enumerate(empleados[:_MAX_EMPLEADOS_EN_PANEL]):
            ctk.CTkFrame(lista, width=6, height=6, corner_radius=3, fg_color=theme.PRIMARIO).grid(
                row=i, column=0, padx=(2, 10), pady=5
            )
            ctk.CTkLabel(
                lista,
                text=f"{emp.nombres} {emp.apellidos}",
                font=theme.fuente(theme.TAM_PEQUENO),
                anchor="w",
                height=18,
            ).grid(row=i, column=1, sticky="w")
        restantes = len(empleados) - _MAX_EMPLEADOS_EN_PANEL
        if restantes > 0:
            ctk.CTkLabel(
                lista,
                text=f"y {restantes} más…",
                font=theme.fuente(theme.TAM_PEQUENO),
                text_color=theme.TEXTO_SUAVE,
                anchor="w",
            ).grid(row=_MAX_EMPLEADOS_EN_PANEL, column=1, sticky="w", pady=(2, 0))

    # ── Acciones ──────────────────────────────────────────────────────────

    def _doble_click(self, iid: str) -> None:
        turno = next((t for t in self._turnos if t.id == int(iid)), None)
        if turno is not None and turno.is_active:
            self._on_editar(turno)

    def _on_nuevo(self) -> None:
        antes = {t.id for t in self._turnos}
        dialog = TurnoFormDialog(self, title="Nuevo turno", on_submit=self._handle_create)
        self.wait_window(dialog)
        self._filtro = _ACTIVOS
        self._refrescar()
        nuevo = next((t for t in self._turnos if t.id not in antes), None)
        if nuevo is not None and nuevo.id is not None:
            self._tabla.seleccionar(str(nuevo.id))
            self._mostrar_detalle()
            self._aviso.mostrar(f"Turno «{nuevo.nombre}» creado.")

    def _on_editar(self, turno: Turno) -> None:
        assert turno.id is not None
        turno_id = turno.id
        guardado = {"ok": False}

        def submit(payload: TurnoFormPayload) -> Optional[str]:
            error = self._handle_update(turno_id, payload)
            guardado["ok"] = error is None
            return error

        dialog = TurnoFormDialog(
            self,
            title=f"Editar turno — {turno.nombre}",
            on_submit=submit,
            initial_nombre=turno.nombre,
            initial_hora_entrada=turno.hora_entrada,
            initial_hora_salida=turno.hora_salida,
            initial_minutos_descanso=turno.minutos_descanso,
            initial_dias_semana=turno.dias_semana,
            initial_horarios_extra=turno.horarios_extra,
            initial_almuerzo_inicio=turno.almuerzo_inicio,
            initial_almuerzo_fin=turno.almuerzo_fin,
        )
        self._conflictos_resueltos = 0
        self.wait_window(dialog)
        self._refrescar(seleccionar=turno_id)
        if guardado["ok"]:
            n = self._conflictos_resueltos
            extra = (
                f" Se quitó el turno que se cruzaba a {n} empleado{'s' if n != 1 else ''}."
                if n
                else ""
            )
            self._aviso.mostrar(f"Cambios guardados.{extra}")

    def _on_archivar(self, turno: Turno) -> None:
        assert turno.id is not None
        asignados = len(self._empleados.get(turno.id, []))
        aviso = (
            f"\n\n{asignados} empleado(s) lo tienen asignado: conservan la asignación, "
            "pero el turno ya no se podrá elegir para nuevos empleados."
            if asignados
            else "\n\nYa no se podrá elegir para nuevos empleados."
        )
        if not messagebox.askyesno("Archivar turno", f"¿Archivar «{turno.nombre}»?{aviso}"):
            return
        try:
            self._controller.archive_turno(turno.id)
        except TurnoNotFoundError as exc:
            messagebox.showerror("Turno no encontrado", str(exc))
            return
        except Exception:  # noqa: BLE001
            self._log.exception("Error archivando turno id=%s", turno.id)
            messagebox.showerror("Error", "No se pudo archivar el turno.")
            return
        self._refrescar()
        self._aviso.mostrar(f"«{turno.nombre}» archivado. Lo encuentra en «Archivados».")

    def _on_reactivar(self, turno: Turno) -> None:
        assert turno.id is not None
        try:
            self._controller.unarchive_turno(turno.id)
        except TurnoNotFoundError as exc:
            messagebox.showerror("Turno no encontrado", str(exc))
            return
        except Exception:  # noqa: BLE001
            self._log.exception("Error reactivando turno id=%s", turno.id)
            messagebox.showerror("Error", "No se pudo reactivar el turno.")
            return
        self._filtro = _ACTIVOS
        self._refrescar(seleccionar=turno.id)
        self._aviso.mostrar(f"«{turno.nombre}» vuelve a estar activo.")

    # ── Callbacks del diálogo ─────────────────────────────────────────────

    def _handle_create(self, payload: TurnoFormPayload) -> Optional[str]:
        try:
            self._controller.create_turno(**payload)
        except _ERRORES_TURNO as exc:
            return str(exc)
        except Exception:  # noqa: BLE001
            self._log.exception("Error creando turno")
            return "Ocurrió un error inesperado al crear el turno."
        return None

    def _handle_update(self, turno_id: int, payload: TurnoFormPayload) -> Optional[str]:
        try:
            try:
                self._controller.update_turno(turno_id=turno_id, **payload)
            except TurnoConflictoEmpleadosError as conflicto:
                # P. ej. se agregó el sábado y esos empleados ya tienen aparte
                # un turno de sábado: se ofrece quitárselo y guardar.
                otros = ", ".join(f"«{t}»" for t in conflicto.turnos)
                uno = len(conflicto.empleados) == 1
                if not messagebox.askyesno(
                    "Empleados con otro turno esos días",
                    f"{conflicto}\n\n¿{'Quitarle' if uno else 'Quitarles'} {otros} y guardar? "
                    f"Desde hoy {'usará' if uno else 'usarán'} el horario de este turno. "
                    "La asistencia anterior no cambia.",
                    # Encima del formulario abierto (que tiene el foco modal).
                    parent=self.grab_current() or self.winfo_toplevel(),
                ):
                    return "No se guardó: primero resuelva los turnos que se cruzan."
                cerradas = self._controller.update_turno(
                    turno_id=turno_id, quitar_conflictos=True, **payload
                )
                self._conflictos_resueltos = cerradas
        except _ERRORES_TURNO as exc:
            return str(exc)
        except Exception:  # noqa: BLE001
            self._log.exception("Error actualizando turno id=%s", turno_id)
            return "Ocurrió un error inesperado al guardar el turno."
        return None
