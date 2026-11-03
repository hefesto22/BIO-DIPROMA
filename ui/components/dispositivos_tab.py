"""Pestaña "Relojes" de Configuración (catálogo de relojes ZKTeco).

Sirve cualquier reloj ZKTeco, de huella, de rostro o de ambos. Hay dos
formas de conexión:

    - Directo: la app le pide las marcas al reloj ("Sincronización
      ZKTeco"). Admite clave de comunicación y UDP para relojes antiguos.
      En el panel: probar conexión, hora del reloj, quién está registrado.
    - Push: el reloj envía las marcas solo, en cuanto alguien marca. En el
      panel: si está conectado, cuándo envió por última vez y qué poner en
      el reloj (IP de esta computadora y puerto).

Si un reloj push se conecta sin estar registrado, arriba aparece un aviso
para agregarlo con un clic (sus marcas esperan en el reloj hasta entonces).
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from tkinter import messagebox
from typing import Dict, List, Optional, Tuple

import customtkinter as ctk

from core.models.dispositivo import (
    MODO_DIRECTO,
    MODO_PUSH,
    PUERTO_ZKTECO_DEFAULT,
    Dispositivo,
)
from core.models.sincronizacion import EstadoSincronizacion, Sincronizacion
from core.services.diagnostico_reloj_service import DiagnosticoReloj
from core.services.recepcion_push_service import RelojDetectado
from core.services.errors import SincronizacionError, ValidationError
from ui.async_util import run_async_ui
from ui import formato, theme
from ui.components import piezas
from ui.components.data_table import Columna, Fila
from ui.components.dispositivo_form_dialog import DispositivoFormDialog, DispositivoFormPayload
from ui.components.lista_detalle import ListaDetalle
from ui.components.usuarios_format import hace
from ui.controllers.configuracion_controller import ConfiguracionController

_ACTIVOS = "ACTIVOS"
_ARCHIVADOS = "ARCHIVADOS"
_ULTIMA = "ULTIMA"

_COLUMNAS = (
    Columna("nombre", "Reloj", ancho=170),
    Columna("conexion", "Tipo", ancho=65),
    Columna("direccion", "Dirección", ancho=120),
    Columna("ultima", "Último dato", ancho=100),
    Columna("estado", "Estado", ancho=90),
)

# Un reloj push manda un latido cada 10 s: si pasa más de esto, "sin contacto".
_CONECTADO_SI_MENOS_DE = timedelta(seconds=90)
_VIGILAR_CADA_MS = 5000

_ERRORES = (SincronizacionError, ValidationError)

_AYUDA_SIN_RESPUESTA = (
    "Si está encendido y conectado, compare la dirección IP de aquí con la que muestra el "
    "propio reloj en su menú de red, y que esta computadora esté en la misma red."
)


@dataclass
class _Prueba:
    """Estado de "Probar conexión" de un reloj mientras la pestaña esté abierta."""

    probando: bool = True
    diagnostico: Optional[DiagnosticoReloj] = None
    error: str = ""
    ajustando_hora: bool = False


@dataclass
class DispositivoFormDialogInicial:
    """Valores con los que se abre el formulario de un reloj."""

    nombre: str = ""
    ip: str = ""
    puerto: int = PUERTO_ZKTECO_DEFAULT
    modo: str = MODO_DIRECTO
    clave: int = 0
    udp: bool = False
    serie: str = ""


def texto_desfase(segundos: int) -> str:
    """``-420`` → ``7 min atrasado``; menos de un minuto → ``en hora``."""
    if abs(segundos) <= 60:
        return "en hora"
    sentido = "adelantado" if segundos > 0 else "atrasado"
    minutos = round(abs(segundos) / 60)
    if minutos < 60:
        return f"{minutos} min {sentido}"
    horas, resto = divmod(minutos, 60)
    if horas < 24:
        return f"{horas} h {resto} min {sentido}" if resto else f"{horas} h {sentido}"
    dias = round(horas / 24)
    return f"{dias} día{'s' if dias != 1 else ''} {sentido}"


def _miles(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def _contacto(r: Dispositivo, ahora: datetime) -> Optional[timedelta]:
    """Hace cuánto se comunicó un reloj push (``None`` = nunca)."""
    if not r.ultimo_contacto:
        return None
    try:
        cuando = datetime.fromisoformat(r.ultimo_contacto)
    except ValueError:
        return None
    if cuando.tzinfo is None:
        cuando = cuando.replace(tzinfo=timezone.utc)
    return ahora - cuando


def _corto(iso_utc: Optional[str], ahora: datetime) -> str:
    """Como ``hace`` pero corto para la tabla: "Ahora", "Hace 5 min", "Hoy 16:08"."""
    if not iso_utc:
        return "Nunca"
    texto = hace(iso_utc, ahora)
    return "Ahora" if texto == "Hace un momento" else texto


def estado_push(r: Dispositivo, ahora: datetime) -> str:
    """``Conectado`` / ``Sin contacto`` / ``Esperando`` (nunca se conectó)."""
    hace_cuanto = _contacto(r, ahora)
    if hace_cuanto is None:
        return "Esperando"
    return "Conectado" if hace_cuanto <= _CONECTADO_SI_MENOS_DE else "Sin contacto"


class DispositivosTab(ListaDetalle):
    texto_sin_seleccion = "Seleccione un reloj\npara ver su detalle y acciones."

    def __init__(self, master: ctk.CTkBaseClass, controller: ConfiguracionController) -> None:
        self._c = controller
        self._relojes: List[Dispositivo] = []
        self._ultimas: Dict[int, Sincronizacion] = {}
        self._pruebas: Dict[int, _Prueba] = {}
        self._fila_conexion = 0
        self._detectados: List[RelojDetectado] = []
        self._firma = ""
        super().__init__(
            master,
            _COLUMNAS,
            (
                (_ACTIVOS, "Relojes activos", theme.PRIMARIO, True),
                (_ARCHIVADOS, "Archivados", theme.NEUTRO, True),
                (_ULTIMA, "Última sincronización correcta", theme.EXITO, False),
            ),
            placeholder_busqueda="Buscar por nombre, dirección IP o número de serie…",
            texto_vacio="No hay relojes en esta lista.",
            filtro_inicial=_ACTIVOS,
        )
        self.tabla.color_tag("fallida", theme.ERROR)
        # Para agregar un reloj push conviene configurarlo primero y que la
        # app lo "vea": este botón enciende la escucha sin registrar nada.
        self._btn_push = piezas.boton_secundario(
            self.barra, "Detectar relojes push", self._empezar_recepcion, ancho=200
        )
        self._btn_push.grid(row=0, column=1, padx=(10, 0))
        self.refrescar()
        self.after(_VIGILAR_CADA_MS, self._vigilar)

    # ── Datos ─────────────────────────────────────────────────────────────

    def _cargar(self) -> None:
        self._relojes = self._c.list_dispositivos(solo_activos=False)
        self._ultimas = self._c.ultimas_sincronizaciones()
        self._detectados = self._c.relojes_detectados()
        self._firma = self._firma_de(self._relojes, self._detectados)
        self._pintar_banda()
        self._pintar_boton_push()

    @staticmethod
    def _resultado(s: Optional[Sincronizacion]) -> str:
        if s is None:
            return "Nunca"
        return {
            EstadoSincronizacion.OK.value: "Correcta",
            EstadoSincronizacion.FALLIDA.value: "Falló",
            EstadoSincronizacion.EN_CURSO.value: "En curso",
        }.get(s.estado, s.estado)

    def _filas(self) -> Tuple[List[Fila], Dict[str, str]]:
        texto = self.texto_busqueda()
        ahora = datetime.now(timezone.utc)
        activos = [r for r in self._relojes if r.is_active]
        filas: List[Fila] = []
        for r in self._relojes:
            if r.id is None or r.is_active != (self.filtro == _ACTIVOS):
                continue
            # El puerto solo se muestra si no es el de siempre.
            direccion = (
                r.ip if r.es_push or r.puerto == PUERTO_ZKTECO_DEFAULT else f"{r.ip}:{r.puerto}"
            )
            buscable = f"{r.nombre} {direccion} {r.numero_serie or ''}".casefold()
            if texto and texto not in buscable:
                continue
            ultima = self._ultimas.get(r.id)
            tags: Tuple[str, ...] = ()
            if r.es_push:
                cuando = r.ultimo_contacto or ""
                ultimo = _corto(cuando, ahora)
                estado = estado_push(r, ahora)
                problema = estado == "Sin contacto"
            else:
                cuando = ultima.inicio if ultima else ""
                ultimo = _corto(cuando, ahora)
                estado = self._resultado(ultima)
                problema = (
                    ultima is not None and ultima.estado == EstadoSincronizacion.FALLIDA.value
                )
            if not r.is_active:
                tags = ("apagado",)
            elif problema:
                tags = ("fallida",)
            filas.append(
                Fila(
                    iid=str(r.id),
                    valores={
                        "nombre": r.nombre,
                        "conexion": "Push" if r.es_push else "Directo",
                        "direccion": direccion,
                        "ultima": ultimo,
                        "estado": estado,
                    },
                    orden={"ultima": cuando, "nombre": r.nombre.casefold()},
                    tags=tags,
                )
            )
        correctas = [
            s.inicio for s in self._ultimas.values() if s.estado == EstadoSincronizacion.OK.value
        ]
        return filas, {
            _ACTIVOS: str(len(activos)),
            _ARCHIVADOS: str(len(self._relojes) - len(activos)),
            _ULTIMA: hace(max(correctas), ahora) if correctas else "Nunca",
        }

    def _buscar(self, iid: str) -> Optional[Dispositivo]:
        return next((r for r in self._relojes if r.id == int(iid)), None)

    # ── Panel ─────────────────────────────────────────────────────────────

    def _detalle(self, panel: ctk.CTkScrollableFrame, iid: str) -> None:
        r = self._buscar(iid)
        if r is None or r.id is None:
            return
        ultima = self._ultimas.get(r.id)
        ctk.CTkLabel(
            panel,
            text=r.nombre,
            font=theme.fuente(theme.TAM_SECCION + 2, "bold"),
            anchor="w",
            justify="left",
            wraplength=280,
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(18, 0))
        ctk.CTkLabel(
            panel,
            text=r.ip if r.es_push else f"{r.ip} : {r.puerto}",
            font=theme.fuente(22, "bold"),
            text_color=theme.PRIMARIO,
            anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=16, pady=(6, 0))
        insignias = [
            (
                ("Activo", theme.EXITO, theme.EXITO_FONDO)
                if r.is_active
                else ("Archivado", theme.NEUTRO, theme.NEUTRO_FONDO)
            ),
            (
                ("Envía las marcas solo", theme.PRIMARIO, theme.PRIMARIO_SUAVE)
                if r.es_push
                else ("Conexión directa", theme.NEUTRO, theme.NEUTRO_FONDO)
            ),
        ]
        if (
            not r.es_push
            and ultima is not None
            and ultima.estado == EstadoSincronizacion.FALLIDA.value
        ):
            insignias.append(("Última sincronización falló", theme.ERROR, theme.ERROR_FONDO))
        fila = piezas.fila_insignias(panel, 2, insignias)

        fila = piezas.titulo_seccion(panel, fila, "Acciones", arriba=14)
        prueba = self._pruebas.get(r.id)
        if r.is_active and r.es_push:
            fila = piezas.boton_accion(
                panel, fila, "Editar", lambda: self._editar(r), primario=True
            )
            fila = piezas.boton_accion(
                panel, fila, "Archivar", lambda: self._archivar(r), peligro=True
            )
            fila = self._detalle_push(panel, fila, r)
        elif r.is_active:
            if self._c.puede_probar_relojes:
                probando = prueba is not None and (prueba.probando or prueba.ajustando_hora)
                fila = piezas.boton_accion(
                    panel,
                    fila,
                    "Probando…" if probando else "Probar conexión",
                    lambda: self._probar(r),
                    primario=True,
                    motivo_deshabilitado="Espere a que termine." if probando else None,
                )
            fila = piezas.boton_accion(
                panel,
                fila,
                "Editar",
                lambda: self._editar(r),
                primario=not self._c.puede_probar_relojes,
            )
            fila = piezas.boton_accion(
                panel, fila, "Archivar", lambda: self._archivar(r), peligro=True
            )
        else:
            fila = piezas.boton_accion(
                panel, fila, "Reactivar", lambda: self._reactivar(r), primario=True
            )

        if prueba is not None and r.is_active:
            self._fila_conexion = fila
            fila = self._detalle_prueba(panel, fila, r, prueba)

        titulo = "Últimas marcas recibidas" if r.es_push else "Última sincronización"
        fila = piezas.titulo_seccion(panel, fila, titulo, arriba=12)
        if ultima is None:
            piezas.texto_suave(
                panel,
                fila,
                (
                    "Todavía no ha enviado marcas."
                    if r.es_push
                    else "Todavía no se ha sincronizado. Hágalo desde «Sincronización ZKTeco»."
                ),
            )
            return
        lineas = [
            ("Fecha", formato.fecha_hora(ultima.inicio)),
            ("Resultado", self._resultado(ultima)),
            ("Período", formato.rango_fechas(ultima.rango_desde, ultima.rango_hasta)),
            ("Marcas recibidas", str(ultima.registros_recibidos)),
        ]
        if ultima.error_mensaje:
            lineas.append(("Error", ultima.error_mensaje))
        fila = piezas.bloque_datos(panel, fila, lineas)
        ctk.CTkFrame(panel, height=16, fg_color="transparent").grid(row=fila, column=0)

    def _detalle_prueba(
        self, panel: ctk.CTkScrollableFrame, fila: int, r: Dispositivo, prueba: _Prueba
    ) -> int:
        fila = piezas.titulo_seccion(panel, fila, "Conexión", arriba=12)
        if prueba.probando:
            barra = ctk.CTkProgressBar(
                panel, mode="indeterminate", height=6, progress_color=theme.PRIMARIO
            )
            barra.grid(row=fila, column=0, sticky="ew", padx=16, pady=(0, 8))
            barra.start()
            return piezas.texto_suave(
                panel, fila + 1, f"Conectando con {r.ip}:{r.puerto}… puede tardar unos segundos."
            )
        d = prueba.diagnostico
        if d is None:
            fila = piezas.fila_insignias(
                panel, fila, [("No responde", theme.ERROR, theme.ERROR_FONDO)]
            )
            fila = piezas.texto_suave(panel, fila, prueba.error, abajo=6)
            return piezas.texto_suave(panel, fila, _AYUDA_SIN_RESPUESTA)

        fila = piezas.fila_insignias(
            panel,
            fila,
            [("Responde", theme.EXITO, theme.EXITO_FONDO)]
            + ([("Hora desfasada", theme.ALERTA, theme.ALERTA_FONDO)] if d.hora_desfasada else []),
        )
        ctk.CTkFrame(panel, height=6, fg_color="transparent").grid(row=fila, column=0)
        fila += 1
        lineas: List[Tuple[str, str]] = []
        if d.info.nombre_equipo:
            lineas.append(("Equipo", d.info.nombre_equipo))
        if d.info.serie:
            lineas.append(("Serie", d.info.serie))
        if d.info.firmware:
            lineas.append(("Firmware", d.info.firmware))
        if d.info.hora is not None and d.desfase_segundos is not None:
            formato_hora = (
                "%H:%M" if d.info.hora.date() == d.probado_en.date() else "%d/%m/%Y %H:%M"
            )
            lineas.append(
                (
                    "Hora del reloj",
                    f"{d.info.hora.strftime(formato_hora)} · {texto_desfase(d.desfase_segundos)}",
                )
            )
        if d.info.capacidad_marcas > 0:
            uso = round((d.uso_memoria or 0) * 100)
            lineas.append(
                (
                    "Marcas guardadas",
                    f"{_miles(d.info.marcas)} de {_miles(d.info.capacidad_marcas)} ({uso} %)",
                )
            )
        lineas.append(("Probado", d.probado_en.strftime("%H:%M")))
        fila = piezas.bloque_datos(panel, fila, lineas)
        ctk.CTkFrame(panel, height=8, fg_color="transparent").grid(row=fila, column=0)
        fila += 1

        if d.hora_desfasada:
            fila = piezas.boton_accion(
                panel,
                fila,
                "Poniendo en hora…" if prueba.ajustando_hora else "Poner en hora",
                lambda: self._poner_en_hora(r),
                motivo_deshabilitado="Espere a que termine." if prueba.ajustando_hora else None,
            )
            fila = piezas.texto_suave(
                panel,
                fila,
                f"Usa la hora de esta computadora ({datetime.now().strftime('%H:%M')}). "
                "Con la hora corrida, las llegadas y salidas se calculan mal.",
                abajo=4,
            )
        if d.memoria_casi_llena:
            fila = piezas.texto_suave(
                panel,
                fila,
                "La memoria de marcas está casi llena: sincronice y luego bórrela desde el "
                "reloj para que no deje de guardar marcas.",
                abajo=4,
            )

        usuarios = len(d.info.usuarios)
        fila = piezas.titulo_seccion(panel, fila, f"Usuarios en el reloj ({usuarios})")
        if d.usuarios_sin_empleado:
            n = len(d.usuarios_sin_empleado)
            fila = piezas.texto_suave(
                panel,
                fila,
                (
                    "1 usuario no corresponde a ningún empleado activo:"
                    if n == 1
                    else f"{n} usuarios no corresponden a ningún empleado activo:"
                ),
                abajo=6,
            )
            fila = piezas.lista_items(
                panel,
                fila,
                [(f"ID {u.user_id}", u.nombre or "Sin nombre") for u in d.usuarios_sin_empleado],
            )
        elif usuarios:
            fila = piezas.texto_suave(
                panel, fila, "Todos corresponden a un empleado activo.", abajo=4
            )

        faltan = [
            (f"{e.nombres} {e.apellidos}", "Sin ID del reloj") for e in d.empleados_sin_id
        ] + [
            (f"{e.nombres} {e.apellidos}", f"ID {e.zkteco_id} no está en el reloj")
            for e in d.empleados_no_registrados
        ]
        fila = piezas.titulo_seccion(panel, fila, f"Empleados que no pueden marcar ({len(faltan)})")
        if not faltan:
            fila = piezas.texto_suave(
                panel, fila, "Todos los empleados activos están registrados en este reloj."
            )
            return fila
        fila = piezas.lista_items(
            panel, fila, sorted(faltan, key=lambda t: t[0].casefold()), debajo=True
        )
        return piezas.texto_suave(
            panel,
            fila,
            "Regístrelos en el reloj y anote su ID en Empleados; si no, aparecen como ausentes.",
        )

    def _detalle_push(self, panel: ctk.CTkScrollableFrame, fila: int, r: Dispositivo) -> int:
        """Estado de la recepción automática y qué configurar en el reloj."""
        estado = self._c.estado_push()
        ahora = datetime.now(timezone.utc)
        fila = piezas.titulo_seccion(panel, fila, "Recepción automática", arriba=12)
        if not estado.disponible:
            return piezas.texto_suave(panel, fila, "Esta instalación no puede recibir marcas push.")
        if not estado.activo:
            fila = piezas.texto_suave(
                panel,
                fila,
                estado.error or "La app todavía no está escuchando a los relojes push.",
                abajo=6,
            )
            return piezas.boton_accion(
                panel, fila, "Empezar a recibir", self._empezar_recepcion, primario=True
            )
        lineas = [
            ("Estado", estado_push(r, ahora)),
            ("Último contacto", hace(r.ultimo_contacto, ahora) if r.ultimo_contacto else "Nunca"),
            ("Número de serie", r.numero_serie or "—"),
        ]
        fila = piezas.bloque_datos(panel, fila, lineas)
        if estado_push(r, ahora) != "Conectado":
            fila = piezas.texto_suave(
                panel,
                fila + 1,
                "Mientras no se conecta, el reloj guarda las marcas y las envía todas "
                "cuando vuelve a tener comunicación.",
                abajo=0,
            )
        fila = piezas.titulo_seccion(panel, fila, "Qué poner en el reloj")
        servidor = estado.direcciones[0] if estado.direcciones else "IP de esta computadora"
        datos = [
            ("En el menú", "Comunicación › ADMS"),
            ("Dirección del servidor", servidor),
            ("Puerto", str(estado.puerto)),
            ("HTTPS / Proxy", "No"),
        ]
        if len(estado.direcciones) > 1:
            datos.append(("Otras IP de esta PC", ", ".join(estado.direcciones[1:])))
        fila = piezas.bloque_datos(panel, fila, datos)
        return piezas.texto_suave(
            panel,
            fila + 1,
            "En algunos relojes la opción se llama «Servidor en la nube» o «Cloud Server». "
            "Esta computadora necesita una IP fija y la app abierta para recibir las marcas. "
            "La primera vez, Windows puede preguntar si permite la conexión: elija «Permitir».",
            abajo=0,
        )

    def _empezar_recepcion(self) -> None:
        estado = self._c.asegurar_recepcion_push()
        if estado.activo:
            ip = estado.direcciones[0] if estado.direcciones else "la IP de esta computadora"
            self.aviso.mostrar(
                f"Escuchando relojes push. En el reloj ponga servidor {ip}, puerto "
                f"{estado.puerto}; cuando se conecte aparecerá aquí para agregarlo.",
                segundos=12,
            )
        else:
            self.aviso.mostrar(estado.error or "No se pudo empezar a recibir.", error=True)
        self._pintar_boton_push()
        self.mostrar_detalle()

    def _pintar_boton_push(self) -> None:
        if not hasattr(self, "_btn_push"):
            return  # primera carga: el botón se crea después
        estado = self._c.estado_push()
        if not estado.disponible:
            self._btn_push.grid_remove()
        elif estado.activo:
            self._btn_push.configure(text=f"Escuchando (puerto {estado.puerto})", state="disabled")
        else:
            self._btn_push.configure(text="Detectar relojes push", state="normal")

    # ── Relojes push sin registrar ────────────────────────────────────────

    def _pintar_banda(self) -> None:
        piezas.limpiar(self.banda)
        if not self._detectados:
            self.banda.grid_remove()
            return
        for i, d in enumerate(self._detectados[:3]):
            caja = ctk.CTkFrame(self.banda, fg_color=theme.ALERTA_FONDO, corner_radius=8)
            caja.grid(row=i, column=0, sticky="ew", pady=(0 if i == 0 else 6, 0))
            caja.grid_columnconfigure(0, weight=1)
            espera = " Tiene marcas esperando para enviar." if d.con_marcas else ""
            ctk.CTkLabel(
                caja,
                text=(
                    f"Se conectó un reloj que no está registrado: serie {d.numero_serie}, "
                    f"IP {d.ip}.{espera}"
                ),
                text_color=theme.ALERTA,
                font=theme.fuente(theme.TAM_PEQUENO),
                anchor="w",
                justify="left",
                wraplength=640,
            ).grid(row=0, column=0, sticky="w", padx=14, pady=8)
            piezas.boton_secundario(
                caja, "Agregar este reloj", functools.partial(self._agregar_detectado, d), ancho=150
            ).grid(row=0, column=1, padx=8, pady=6)
        self.banda.grid()

    def _agregar_detectado(self, d: RelojDetectado) -> None:
        self._abrir_formulario(
            "Agregar reloj",
            None,
            DispositivoFormDialogInicial(ip=d.ip, modo=MODO_PUSH, serie=d.numero_serie),
        )

    # ── Vigilancia (los relojes push cambian solos) ───────────────────────

    def _vigilar(self) -> None:
        if not self.winfo_exists():
            return
        try:
            if self.winfo_viewable() and self._c.estado_push().activo:
                self._refrescar_si_cambio()
        except Exception:  # noqa: BLE001 — una vuelta fallida no debe frenar las siguientes
            self._log.exception("No se pudo revisar los relojes push")
        self.after(_VIGILAR_CADA_MS, self._vigilar)

    def _refrescar_si_cambio(self) -> None:
        """Refresca solo si cambió algo de lo que se ve (no en cada latido del reloj)."""
        firma = self._firma_de(
            self._c.list_dispositivos(solo_activos=False), self._c.relojes_detectados()
        )
        if firma != self._firma:
            self.refrescar(seleccionar=self.tabla.seleccionado())

    @staticmethod
    def _firma_de(relojes: List[Dispositivo], detectados: List[RelojDetectado]) -> str:
        ahora = datetime.now(timezone.utc)
        return repr(
            (
                [(d.numero_serie, d.con_marcas) for d in detectados],
                [
                    (r.id, _corto(r.ultimo_contacto, ahora), estado_push(r, ahora))
                    for r in relojes
                    if r.es_push
                ],
            )
        )

    def _probar(self, r: Dispositivo) -> None:
        assert r.id is not None
        reloj_id = r.id
        prueba = _Prueba()
        self._pruebas[reloj_id] = prueba
        self._repintar_si_elegido(reloj_id)

        def listo(d: DiagnosticoReloj) -> None:
            prueba.probando, prueba.diagnostico = False, d
            self._repintar_si_elegido(reloj_id)

        def fallo(exc: BaseException) -> None:
            prueba.probando = False
            prueba.error = str(exc) if isinstance(exc, _ERRORES) else "Error inesperado."
            if not isinstance(exc, _ERRORES):
                self._log.error("Probar reloj falló", exc_info=exc)
            self._repintar_si_elegido(reloj_id)

        run_async_ui(self.winfo_toplevel(), lambda: self._c.probar_reloj(reloj_id), listo, fallo)

    def _poner_en_hora(self, r: Dispositivo) -> None:
        assert r.id is not None
        reloj_id = r.id
        prueba = self._pruebas.get(reloj_id)
        if prueba is None:
            return
        prueba.ajustando_hora = True
        self._repintar_si_elegido(reloj_id)

        def listo(puesta: datetime) -> None:
            prueba.ajustando_hora = False
            if self.winfo_exists():
                self.aviso.mostrar(f"«{r.nombre}» quedó con la hora {puesta.strftime('%H:%M')}.")
            self._probar(r)

        def fallo(exc: BaseException) -> None:
            prueba.ajustando_hora = False
            if self.winfo_exists():
                texto = str(exc) if isinstance(exc, _ERRORES) else "Error inesperado."
                self.aviso.mostrar(f"No se pudo poner en hora: {texto}", error=True)
            self._repintar_si_elegido(reloj_id)

        run_async_ui(
            self.winfo_toplevel(), lambda: self._c.poner_reloj_en_hora(reloj_id), listo, fallo
        )

    def _repintar_si_elegido(self, reloj_id: int) -> None:
        """Repinta el panel y lo baja hasta "Conexión" (está debajo de las acciones)."""
        if self.winfo_exists() and self.tabla.seleccionado() == str(reloj_id):
            self.mostrar_detalle()
            piezas.desplazar_hasta_fila(self.panel, self._fila_conexion)

    # ── Acciones ──────────────────────────────────────────────────────────

    def _doble_click(self, iid: str) -> None:
        r = self._buscar(iid)
        if r is not None and r.is_active:
            self._editar(r)

    def _suprimir(self, iid: str) -> None:
        r = self._buscar(iid)
        if r is not None and r.is_active:
            self._archivar(r)

    def nuevo(self) -> None:
        self._abrir_formulario("Nuevo reloj", None, DispositivoFormDialogInicial())

    def _editar(self, r: Dispositivo) -> None:
        self._abrir_formulario(
            "Editar reloj",
            r,
            DispositivoFormDialogInicial(
                nombre=r.nombre,
                ip=r.ip,
                puerto=r.puerto,
                modo=r.modo,
                clave=r.clave_comunicacion,
                udp=r.usar_udp,
                serie=r.numero_serie or "",
            ),
        )

    def _abrir_formulario(
        self, titulo: str, r: Optional[Dispositivo], inicial: "DispositivoFormDialogInicial"
    ) -> None:
        guardado: Dict[str, Dispositivo] = {}
        modo_guardado = {"push": False}

        def submit(p: DispositivoFormPayload) -> Optional[str]:
            try:
                if r is None or r.id is None:
                    guardado["r"] = self._c.create_dispositivo(
                        p["nombre"],
                        p["ip"],
                        p["puerto"],
                        modo=p["modo"],
                        clave_comunicacion=p["clave_comunicacion"],
                        usar_udp=p["usar_udp"],
                        numero_serie=p["numero_serie"],
                    )
                else:
                    self._c.update_dispositivo(
                        r.id,
                        p["nombre"],
                        p["ip"],
                        p["puerto"],
                        modo=p["modo"],
                        clave_comunicacion=p["clave_comunicacion"],
                        usar_udp=p["usar_udp"],
                        numero_serie=p["numero_serie"] or "",
                    )
                    guardado["r"] = r
            except _ERRORES as exc:
                return str(exc)
            modo_guardado["push"] = p["modo"] == MODO_PUSH
            return None

        self.wait_window(
            DispositivoFormDialog(
                self,
                title=titulo,
                on_submit=submit,
                initial_nombre=inicial.nombre,
                initial_ip=inicial.ip,
                initial_puerto=inicial.puerto,
                initial_modo=inicial.modo,
                initial_clave=inicial.clave,
                initial_udp=inicial.udp,
                initial_serie=inicial.serie,
                es_nuevo=r is None,
            )
        )
        if "r" not in guardado:
            return
        self.filtro = _ACTIVOS
        self.refrescar(seleccionar=str(guardado["r"].id))
        mensaje = (
            f"Reloj «{guardado['r'].nombre}» registrado." if r is None else "Cambios guardados."
        )
        if modo_guardado["push"]:
            estado = self._c.asegurar_recepcion_push()
            if not estado.activo:
                self.aviso.mostrar(estado.error or "No se pudo empezar a recibir.", error=True)
                return
            self.refrescar(seleccionar=str(guardado["r"].id))
            mensaje += " Configure el reloj con los datos del panel."
        self.aviso.mostrar(mensaje)

    def _archivar(self, r: Dispositivo) -> None:
        assert r.id is not None
        if not messagebox.askyesno(
            "Archivar reloj",
            f"¿Archivar «{r.nombre}»?\n\nYa no aparecerá para sincronizar. Las marcas ya "
            "descargadas se conservan.",
            parent=self,
        ):
            return
        try:
            self._c.archive_dispositivo(r.id)
        except _ERRORES as exc:
            self.aviso.mostrar(str(exc), error=True)
            return
        self.refrescar()
        self.aviso.mostrar(f"«{r.nombre}» archivado. Lo encuentra en «Archivados».")

    def _reactivar(self, r: Dispositivo) -> None:
        assert r.id is not None
        try:
            self._c.unarchive_dispositivo(r.id)
        except _ERRORES as exc:
            self.aviso.mostrar(str(exc), error=True)
            return
        self.filtro = _ACTIVOS
        self.refrescar(seleccionar=str(r.id))
        self.aviso.mostrar(f"«{r.nombre}» vuelve a estar activo.")
