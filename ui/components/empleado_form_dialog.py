"""Diálogo modal ``EmpleadoFormDialog``: alta y edición de empleados.

    ┌ Nuevo empleado ────────────────────────────────────────┐
    │ DATOS PERSONALES                                        │
    │ DNI [        ]  Nombres [        ]  Apellidos [       ]   │
    │ Nombres [          ]        Apellidos [          ]      │
    │ Teléfono [         ]        Correo [             ]      │
    │ PUESTO                                                  │
    │ Departamento [ ▾ ]          Cargo [ ▾ ]                 │
    │ Fecha de ingreso [📅]        ID en el reloj [   ]        │
    │ TURNO INICIAL (solo en alta)                            │
    │ Turno [ ▾ ]                 Desde [📅]                   │
    │                                  [Cancelar] [Guardar]   │
    └─────────────────────────────────────────────────────────┘

Contrato (sin cambios respecto a la versión anterior): el diálogo NO
llama al controller; recibe ``on_submit(payload)`` que devuelve ``None``
(guardado → cierra) o un ``str`` con el error a mostrar. Las listas de
catálogos llegan como ``(id, etiqueta)``; el diálogo traduce etiqueta → id
al guardar. Las reglas de dominio (formato de DNI, duplicados, fechas)
las valida el servicio.
"""

from __future__ import annotations

import datetime as _dt
import logging
from typing import Callable, Dict, List, NotRequired, Optional, Tuple, TypedDict, Union

import customtkinter as ctk

from ui import theme
from ui.components.dialogo import DialogoModal
from ui.components.piezas import selector_fecha


class EmpleadoFormPayload(TypedDict):
    """Resultado del form al pulsar Guardar.

    ``turno_id`` y ``fecha_inicio_turno`` solo vienen en modo alta.
    """

    dni: str
    nombres: str
    apellidos: str
    departamento_id: int
    cargo_id: int
    fecha_ingreso: str
    telefono: Optional[str]
    email: Optional[str]
    zkteco_id: Optional[int]
    turno_id: NotRequired[int]
    fecha_inicio_turno: NotRequired[str]


SubmitFn = Callable[[EmpleadoFormPayload], Optional[str]]

# Opción de combo: (id en BD, etiqueta visible).
ComboOption = Tuple[int, str]


def formatear_dni(texto: str) -> str:
    """Deja solo dígitos (máx. 13) y agrega los guiones: ``0401199000123`` → ``0401-1990-00123``."""
    digitos = "".join(c for c in texto if c.isdigit())[:13]
    partes = [digitos[:4], digitos[4:8], digitos[8:]]
    return "-".join(p for p in partes if p)


