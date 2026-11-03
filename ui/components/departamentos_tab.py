"""Pestaña "Departamentos" de Configuración.

Tabla con cuántos cargos propios y empleados activos tiene cada
departamento. El panel muestra sus cargos y su personal, y permite:

    - Renombrar.
    - Agregar cargos al departamento sin salir de aquí.
    - Archivar. Si todavía tiene empleados, se elige a qué departamento
      pasan (junto con sus cargos propios) y se archiva en un solo paso.
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
from ui.components.config_dialogos import CargoDialog, NombreDialog, PasarYArchivarDialog
from ui.components.data_table import Columna, Fila
from ui.components.lista_detalle import ListaDetalle
from ui.controllers.configuracion_controller import ConfiguracionController

_ACTIVOS = "ACTIVOS"
_ARCHIVADOS = "ARCHIVADOS"
_EMPLEADOS = "EMPLEADOS"

_COLUMNAS = (
    Columna("nombre", "Departamento", ancho=280),
    Columna("cargos", "Cargos propios", ancho=120, alinear="center"),
    Columna("empleados", "Empleados activos", ancho=140, alinear="center"),
)

_ERRORES = (CatalogoError, MissingRequiredFieldError)


def _plural(n: int, uno: str, varios: str) -> str:
    return f"1 {uno}" if n == 1 else f"{n} {varios}"


class DepartamentosTab(ListaDetalle):
    texto_sin_seleccion = "Seleccione un departamento\npara ver su personal y acciones."

    def __init__(self, master: ctk.CTkBaseClass, controller: ConfiguracionController) -> None:
        self._c = controller
        self._deptos: List[Departamento] = []
        self._cargos: Dict[int, Cargo] = {}
        self._empleados: List[Empleado] = []
        super().__init__(
            master,
            _COLUMNAS,
            (
                (_ACTIVOS, "Departamentos activos", theme.PRIMARIO, True),
                (_ARCHIVADOS, "Archivados", theme.NEUTRO, True),
                (_EMPLEADOS, "Empleados activos", theme.EXITO, False),
            ),
            placeholder_busqueda="Buscar departamento…",
            texto_vacio="No hay departamentos en esta lista.",
            filtro_inicial=_ACTIVOS,
        )
        self.refrescar()

    # ── Datos ─────────────────────────────────────────────────────────────

    def _cargar(self) -> None:
        self._deptos = self._c.list_departamentos(solo_activos=False)
        self._cargos = {c.id: c for c in self._c.list_cargos(solo_activos=True) if c.id is not None}
        self._empleados = self._c.empleados_activos()

    def _cargos_de(self, depto_id: int) -> List[Cargo]:
        return sorted(
            (c for c in self._cargos.values() if c.departamento_id == depto_id),
            key=lambda c: c.nombre.casefold(),
        )

    def _empleados_de(self, depto_id: int) -> List[Empleado]:
        return sorted(
            (e for e in self._empleados if e.departamento_id == depto_id),
            key=lambda e: (e.nombres.casefold(), e.apellidos.casefold()),
        )

    def _filas(self) -> Tuple[List[Fila], Dict[str, str]]:
        texto = self.texto_busqueda()
        activos = [d for d in self._deptos if d.is_active]
        filas: List[Fila] = []
        for d in self._deptos:
            if d.id is None or d.is_active != (self.filtro == _ACTIVOS):
                continue
            if texto and texto not in d.nombre.casefold():
                continue
            cargos = len(self._cargos_de(d.id))
            empleados = len(self._empleados_de(d.id))
            filas.append(
                Fila(
                    iid=str(d.id),
                    valores={
                        "nombre": d.nombre,
                        "cargos": str(cargos),
                        "empleados": str(empleados),
                    },
                    orden={"nombre": d.nombre.casefold(), "cargos": cargos, "empleados": empleados},
                    tags=() if d.is_active else ("apagado",),
                )
            )
        filas.sort(key=lambda f: str(f.orden["nombre"]))
        return filas, {
            _ACTIVOS: str(len(activos)),
            _ARCHIVADOS: str(len(self._deptos) - len(activos)),
            _EMPLEADOS: str(len(self._empleados)),
        }

    def _buscar(self, iid: str) -> Optional[Departamento]:
        return next((d for d in self._deptos if d.id == int(iid)), None)

    def _otros_activos(self, d: Departamento) -> List[Tuple[int, str]]:
        """Departamentos activos (menos ``d``) como ``(id, nombre)``, por nombre."""
        otros = [
            (o.id, o.nombre)
            for o in self._deptos
            if o.is_active and o.id is not None and o.id != d.id
        ]
        return sorted(otros, key=lambda t: t[1].casefold())

    def _activos(self) -> List[Tuple[int, str]]:
        activos = [(o.id, o.nombre) for o in self._deptos if o.is_active and o.id is not None]
        return sorted(activos, key=lambda t: t[1].casefold())

    # ── Panel ─────────────────────────────────────────────────────────────

    def _detalle(self, panel: ctk.CTkScrollableFrame, iid: str) -> None:
        d = self._buscar(iid)
        if d is None or d.id is None:
            return
        empleados = self._empleados_de(d.id)
        cargos = self._cargos_de(d.id)

        ctk.CTkLabel(
            panel,
            text=d.nombre,
            font=theme.fuente(theme.TAM_SECCION + 2, "bold"),
            anchor="w",
            justify="left",
            wraplength=280,
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(18, 0))
        estado = (
            ("Activo", theme.EXITO, theme.EXITO_FONDO)
            if d.is_active
            else ("Archivado", theme.NEUTRO, theme.NEUTRO_FONDO)
        )
        fila = piezas.fila_insignias(panel, 1, [estado])

        fila = piezas.titulo_seccion(panel, fila, "Acciones", arriba=14)
        if d.is_active:
            fila = piezas.boton_accion(
                panel, fila, "Renombrar", lambda: self._renombrar(d), primario=True
            )
            fila = piezas.boton_accion(
                panel, fila, "+  Agregar cargos aquí", lambda: self._agregar_cargos(d)
            )
            motivo = None
            if empleados and not self._otros_activos(d):
                motivo = "No hay otro departamento activo al cual pasar a sus empleados."
            fila = piezas.boton_accion(
                panel,
                fila,
                "Archivar…" if empleados else "Archivar",
                lambda: self._archivar(d),
                peligro=True,
                motivo_deshabilitado=motivo,
            )
        else:
            fila = piezas.boton_accion(
                panel, fila, "Reactivar", lambda: self._reactivar(d), primario=True
            )

        fila = piezas.titulo_seccion(panel, fila, f"Cargos propios ({len(cargos)})", arriba=12)
        if cargos:
            por_cargo: Dict[int, int] = {}
            for e in empleados:
                por_cargo[e.cargo_id] = por_cargo.get(e.cargo_id, 0) + 1
            fila = piezas.lista_items(
                panel,
                fila,
                [(c.nombre, str(por_cargo.get(c.id or 0, 0))) for c in cargos],
            )
        else:
            fila = piezas.texto_suave(
                panel,
                fila,
                "No tiene cargos propios: sus empleados usan los de «Cualquier departamento».",
                abajo=4,
            )

        fila = piezas.titulo_seccion(panel, fila, f"Empleados activos ({len(empleados)})")
        if not empleados:
            piezas.texto_suave(panel, fila, "Nadie trabaja aquí por ahora.")
            return
        piezas.lista_items(
            panel,
            fila,
            [(f"{e.nombres} {e.apellidos}", self._nombre_cargo(e.cargo_id)) for e in empleados],
            debajo=True,
        )
        ctk.CTkFrame(panel, height=12, fg_color="transparent").grid(row=fila + 1, column=0)

    def _nombre_cargo(self, cargo_id: int) -> str:
        cargo = self._cargos.get(cargo_id)
        return cargo.nombre if cargo is not None else "Cargo archivado"

    # ── Acciones ──────────────────────────────────────────────────────────

    def _doble_click(self, iid: str) -> None:
        d = self._buscar(iid)
        if d is not None and d.is_active:
            self._renombrar(d)

    def _suprimir(self, iid: str) -> None:
        d = self._buscar(iid)
        if d is not None and d.is_active:
            self._archivar(d)

    def nuevo(self) -> None:
        creados: List[Departamento] = []

        def submit(nombre: str) -> Optional[str]:
            try:
                creados.append(self._c.create_departamento(nombre))
            except _ERRORES as exc:
                return str(exc)
            return None

        self.wait_window(
            NombreDialog(
                self,
                titulo="Nuevo departamento",
                subtitulo="Área de la empresa a la que pertenecen los empleados.",
                etiqueta="Nombre",
                placeholder="ej. Contabilidad",
                on_submit=submit,
                texto_ok="Crear departamento",
                permitir_varios=True,
            )
        )
        if creados:
            self.filtro = _ACTIVOS
            self.refrescar(seleccionar=str(creados[-1].id))
            if len(creados) == 1:
                self.aviso.mostrar(f"Departamento «{creados[0].nombre}» creado.")
            else:
                self.aviso.mostrar(f"{len(creados)} departamentos creados.")

    def _agregar_cargos(self, d: Departamento) -> None:
        assert d.id is not None
        creados: List[Cargo] = []

        def submit(nombre: str, depto_id: Optional[int]) -> Optional[str]:
            try:
                creados.append(self._c.create_cargo(nombre, departamento_id=depto_id))
            except _ERRORES as exc:
                return str(exc)
            return None

        self.wait_window(
            CargoDialog(
                self,
                titulo=f"Nuevo cargo en {d.nombre}",
                departamentos=self._activos(),
                on_submit=submit,
                inicial_departamento_id=d.id,
                texto_ok="Crear cargo",
                permitir_varios=True,
            )
        )
        if creados:
            self.refrescar(seleccionar=str(d.id))
            nombres = ", ".join(f"«{c.nombre}»" for c in creados[:3])
            extra = f" y {len(creados) - 3} más" if len(creados) > 3 else ""
            self.aviso.mostrar(
                f"{_plural(len(creados), 'cargo creado', 'cargos creados')}: {nombres}{extra}."
            )

    def _renombrar(self, d: Departamento) -> None:
        assert d.id is not None
        depto_id = d.id
        hecho = {"ok": False}

        def submit(nombre: str) -> Optional[str]:
            if nombre == d.nombre:
                return None
            try:
                self._c.rename_departamento(depto_id, nombre)
            except _ERRORES as exc:
                return str(exc)
            hecho["ok"] = True
            return None

        self.wait_window(
            NombreDialog(
                self,
                titulo="Renombrar departamento",
                subtitulo="El cambio se ve en todas las pantallas y reportes.",
                etiqueta="Nombre",
                inicial=d.nombre,
                on_submit=submit,
            )
        )
        if hecho["ok"]:
            self.refrescar(seleccionar=str(depto_id))
            self.aviso.mostrar("Nombre actualizado.")

    def _archivar(self, d: Departamento) -> None:
        assert d.id is not None
        empleados = self._empleados_de(d.id)
        if empleados:
            self._pasar_y_archivar(d, len(empleados))
            return
        if not messagebox.askyesno(
            "Archivar departamento",
            f"¿Archivar «{d.nombre}»?\n\nYa no se podrá elegir al registrar empleados. "
            "Lo puede reactivar cuando quiera.",
            parent=self,
        ):
            return
        try:
            self._c.archive_departamento(d.id)
        except _ERRORES as exc:
            self.aviso.mostrar(str(exc), error=True)
            return
        self.refrescar()
        self.aviso.mostrar(f"«{d.nombre}» archivado. Lo encuentra en «Archivados».")

    def _pasar_y_archivar(self, d: Departamento, empleados: int) -> None:
        assert d.id is not None
        origen_id = d.id
        opciones = self._otros_activos(d)
        if not opciones:
            self.aviso.mostrar(
                "No hay otro departamento activo al cual pasar a sus empleados.", error=True
            )
            return
        cargos = self._cargos_de(origen_id)
        detalle = (
            f"Sus cargos propios ({', '.join(c.nombre for c in cargos)}) también pasan a ese "
            "departamento, así nadie se queda sin cargo."
            if cargos
            else "Cada empleado conserva su cargo."
        )
        resultado: Dict[str, Tuple[int, int, int, str]] = {}

        def submit(destino_id: int) -> Optional[str]:
            try:
                r = self._c.archive_departamento_moviendo(origen_id, destino_id)
            except _ERRORES as exc:
                return str(exc)
            nombre = next((n for i, n in opciones if i == destino_id), "")
            resultado["r"] = (r.empleados, r.cargos, destino_id, nombre)
            return None

        self.wait_window(
            PasarYArchivarDialog(
                self,
                titulo=f"Archivar «{d.nombre}»",
                explicacion=(
                    f"Tiene {_plural(empleados, 'empleado activo', 'empleados activos')}. "
                    "Elija a qué departamento pasan; después se archiva."
                ),
                etiqueta="Pasar a",
                opciones=opciones,
                on_submit=submit,
                detalle=detalle,
            )
        )
        if "r" not in resultado:
            return
        movidos, cargos_movidos, destino_id, destino = resultado["r"]
        partes = [_plural(movidos, "empleado", "empleados")]
        if cargos_movidos:
            partes.append(_plural(cargos_movidos, "cargo", "cargos"))
        verbo = "pasó" if len(partes) == 1 and movidos == 1 else "pasaron"
        # Se queda elegido el destino, para ver cómo quedó.
        self.refrescar(seleccionar=str(destino_id))
        self.aviso.mostrar(f"«{d.nombre}» archivado: {' y '.join(partes)} {verbo} a «{destino}».")

    def _reactivar(self, d: Departamento) -> None:
        assert d.id is not None
        try:
            self._c.unarchive_departamento(d.id)
        except _ERRORES as exc:
            self.aviso.mostrar(str(exc), error=True)
            return
        self.filtro = _ACTIVOS
        self.refrescar(seleccionar=str(d.id))
        self.aviso.mostrar(f"«{d.nombre}» vuelve a estar activo.")
