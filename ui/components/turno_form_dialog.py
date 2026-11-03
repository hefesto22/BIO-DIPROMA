"""Diálogo modal ``TurnoFormDialog``: crear o editar un turno.

    ┌ Nuevo turno ─────────────────────────────────────────────┐
    │ Nombre [ Administrativo                                 ] │
    │ ┌ Horario 1 ───────────────────────────────────────────┐ │
    │ │ (L)(M)(M)(J)(V)( S)( D)   Lun a Vie · Lun a Sáb · Todos│ │
    │ │ Entrada [09:00]  Salida [17:00]  Descanso [60] min  7 h│ │
    │ └───────────────────────────────────────────────────────┘ │
    │ ┌ Horario 2 ─────────────────────────────────── Quitar ─┐ │
    │ │ ( L)( M)( M)( J)( V)(S)( D)                            │ │
    │ │ Entrada [09:00]  Salida [12:00]  Descanso [0] min   3 h│ │
    │ └───────────────────────────────────────────────────────┘ │
    │ + Agregar horario para otros días                         │
    │ [ Lun a Vie 09:00–17:00 · Sábado 09:00–12:00 · 38 h/sem ] │
    │                                     [Cancelar] [Guardar]  │
    └───────────────────────────────────────────────────────────┘

Un día solo puede estar en un horario: los días que ya usa otro horario
aparecen apagados. El primer horario es el principal (columnas de
``turnos``); los demás viajan en ``horarios_extra``.

Contrato: ``on_submit(payload)`` devuelve ``None`` (cierra) o un ``str``
con el error. Las reglas (formato de horas, descanso menor que la
jornada, nombre único, días sin repetir) las valida el servicio.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Sequence, TypedDict

import customtkinter as ctk

from core.models.turno import (
    DIAS_LABORALES,
    DIAS_TODA_LA_SEMANA,
    DOMINGO,
    SABADO,
    HorarioTurno,
)
from core.services.validators import duracion_turno_minutos
from ui import formato, theme
from ui.components.dialogo import DialogoModal


class TurnoFormPayload(TypedDict):
    """Resultado del form al pulsar Guardar (todos los campos editables)."""

    nombre: str
    hora_entrada: str
    hora_salida: str
    minutos_descanso: int
    dias_semana: int
    horarios_extra: List[HorarioTurno]


SubmitFn = Callable[[TurnoFormPayload], Optional[str]]

_ATAJOS = (
    ("Lun a Vie", DIAS_LABORALES),
    ("Lun a Sáb", DIAS_LABORALES | SABADO),
    ("Todos", DIAS_TODA_LA_SEMANA),
)

# Más de cuatro horarios distintos en una semana ya no es "un turno".
_MAX_HORARIOS = 4


class TurnoFormDialog(DialogoModal):
    """Diálogo modal para crear o editar un turno."""

    def __init__(
        self,
        parent: ctk.CTkBaseClass,
        title: str,
        on_submit: SubmitFn,
        initial_nombre: str = "",
        initial_hora_entrada: str = "08:00",
        initial_hora_salida: str = "17:00",
        initial_minutos_descanso: int = 60,
        initial_dias_semana: int = DIAS_LABORALES,
        initial_horarios_extra: Sequence[HorarioTurno] = (),
    ) -> None:
        es_nuevo = not initial_nombre
        super().__init__(
            parent,
            titulo="Nuevo turno" if es_nuevo else "Editar turno",
            subtitulo=(
                "Horario contra el que se comparan las marcas. Si algún día se trabaja "
                "con otro horario (por ejemplo el sábado medio día), agréguelo abajo."
            ),
            ancho=620,
        )
        self._on_submit = on_submit
        self._bloques: List[_BloqueHorario] = []

        c = self.cuerpo
        c.grid_columnconfigure(0, weight=1)
        negrita = theme.fuente(theme.TAM_PEQUENO, "bold")

        ctk.CTkLabel(c, text="Nombre", font=negrita).grid(row=0, column=0, sticky="w", pady=(0, 3))
        self._entry_nombre = ctk.CTkEntry(c, height=34, placeholder_text="ej. Administrativo")
        if initial_nombre:
            self._entry_nombre.insert(0, initial_nombre)
        self._entry_nombre.grid(row=1, column=0, sticky="ew")
        self._entry_nombre.bind("<Return>", lambda _e: self._on_save())

        self._contenedor = ctk.CTkFrame(c, fg_color="transparent")
        self._contenedor.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        self._contenedor.grid_columnconfigure(0, weight=1)

        self._btn_agregar = ctk.CTkButton(
            c,
            text="+  Agregar horario para otros días",
            height=30,
            anchor="w",
            fg_color="transparent",
            hover_color=theme.SECUNDARIO_HOVER,
            text_color=theme.PRIMARIO,
            font=theme.fuente(theme.TAM_PEQUENO, "bold"),
            command=self._agregar_horario,
        )
        self._btn_agregar.grid(row=3, column=0, sticky="w", pady=(6, 0))

        self._resumen = ctk.CTkLabel(
            c,
            text="",
            fg_color=theme.TARJETA_ALT,
            corner_radius=8,
            anchor="w",
            justify="left",
            wraplength=560,
            font=theme.fuente(theme.TAM_PEQUENO),
            padx=12,
            pady=8,
        )
        self._resumen.grid(row=4, column=0, sticky="ew", pady=(10, 0))

        principal = HorarioTurno(
            dias_semana=initial_dias_semana,
            hora_entrada=initial_hora_entrada,
            hora_salida=initial_hora_salida,
            minutos_descanso=initial_minutos_descanso,
        )
        for horario in (principal, *initial_horarios_extra):
            self._crear_bloque(horario)
        self._acomodar()

        self.agregar_botones("Crear turno" if es_nuevo else "Guardar cambios", self._on_save)
        self.after(80, self._entry_nombre.focus_set)

    # ── Bloques de horario ────────────────────────────────────────────────

    def _crear_bloque(self, horario: HorarioTurno) -> "_BloqueHorario":
        bloque = _BloqueHorario(
            self._contenedor,
            principal=not self._bloques,
            horario=horario,
            al_cambiar=self._actualizar_resumen,
            al_tocar_dia=self._tocar_dia,
            al_atajo=self._aplicar_atajo,
            al_quitar=self._quitar_horario,
            al_guardar=self._on_save,
        )
        self._bloques.append(bloque)
        return bloque

    def _ocupados_por_otros(self, bloque: "_BloqueHorario") -> int:
        total = 0
        for otro in self._bloques:
            if otro is not bloque:
                total |= otro.dias
        return total

    def _tocar_dia(self, bloque: "_BloqueHorario", bit: int) -> None:
        if self._ocupados_por_otros(bloque) & bit:
            return
        bloque.dias ^= bit
        self._pintar()

    def _aplicar_atajo(self, bloque: "_BloqueHorario", valor: int) -> None:
        bloque.dias = valor & ~self._ocupados_por_otros(bloque)
        self._pintar()

    def _agregar_horario(self) -> None:
        ocupados = 0
        for b in self._bloques:
            ocupados |= b.dias
        libres = DIAS_TODA_LA_SEMANA & ~ocupados
        if not libres or len(self._bloques) >= _MAX_HORARIOS:
            return
        if libres & SABADO:
            dia = SABADO
        elif libres & DOMINGO:
            dia = DOMINGO
        else:
            dia = 1 << (libres.bit_length() - 1)  # el primer día libre de la semana
        entrada, salida, _desc = self._bloques[0].textos()
        bloque = self._crear_bloque(
            HorarioTurno(
                dias_semana=dia,
                hora_entrada=entrada or "08:00",
                hora_salida="12:00" if entrada and entrada < "12:00" else (salida or "12:00"),
                minutos_descanso=0,
            )
        )
        self._acomodar()
        bloque.enfocar()

    def _quitar_horario(self, bloque: "_BloqueHorario") -> None:
        if bloque.principal:
            return
        self._bloques.remove(bloque)
        bloque.destroy()
        self._acomodar()

    def _acomodar(self) -> None:
        varios = len(self._bloques) > 1
        for i, bloque in enumerate(self._bloques):
            bloque.grid(row=i, column=0, sticky="ew", pady=(0 if i == 0 else 8, 0))
            bloque.set_titulo(f"Horario {i + 1}" if varios else "Horario")
        self._pintar()

    def _pintar(self) -> None:
        ocupados = 0
        for bloque in self._bloques:
            bloque.pintar(self._ocupados_por_otros(bloque))
            ocupados |= bloque.dias
        lleno = ocupados == DIAS_TODA_LA_SEMANA or len(self._bloques) >= _MAX_HORARIOS
        if lleno:
            self._btn_agregar.grid_remove()
        else:
            self._btn_agregar.grid()
        self._actualizar_resumen()

    # ── Resumen en vivo ───────────────────────────────────────────────────

    def _actualizar_resumen(self) -> None:
        partes: List[str] = []
        minutos_semana = 0
        for i, bloque in enumerate(self._bloques, start=1):
            nombre = f"El horario {i}" if len(self._bloques) > 1 else "El horario"
            entrada, salida, descanso_txt = bloque.textos()
            bloque.mostrar_jornada("")
            if not bloque.dias:
                self._mostrar_resumen(f"{nombre} no tiene días: elija al menos uno.", error=True)
                return
            try:
                total = duracion_turno_minutos(entrada, salida)
                descanso = int(descanso_txt or "0")
            except (ValueError, TypeError):
                self._mostrar_resumen(
                    "Escriba las horas como HH:MM (24 h), por ejemplo 08:00 y 17:00.",
                    error=False,
                )
                return
            efectiva = total - descanso
            if efectiva <= 0:
                self._mostrar_resumen(
                    f"{nombre}: el descanso no puede ser igual o mayor que la jornada.", error=True
                )
                return
            bloque.mostrar_jornada(formato.duracion(efectiva))
            minutos_semana += efectiva * bin(bloque.dias).count("1")
            texto = f"{formato.dias_texto(bloque.dias)} {entrada}–{salida}"
            if salida <= entrada:
                texto += " (cruza la medianoche)"
            partes.append(texto)
        partes.append(f"{formato.duracion(minutos_semana)} por semana")
        self._mostrar_resumen("  ·  ".join(partes), error=False)

    def _mostrar_resumen(self, texto: str, error: bool) -> None:
        self._resumen.configure(text=texto, text_color=theme.ERROR if error else theme.TEXTO)

    # ── Guardar ───────────────────────────────────────────────────────────

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

    def _recolectar_payload(self) -> TurnoFormPayload | str:
        """Solo valida tipos; las reglas de negocio las aplica el servicio."""
        horarios: List[HorarioTurno] = []
        varios = len(self._bloques) > 1
        for i, bloque in enumerate(self._bloques, start=1):
            nombre = f"horario {i}" if varios else "horario"
            entrada, salida, descanso_txt = bloque.textos()
            try:
                descanso = int(descanso_txt or "0")
            except ValueError:
                return f"El descanso del {nombre} debe ser un número entero de minutos."
            if descanso < 0:
                return f"El descanso del {nombre} no puede ser negativo."
            if not bloque.dias:
                return (
                    f"Seleccione al menos un día en el {nombre}."
                    if varios
                    else "Seleccione al menos un día de la semana."
                )
            horarios.append(HorarioTurno(bloque.dias, entrada, salida, descanso))
        principal, extras = horarios[0], horarios[1:]
        return TurnoFormPayload(
            nombre=str(self._entry_nombre.get()).strip(),
            hora_entrada=principal.hora_entrada,
            hora_salida=principal.hora_salida,
            minutos_descanso=principal.minutos_descanso,
            dias_semana=principal.dias_semana,
            horarios_extra=extras,
        )


class _BloqueHorario(ctk.CTkFrame):
    """Una tarjeta "días + entrada/salida/descanso" dentro del formulario."""

    def __init__(
        self,
        master: ctk.CTkFrame,
        principal: bool,
        horario: HorarioTurno,
        al_cambiar: Callable[[], None],
        al_tocar_dia: Callable[["_BloqueHorario", int], None],
        al_atajo: Callable[["_BloqueHorario", int], None],
        al_quitar: Callable[["_BloqueHorario"], None],
        al_guardar: Callable[[], None],
    ) -> None:
        super().__init__(
            master,
            fg_color=theme.TARJETA_ALT,
            corner_radius=8,
            border_width=1,
            border_color=theme.BORDE,
        )
        self.principal = principal
        self.dias = horario.dias_semana
        self.grid_columnconfigure(0, weight=1)
        negrita = theme.fuente(theme.TAM_PEQUENO, "bold")

        cab = ctk.CTkFrame(self, fg_color="transparent")
        cab.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 0))
        cab.grid_columnconfigure(0, weight=1)
        self._titulo = ctk.CTkLabel(cab, text="Horario", font=negrita, anchor="w", height=22)
        self._titulo.grid(row=0, column=0, sticky="w")
        if not principal:
            ctk.CTkButton(
                cab,
                text="Quitar",
                width=60,
                height=22,
                fg_color="transparent",
                hover_color=theme.SECUNDARIO_HOVER,
                text_color=theme.PELIGRO,
                font=theme.fuente(theme.TAM_PEQUENO),
                command=lambda: al_quitar(self),
            ).grid(row=0, column=1, sticky="e")

        fila_dias = ctk.CTkFrame(self, fg_color="transparent")
        fila_dias.grid(row=1, column=0, sticky="w", padx=12, pady=(6, 0))
        self._botones: List[ctk.CTkButton] = []
        for i, (inicial, _corto, _largo, bit) in enumerate(formato.DIAS):
            boton = ctk.CTkButton(
                fila_dias,
                text=inicial,
                width=32,
                height=32,
                corner_radius=16,
                font=theme.fuente(theme.TAM_PEQUENO, "bold"),
                command=lambda b=bit: al_tocar_dia(self, b),
            )
            boton.grid(row=0, column=i, padx=(0, 5))
            self._botones.append(boton)
        if principal:
            for j, (atajo, bits) in enumerate(_ATAJOS):
                ctk.CTkButton(
                    fila_dias,
                    text=atajo,
                    width=66,
                    height=26,
                    fg_color="transparent",
                    hover_color=theme.SECUNDARIO_HOVER,
                    text_color=theme.PRIMARIO,
                    font=theme.fuente(theme.TAM_PEQUENO),
                    command=lambda v=bits: al_atajo(self, v),
                ).grid(row=0, column=8 + j, padx=(8 if j == 0 else 0, 0))

        horas = ctk.CTkFrame(self, fg_color="transparent")
        horas.grid(row=2, column=0, sticky="ew", padx=12, pady=(8, 10))
        horas.grid_columnconfigure(7, weight=1)
        self._entradas: List[ctk.CTkEntry] = []
        campos = (
            ("Entrada", horario.hora_entrada, 70),
            ("Salida", horario.hora_salida, 70),
            ("Descanso", str(horario.minutos_descanso), 52),
        )
        for k, (etiqueta, valor, ancho) in enumerate(campos):
            ctk.CTkLabel(horas, text=etiqueta, font=theme.fuente(theme.TAM_PEQUENO)).grid(
                row=0, column=k * 2, padx=(0 if k == 0 else 14, 6)
            )
            entry = ctk.CTkEntry(horas, width=ancho, height=32, justify="center")
            entry.insert(0, valor)
            entry.grid(row=0, column=k * 2 + 1)
            entry.bind("<KeyRelease>", lambda _e: al_cambiar())
            # Al entrar al campo se selecciona todo: escribir reemplaza la hora.
            entry.bind("<FocusIn>", lambda _e, w=entry: w.after(1, _seleccionar_todo, w))
            entry.bind("<Return>", lambda _e: al_guardar())
            self._entradas.append(entry)
        ctk.CTkLabel(
            horas, text="min", font=theme.fuente(theme.TAM_PEQUENO), text_color=theme.TEXTO_SUAVE
        ).grid(row=0, column=6, padx=(6, 0))
        self._jornada = ctk.CTkLabel(
            horas,
            text="",
            font=theme.fuente(theme.TAM_PEQUENO, "bold"),
            text_color=theme.TEXTO_SUAVE,
            anchor="e",
        )
        self._jornada.grid(row=0, column=7, sticky="e")

    def set_titulo(self, texto: str) -> None:
        self._titulo.configure(text=texto)

    def textos(self) -> tuple[str, str, str]:
        """``(entrada, salida, descanso)`` tal como están escritos."""
        e, s, d = (str(w.get()).strip() for w in self._entradas)
        return e, s, d

    def mostrar_jornada(self, texto: str) -> None:
        self._jornada.configure(text=f"{texto} por día" if texto else "")

    def enfocar(self) -> None:
        self.after(60, self._entradas[0].focus_set)

    def pintar(self, ocupados_por_otros: int) -> None:
        """Días propios en azul; los que usa otro horario, apagados."""
        for boton, (_i, _c, _l, bit) in zip(self._botones, formato.DIAS):
            if ocupados_por_otros & bit:
                boton.configure(
                    state="disabled",
                    fg_color=theme.NEUTRO_FONDO,
                    text_color_disabled=theme.BORDE,
                )
                continue
            activo = bool(self.dias & bit)
            boton.configure(
                state="normal",
                fg_color=theme.PRIMARIO if activo else theme.SECUNDARIO,
                hover_color=theme.PRIMARIO_HOVER if activo else theme.SECUNDARIO_HOVER,
                text_color=theme.TEXTO_SOBRE_PRIMARIO if activo else theme.TEXTO_SUAVE,
            )


def _seleccionar_todo(entry: ctk.CTkEntry) -> None:
    entry.select_range(0, "end")
    entry.icursor("end")