class EmpleadoFormDialog(DialogoModal):
    """Diálogo modal para crear o editar un empleado."""

    def __init__(
        self,
        parent: ctk.CTkBaseClass,
        title: str,
        on_submit: SubmitFn,
        departamentos: List[ComboOption],
        cargos: List[ComboOption],
        turnos: Optional[List[ComboOption]] = None,
        modo_alta: bool = True,
        initial_dni: str = "",
        initial_nombres: str = "",
        initial_apellidos: str = "",
        initial_departamento_id: Optional[int] = None,
        initial_cargo_id: Optional[int] = None,
        initial_fecha_ingreso: Optional[str] = None,
        initial_telefono: str = "",
        initial_email: str = "",
        initial_zkteco_id: Optional[int] = None,
        cargos_por_depto_provider: Optional[Callable[[int], List[ComboOption]]] = None,
    ) -> None:
        """Construye el diálogo.

        Raises:
            ValueError: Si faltan catálogos (departamentos, cargos o, en
                alta, turnos).
        """
        if not departamentos:
            raise ValueError("Se requiere al menos un departamento activo.")
        if not cargos:
            raise ValueError("Se requiere al menos un cargo activo.")
        if modo_alta and not turnos:
            raise ValueError("En modo alta se requiere al menos un turno activo.")

        titulo = "Nuevo empleado" if modo_alta else "Editar empleado"
        subtitulo = (
            "El ID en el reloj es el número con el que la persona se enroló en el "
            "biométrico; si aún no lo tiene, puede agregarlo después."
            if modo_alta
            else title.replace("Editar empleado — ", "")
        )
        super().__init__(parent, titulo=titulo, subtitulo=subtitulo, ancho=680)
        self._on_submit = on_submit
        self._modo_alta = modo_alta
        self._log = logging.getLogger(self.__class__.__name__)
        self._dep_map: Dict[str, int] = {label: id_ for id_, label in departamentos}
        self._car_map: Dict[str, int] = {label: id_ for id_, label in cargos}
        self._tur_map: Dict[str, int] = {label: id_ for id_, label in (turnos or [])}
        self._cargos_por_depto_provider = cargos_por_depto_provider

        c = self.cuerpo
        c.grid_columnconfigure((0, 1, 2), weight=1, uniform="campo")

        fila = self._seccion("Datos personales", 0, arriba=0)
        self._entry_dni = self._campo("DNI", fila, 0, "0000-0000-00000", initial_dni)
        self._entry_dni.bind("<KeyRelease>", self._formatear_dni, add="+")
        self._entry_nombres = self._campo("Nombres", fila, 1, "ej. Juan Carlos", initial_nombres)
        self._entry_apellidos = self._campo(
            "Apellidos", fila, 2, "ej. Pérez López", initial_apellidos
        )
        fila += 2
        self._entry_telefono = self._campo(
            "Teléfono (opcional)", fila, 0, "ej. 9876-5432", initial_telefono
        )
        self._entry_email = self._campo(
            "Correo (opcional)", fila, 1, "ej. nombre@empresa.hn", initial_email
        )
        zk = "" if initial_zkteco_id is None else str(initial_zkteco_id)
        self._entry_zkteco = self._campo("ID en el reloj", fila, 2, "ej. 25 (opcional)", zk)
        fila += 2

        fila = self._seccion("Puesto", fila)
        self._combo_dep = self._combo(
            "Departamento", fila, 0, departamentos, initial_departamento_id
        )
        if cargos_por_depto_provider is not None:
            self._combo_dep.configure(command=self._on_departamento_change)
        self._combo_cargo = self._combo("Cargo", fila, 1, cargos, initial_cargo_id)
        self._etiqueta("Fecha de ingreso", fila, 2)
        self._fecha_ingreso = selector_fecha(c, _a_fecha(initial_fecha_ingreso))
        self._fecha_ingreso.grid(row=fila + 1, column=2, sticky="w", padx=(12, 0))
        fila += 2

        self._combo_turno: Optional[ctk.CTkOptionMenu] = None
        self._fecha_turno = None
        if modo_alta and turnos:
            fila = self._seccion("Turno inicial", fila)
            self._etiqueta("Turno", fila, 0)
            self._combo_turno = self._combo_sin_etiqueta(fila + 1, turnos)
            self._etiqueta("Aplica desde", fila, 2)
            self._fecha_turno = selector_fecha(c, _dt.date.today())
            self._fecha_turno.grid(row=fila + 1, column=2, sticky="w", padx=(12, 0))
            fila += 2
            ctk.CTkLabel(
                c,
                text=(
                    "¿También trabaja sábados con otro horario? "
                    "Agréguelo luego en «Gestionar turnos»."
                ),
                font=theme.fuente(theme.TAM_PEQUENO - 1),
                text_color=theme.TEXTO_SUAVE,
                anchor="w",
            ).grid(row=fila, column=0, columnspan=3, sticky="w", pady=(4, 0))

        self.agregar_botones(
            "Registrar empleado" if modo_alta else "Guardar cambios", self._on_save
        )
        for entry in (
            self._entry_dni,
            self._entry_nombres,
            self._entry_apellidos,
            self._entry_telefono,
            self._entry_email,
            self._entry_zkteco,
        ):
            entry.bind("<Return>", lambda _e: self._on_save())
        destino = self._entry_dni if modo_alta else self._entry_nombres
        self.after(80, destino.focus_set)

    # ── Construcción ──────────────────────────────────────────────────────

    def _seccion(self, texto: str, fila: int, arriba: int = 16) -> int:
        ctk.CTkLabel(
            self.cuerpo,
            text=texto.upper(),
            font=theme.fuente(theme.TAM_PEQUENO - 1, "bold"),
            text_color=theme.TEXTO_SUAVE,
            anchor="w",
        ).grid(row=fila, column=0, columnspan=3, sticky="w", pady=(arriba, 2))
        return fila + 1

    def _etiqueta(self, texto: str, fila: int, col: int) -> None:
        ctk.CTkLabel(
            self.cuerpo, text=texto, font=theme.fuente(theme.TAM_PEQUENO, "bold"), anchor="w"
        ).grid(row=fila, column=col, sticky="w", padx=(12 if col else 0, 0), pady=(4, 2))

    def _campo(
        self, texto: str, fila: int, col: int, placeholder: str, inicial: str
    ) -> ctk.CTkEntry:
        self._etiqueta(texto, fila, col)
        entry = ctk.CTkEntry(self.cuerpo, height=34, placeholder_text=placeholder)
        if inicial:
            entry.insert(0, inicial)
        entry.grid(row=fila + 1, column=col, sticky="ew", padx=(12 if col else 0, 0))
        return entry

    def _combo_sin_etiqueta(self, fila: int, opciones: List[ComboOption]) -> ctk.CTkOptionMenu:
        """Combo que ocupa dos columnas (los nombres de turno son largos)."""
        labels = [lbl for _id, lbl in opciones] or [""]
        combo = ctk.CTkOptionMenu(
            self.cuerpo,
            values=labels,
            height=34,
            fg_color=theme.SECUNDARIO,
            button_color=theme.SECUNDARIO_HOVER,
            button_hover_color=theme.BORDE,
            text_color=theme.TEXTO,
            dynamic_resizing=False,
        )
        combo.set(labels[0])
        combo.grid(row=fila, column=0, columnspan=2, sticky="ew")
        return combo

    def _combo(
        self,
        texto: str,
        fila: int,
        col: int,
        opciones: List[ComboOption],
        inicial: Optional[int],
    ) -> ctk.CTkOptionMenu:
        self._etiqueta(texto, fila, col)
        labels = [lbl for _id, lbl in opciones] or [""]
        combo = ctk.CTkOptionMenu(
            self.cuerpo,
            values=labels,
            height=34,
            fg_color=theme.SECUNDARIO,
            button_color=theme.SECUNDARIO_HOVER,
            button_hover_color=theme.BORDE,
            text_color=theme.TEXTO,
            dynamic_resizing=False,
        )
        seleccion = next((lbl for id_, lbl in opciones if id_ == inicial), labels[0])
        combo.set(seleccion)
        combo.grid(row=fila + 1, column=col, sticky="ew", padx=(12 if col else 0, 0))
        return combo

    # ── Eventos ───────────────────────────────────────────────────────────

    def _formatear_dni(self, evento: object) -> None:
        actual = str(self._entry_dni.get())
        nuevo = formatear_dni(actual)
        if nuevo != actual:
            self._entry_dni.delete(0, "end")
            self._entry_dni.insert(0, nuevo)

    def _on_departamento_change(self, _label: str) -> None:
        """Al cambiar de departamento, ofrece solo sus cargos (y los globales)."""
        if self._cargos_por_depto_provider is None:
            return
        dep_id = self._dep_map.get(self._combo_dep.get())
        if dep_id is None:
            return
        try:
            opciones = self._cargos_por_depto_provider(dep_id)
        except Exception:  # noqa: BLE001
            self._log.exception("No se pudieron recargar los cargos.")
            return
        if not opciones:
            self._car_map = {}
            self._combo_cargo.configure(values=[""])
            self._combo_cargo.set("")
            return
        labels = [lbl for _id, lbl in opciones]
        actual = self._combo_cargo.get()
        self._car_map = {label: id_ for id_, label in opciones}
        self._combo_cargo.configure(values=labels)
        self._combo_cargo.set(actual if actual in labels else labels[0])

    def _on_save(self) -> None:
        payload = self._recolectar_payload()
        if isinstance(payload, str):
            self.mostrar_error(payload)
            return
        resultado = self._on_submit(payload)
        if resultado is None:
            self.cerrar()
        else:
            self.mostrar_error(resultado)

    def _recolectar_payload(self) -> Union[EmpleadoFormPayload, str]:
        """Lee los widgets; solo valida tipos y selecciones (el resto, el servicio)."""
        dep_label = self._combo_dep.get()
        car_label = self._combo_cargo.get()
        if dep_label not in self._dep_map:
            return "Seleccione un departamento."
        if car_label not in self._car_map:
            return "Seleccione un cargo para ese departamento."

        zk_raw = str(self._entry_zkteco.get()).strip()
        zkteco_id: Optional[int] = None
        if zk_raw:
            try:
                zkteco_id = int(zk_raw)
            except ValueError:
                return "El ID en el reloj debe ser un número entero."
            if zkteco_id <= 0:
                return "El ID en el reloj debe ser mayor que cero."

        telefono = str(self._entry_telefono.get()).strip()
        email = str(self._entry_email.get()).strip()
        payload: EmpleadoFormPayload = {
            "dni": str(self._entry_dni.get()).strip(),
            "nombres": str(self._entry_nombres.get()).strip(),
            "apellidos": str(self._entry_apellidos.get()).strip(),
            "departamento_id": self._dep_map[dep_label],
            "cargo_id": self._car_map[car_label],
            "fecha_ingreso": self._fecha_ingreso.get_date().isoformat(),
            "telefono": telefono or None,
            "email": email or None,
            "zkteco_id": zkteco_id,
        }
        if self._modo_alta:
            assert self._combo_turno is not None and self._fecha_turno is not None
            tur_label = self._combo_turno.get()
            if tur_label not in self._tur_map:
                return "Seleccione el turno inicial."
            payload["turno_id"] = self._tur_map[tur_label]
            payload["fecha_inicio_turno"] = self._fecha_turno.get_date().isoformat()
        return payload


def _a_fecha(iso: Optional[str]) -> _dt.date:
    if iso:
        try:
            return _dt.date.fromisoformat(iso[:10])
        except ValueError:
            pass
    return _dt.date.today()
