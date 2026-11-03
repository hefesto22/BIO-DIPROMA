"""Pestaña "Cargos" de Configuración.

Cada cargo puede ser propio de un departamento (solo aparece al registrar
empleados de ese departamento) o servir para cualquiera. Tabla con
departamento y empleados activos; el panel muestra quién tiene el cargo y
permite editar, archivar (pasando antes sus empleados a otro cargo, en un
solo paso) o reactivar.
"""

from __future__ import annotations

from tkinter import messagebox
from typing import Dict, List, Optional, Tuple

import customtkinter as ctk

from core.models.cargo import Cargo
from core.models.departamento import Departamento
from core.models.empleado import Empleado
from core.services.errors import CatalogoError, MissingRequiredFieldError
from ui import theme
from ui.components import piezas
from ui.components.config_dialogos import CargoDialog, PasarYArchivarDialog
from ui.components.data_table import Columna, Fila
from ui.components.lista_detalle import ListaDetalle
from ui.controllers.configuracion_controller import ConfiguracionController

_ACTIVOS = "ACTIVOS"
_ARCHIVADOS = "ARCHIVADOS"
_GENERALES = "GENERALES"
_TODOS_DEPTOS = "Todos los departamentos"
_CUALQUIERA = "Cualquier departamento"

_COLUMNAS = (
    Columna("nombre", "Cargo", ancho=260),
    Columna("depto", "Departamento", ancho=200),
    Columna("empleados", "Empleados", ancho=120, alinear="center"),
)

_ERRORES = (CatalogoError, MissingRequiredFieldError)


def _plural(n: int, uno: str, varios: str) -> str:
    return f"1 {uno}" if n == 1 else f"{n} {varios}"


class CargosTab(ListaDetalle):
    texto_sin_seleccion = "Seleccione un cargo\npara ver quién lo tiene y sus acciones."

    def __init__(self, master: ctk.CTkBaseClass, controller: ConfiguracionController) -> None:
        self._c = controller
        self._cargos: List[Cargo] = []
        self._deptos: Dict[int, Departamento] = {}
        self._empleados: Dict[int, int] = {}
        self._personal: List[Empleado] = []
        super().__init__(
            master,
            _COLUMNAS,
            (
                (_ACTIVOS, "Cargos activos", theme.PRIMARIO, True),
                (_ARCHIVADOS, "Archivados", theme.NEUTRO, True),
                (_GENERALES, "Para cualquier departamento", theme.EXITO, False),
            ),
            placeholder_busqueda="Buscar cargo…",
            texto_vacio="No hay cargos en esta lista.",
            filtro_inicial=_ACTIVOS,
        )
        self._menu_depto = piezas.menu_filtro(self.barra, [_TODOS_DEPTOS], lambda _v: self.render())
        self._menu_depto.grid(row=0, column=1, padx=(10, 0))
        self.refrescar()

    # ── Datos ─────────────────────────────────────────────────────────────

    def _cargar(self) -> None:
        self._cargos = self._c.list_cargos(solo_activos=False)
        self._deptos = {
            d.id: d for d in self._c.list_departamentos(solo_activos=False) if d.id is not None
        }
        self._personal = self._c.empleados_activos()
        self._empleados = {}
        for e in self._personal:
            self._empleados[e.cargo_id] = self._empleados.get(e.cargo_id, 0) + 1
        actual = str(self._menu_depto.get())
        nombres = sorted((d.nombre for d in self._deptos.values() if d.is_active), key=str.casefold)
        opciones = [_TODOS_DEPTOS, _CUALQUIERA, *nombres]
        self._menu_depto.configure(values=opciones)
        self._menu_depto.set(actual if actual in opciones else _TODOS_DEPTOS)

    def _nombre_depto(self, c: Cargo) -> str:
        if c.departamento_id is None:
            return _CUALQUIERA
        d = self._deptos.get(c.departamento_id)
        return d.nombre if d is not None else "—"

    def _filas(self) -> Tuple[List[Fila], Dict[str, str]]:
        texto = self.texto_busqueda()
        filtro_depto = str(self._menu_depto.get())
        activos = [c for c in self._cargos if c.is_active]
        filas: List[Fila] = []
        for c in self._cargos:
            if c.id is None or c.is_active != (self.filtro == _ACTIVOS):
                continue
            depto = self._nombre_depto(c)
            if filtro_depto != _TODOS_DEPTOS and depto != filtro_depto:
                continue
            if texto and texto not in c.nombre.casefold() and texto not in depto.casefold():
                continue
            empleados = self._empleados.get(c.id, 0)
            filas.append(
                Fila(
                    iid=str(c.id),
                    valores={"nombre": c.nombre, "depto": depto, "empleados": str(empleados)},
                    orden={
                        "nombre": c.nombre.casefold(),
                        "depto": depto.casefold(),
                        "empleados": empleados,
                    },
                    tags=() if c.is_active else ("apagado",),
                )
            )
        filas.sort(key=lambda f: (str(f.orden["depto"]), str(f.orden["nombre"])))
        return filas, {
            _ACTIVOS: str(len(activos)),
            _ARCHIVADOS: str(len(self._cargos) - len(activos)),
            _GENERALES: str(sum(1 for c in activos if c.departamento_id is None)),
        }

    def _buscar(self, iid: str) -> Optional[Cargo]:
        return next((c for c in self._cargos if c.id == int(iid)), None)

    # ── Panel ─────────────────────────────────────────────────────────────

    def _detalle(self, panel: ctk.CTkScrollableFrame, iid: str) -> None:
        c = self._buscar(iid)
        if c is None or c.id is None:
            return
        empleados = self._empleados.get(c.id, 0)
        ctk.CTkLabel(
            panel,
            text=c.nombre,
            font=theme.fuente(theme.TAM_SECCION + 2, "bold"),
            anchor="w",
            justify="left",
            wraplength=280,
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(18, 0))
        ctk.CTkLabel(
            panel,
            text=self._nombre_depto(c),
            font=theme.fuente(theme.TAM_PEQUENO),
            text_color=theme.TEXTO_SUAVE,
            anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=16)
        estado = (
            ("Activo", theme.EXITO, theme.EXITO_FONDO)
            if c.is_active
            else ("Archivado", theme.NEUTRO, theme.NEUTRO_FONDO)
        )
        fila = piezas.fila_insignias(panel, 2, [estado])

        fila = piezas.titulo_seccion(panel, fila, "Acciones", arriba=14)
        if c.is_active:
            fila = piezas.boton_accion(
                panel, fila, "Editar", lambda: self._editar(c), primario=True
            )
            fila = piezas.boton_accion(
                panel,
                fila,
                "Archivar…" if empleados else "Archivar",
                lambda: self._archivar(c),
                peligro=True,
            )
        else:
            fila = piezas.boton_accion(
                panel, fila, "Reactivar", lambda: self._reactivar(c), primario=True
            )

        personal = sorted(
            (e for e in self._personal if e.cargo_id == c.id),
            key=lambda e: (e.nombres.casefold(), e.apellidos.casefold()),
        )
        general = c.departamento_id is None
        fila = piezas.titulo_seccion(panel, fila, f"Quién lo tiene ({len(personal)})", arriba=12)
        if personal:
            fila = piezas.lista_items(
                panel,
                fila,
                # El departamento solo aporta si el cargo es de cualquiera.
                [
                    (f"{e.nombres} {e.apellidos}", self._depto_de(e) if general else "")
                    for e in personal
                ],
                debajo=general,
            )
        else:
            fila = piezas.texto_suave(
                panel, fila, "Ningún empleado activo tiene este cargo.", abajo=4
            )
        piezas.texto_suave(
            panel,
            fila,
            (
                "Aparece al registrar empleados de cualquier departamento."
                if c.departamento_id is None
                else f"Solo aparece al registrar empleados de {self._nombre_depto(c)}."
            ),
        )

    def _depto_de(self, e: Empleado) -> str:
        d = self._deptos.get(e.departamento_id)
        return d.nombre if d is not None else ""

    # ── Acciones ──────────────────────────────────────────────────────────

    def _deptos_activos(self) -> List[Tuple[int, str]]:
        return sorted(
            ((i, d.nombre) for i, d in self._deptos.items() if d.is_active),
            key=lambda t: t[1].casefold(),
        )

    def _doble_click(self, iid: str) -> None:
        c = self._buscar(iid)
        if c is not None and c.is_active:
            self._editar(c)

    def _suprimir(self, iid: str) -> None:
        c = self._buscar(iid)
        if c is not None and c.is_active:
            self._archivar(c)

    def nuevo(self) -> None:
        creados: List[Cargo] = []

        def submit(nombre: str, depto_id: Optional[int]) -> Optional[str]:
            try:
                creados.append(self._c.create_cargo(nombre, departamento_id=depto_id))
            except _ERRORES as exc:
                return str(exc)
            return None

        # Si la lista está filtrada por un departamento, se propone ese.
        filtro_depto = str(self._menu_depto.get())
        inicial = next(
            (i for i, d in self._deptos.items() if d.is_active and d.nombre == filtro_depto), None
        )
        self.wait_window(
            CargoDialog(
                self,
                titulo="Nuevo cargo",
                departamentos=self._deptos_activos(),
                on_submit=submit,
                inicial_departamento_id=inicial,
                texto_ok="Crear cargo",
                permitir_varios=True,
            )
        )
        if creados:
            self.filtro = _ACTIVOS
            self.refrescar(seleccionar=str(creados[-1].id))
            if len(creados) == 1:
                self.aviso.mostrar(f"Cargo «{creados[0].nombre}» creado.")
            else:
                self.aviso.mostrar(f"{len(creados)} cargos creados.")

    def _editar(self, c: Cargo) -> None:
        assert c.id is not None
        cargo_id = c.id
        hecho = {"ok": False}

        def submit(nombre: str, depto_id: Optional[int]) -> Optional[str]:
            # Primero el departamento: es lo que más puede rechazarse, y así un
            # error no deja el nombre cambiado a medias. Ambos son no-op si no
            # cambian, por eso se puede reintentar sin problema.
            try:
                if depto_id != c.departamento_id:
                    self._c.set_cargo_departamento(cargo_id, depto_id)
                    hecho["ok"] = True
                if nombre != c.nombre:
                    self._c.rename_cargo(cargo_id, nombre)
                    hecho["ok"] = True
            except _ERRORES as exc:
                return str(exc)
            hecho["ok"] = True
            return None

        self.wait_window(
            CargoDialog(
                self,
                titulo="Editar cargo",
                departamentos=self._deptos_activos(),
                on_submit=submit,
                inicial_nombre=c.nombre,
                inicial_departamento_id=c.departamento_id,
            )
        )
        if hecho["ok"]:
            self.refrescar(seleccionar=str(cargo_id))
            self.aviso.mostrar("Cambios guardados.")

    def _archivar(self, c: Cargo) -> None:
        assert c.id is not None
        empleados = self._empleados.get(c.id, 0)
        if empleados:
            self._pasar_y_archivar(c, empleados)
            return
        if not messagebox.askyesno(
            "Archivar cargo",
            f"¿Archivar «{c.nombre}»?\n\nYa no se podrá elegir al registrar empleados.",
            parent=self,
        ):
            return
        try:
            self._c.archive_cargo(c.id)
        except _ERRORES as exc:
            self.aviso.mostrar(str(exc), error=True)
            return
        self.refrescar()
        self.aviso.mostrar(f"«{c.nombre}» archivado. Lo encuentra en «Archivados».")

    def _pasar_y_archivar(self, c: Cargo, empleados: int) -> None:
        assert c.id is not None
        origen_id = c.id
        try:
            destinos = self._c.cargos_destino_para(origen_id)
        except _ERRORES as exc:
            self.aviso.mostrar(str(exc), error=True)
            return
        opciones = sorted(
            (
                (d.id, f"{d.nombre}  ·  {self._nombre_depto(d)}")
                for d in destinos
                if d.id is not None
            ),
            key=lambda t: t[1].casefold(),
        )
        if not opciones:
            self.aviso.mostrar(
                "No hay otro cargo al que puedan pasar sus empleados. Cree uno primero.",
                error=True,
            )
            return
        resultado: Dict[str, Tuple[int, int, str]] = {}

        def submit(destino_id: int) -> Optional[str]:
            try:
                r = self._c.archive_cargo_moviendo(origen_id, destino_id)
            except _ERRORES as exc:
                return str(exc)
            destino = next((d.nombre for d in destinos if d.id == destino_id), "")
            resultado["r"] = (r.empleados, destino_id, destino)
            return None

        self.wait_window(
            PasarYArchivarDialog(
                self,
                titulo=f"Archivar «{c.nombre}»",
                explicacion=(
                    (
                        "Lo tiene 1 empleado activo"
                        if empleados == 1
                        else f"Lo tienen {empleados} " "empleados activos"
                    )
                    + ". Elija qué cargo pasan a tener; después se archiva."
                ),
                etiqueta="Nuevo cargo",
                opciones=opciones,
                on_submit=submit,
                detalle=(
                    "Solo aparecen los cargos que sirven para el departamento de cada "
                    "empleado. Útil también para unir cargos repetidos."
                ),
            )
        )
        if "r" in resultado:
            movidos, destino_id, destino = resultado["r"]
            verbo = "pasó" if movidos == 1 else "pasaron"
            self.refrescar(seleccionar=str(destino_id))
            self.aviso.mostrar(
                f"«{c.nombre}» archivado: {_plural(movidos, 'empleado', 'empleados')} "
                f"{verbo} a «{destino}»."
            )

    def _reactivar(self, c: Cargo) -> None:
        assert c.id is not None
        try:
            self._c.unarchive_cargo(c.id)
        except _ERRORES as exc:
            self.aviso.mostrar(str(exc), error=True)
            return
        self.filtro = _ACTIVOS
        self.refrescar(seleccionar=str(c.id))
        self.aviso.mostrar(f"«{c.nombre}» vuelve a estar activo.")
