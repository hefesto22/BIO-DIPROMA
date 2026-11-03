"""Vista principal (MainWindow).

Layout:
    ┌─────────────────────────────────────────────────┐
    │  Sidebar  │            Contenido                │
    │  (botones │    (la vista del módulo elegido)    │
    │   por per-│                                     │
    │   miso)   │                                     │
    │           │                                     │
    ├───────────┴─────────────────────────────────────┤
    │  Status bar: usuario · rol · estado ZKTeco      │
    └─────────────────────────────────────────────────┘

Cambio de módulo rápido:
    Cada vista se construye UNA vez por sesión y queda guardada; al
    cambiar de módulo la anterior solo se oculta (``grid_remove``) y la
    elegida vuelve a mostrarse al instante. Justo después de mostrarla se
    llama a su ``al_mostrar()`` (si lo tiene) para poner los datos al día:
    algo pudo cambiar en otro módulo. Además, tras el login se construyen
    en los ratos libres las vistas que el usuario todavía no abrió, para
    que hasta la primera vez sea inmediata. Al cerrar sesión se destruye
    el MainFrame y con él todas las vistas guardadas.

Doble defensa de permisos:
    1. El sidebar SOLO pinta los botones de módulos cuyo permiso la
       sesión posee (filtro vía ``permission_guard.filter_visible``).
    2. Aún así, el ``MainController.open_*`` está decorado con
       ``@require_permission`` — si un bug futuro dejara un botón visible,
       el click lanzaría ``PermissionDeniedError`` y NO montaría la vista.

Estado ZKTeco en la status bar:
    - Default al arrancar: "Sin conexión" (no se conoce historial vivo).
    - Tras un sync OK: ``SincronizacionView`` invoca un callback que
      llega hasta ``refresh_status_zkteco(...)`` y actualiza el texto
      a "última sync HH:MM" (Sub-2.4c / fix cosmético post-Plan B).
    - Phase 4 podrá reemplazar el callback por un poller en tiempo
      real sin tocar este método.
"""

from __future__ import annotations

import functools
import logging
from typing import Callable, Dict, List, Optional, Set

import customtkinter as ctk

import config
from core.services.errors import PermissionDeniedError
from core.services.session import Session
from ui.controllers.main_controller import MainController
from ui.guards.permission_guard import filter_visible
from ui import theme
from ui.menu_items import MENU_ITEMS, MenuItem
from ui.views.placeholder_view import PlaceholderView
from ui.views.welcome_view import ResumenSistema, WelcomeView

# Factory de vista: recibe el contenedor padre y devuelve el widget a montar.
# El composition root (``ui/app.py``) construye estas factories con los
# controllers ya cableados — MainFrame solo las invoca por ``code``.
ViewFactory = Callable[[ctk.CTkBaseClass], ctk.CTkBaseClass]

# Pausa antes de refrescar los datos de una vista guardada: primero se
# pinta el módulo (se siente instantáneo) y después se actualiza.
_PAUSA_REFRESCO_MS = 15
# Precarga tras el login: cuándo empieza y cuánto se descansa entre vistas
# para que la ventana siga respondiendo a los clics.
_PRECARGA_INICIO_MS = 700
_PRECARGA_ENTRE_MS = 120


class MainFrame(ctk.CTkFrame):
    """Frame principal de la app post-login."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        session: Session,
        controller: MainController,
        on_logout: Callable[[], None],
        view_factories: Optional[Dict[str, ViewFactory]] = None,
        initial_zkteco_status: str = "sin conexión",
        resumen_sistema: Optional[ResumenSistema] = None,
        precargar: bool = False,
    ) -> None:
        """Construye el frame.

        Args:
            master: Ventana raíz donde se monta.
            session: Sesión activa — determina qué botones del sidebar
                se renderizan y qué se muestra en la status bar.
            controller: Controller ya inyectado (``open_view`` apunta a
                este frame vía callback).
            on_logout: Callback invocado cuando el usuario cierra sesión.
            view_factories: Mapa opcional ``{code: factory}``. Si el
                ``code`` abierto está en el mapa, se monta la vista real;
                si no, cae al ``PlaceholderView`` genérico (módulo aún
                sin implementación).
            initial_zkteco_status: Texto inicial del segmento ZKTeco de
                la status bar. El composition root lo construye leyendo
                la última sincronización OK de BD para que el estado
                persista entre logouts/logins (Sub-2.7c).
            precargar: Si es ``True``, tras mostrarse construye en ratos
                libres las vistas de los módulos visibles que aún no se
                abrieron (la app lo activa; los tests no lo necesitan).
        """
        super().__init__(master, corner_radius=0)
        self._session = session
        self._controller = controller
        self._on_logout = on_logout
        self._view_factories: Dict[str, ViewFactory] = view_factories or {}
        self._initial_zkteco_status = initial_zkteco_status
        self._resumen_sistema = resumen_sistema
        self._log = logging.getLogger(self.__class__.__name__)

        # Index por code para mostrar el título/descripción en el placeholder.
        self._items_by_code: Dict[str, MenuItem] = {item.code: item for item in MENU_ITEMS}

        # Widget actual del área central.
        self._current_view: Optional[ctk.CTkBaseClass] = None
        # Vistas ya construidas por code: se ocultan y se vuelven a mostrar.
        self._vistas: Dict[str, ctk.CTkBaseClass] = {}
        self._botones: Dict[str, ctk.CTkButton] = {}
        self._activo: Optional[str] = None
        self._sin_precarga: Set[str] = set()
        self._job_refresco: Optional[str] = None
        self._job_precarga: Optional[str] = None

        self._construir_ui()
        self._mostrar_bienvenida()
        if precargar:
            self._job_precarga = self.after(_PRECARGA_INICIO_MS, self._precargar_siguiente)

    def destroy(self) -> None:
        for job in (self._job_refresco, self._job_precarga):
            if job is not None:
                self.after_cancel(job)
        self._job_refresco = self._job_precarga = None
        self._vistas.clear()
        super().destroy()

    # ── Construcción del árbol de widgets ─────────────────────────────────

    def _construir_ui(self) -> None:
        """Arma sidebar, área de contenido y status bar con grid."""
        # Grid raíz: columna 0 fija (sidebar), columna 1 elástica (contenido).
        # Fila 0 elástica (cuerpo), fila 1 fija (status bar).
        self.grid_columnconfigure(0, weight=0, minsize=220)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0)

        self._sidebar = self._construir_sidebar()
        self._sidebar.grid(row=0, column=0, sticky="nsew")

        self._content = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self._content.grid(row=0, column=1, sticky="nsew")
        self._content.grid_rowconfigure(0, weight=1)
        self._content.grid_columnconfigure(0, weight=1)

        self._status_bar = self._construir_status_bar()
        self._status_bar.grid(row=1, column=0, columnspan=2, sticky="sew")

    def _construir_sidebar(self) -> ctk.CTkFrame:
        """Arma el sidebar con los botones filtrados por permiso."""
        sidebar = ctk.CTkFrame(self, corner_radius=0, width=220)

        # Header del sidebar: marca de la app (sale de config.py).
        marca = ctk.CTkFrame(sidebar, fg_color="transparent")
        marca.pack(padx=14, pady=(20, 22), fill="x")
        ctk.CTkLabel(
            marca,
            text="BD",
            width=40,
            height=40,
            corner_radius=10,
            fg_color=theme.PRIMARIO,
            text_color=theme.TEXTO_SOBRE_PRIMARIO,
            font=ctk.CTkFont(size=15, weight="bold"),
        ).grid(row=0, column=0, rowspan=2, padx=(0, 10))
        ctk.CTkLabel(
            marca,
            text=config.APP_NAME,
            font=ctk.CTkFont(size=16, weight="bold"),
            anchor="w",
            height=20,
        ).grid(row=0, column=1, sticky="sw")
        ctk.CTkLabel(
            marca,
            text=config.APP_TAGLINE,
            font=ctk.CTkFont(size=11),
            text_color=theme.TEXTO_SUAVE,
            anchor="w",
            height=16,
        ).grid(row=1, column=1, sticky="nw")

        # Botones de módulos visibles para la sesión activa.
        self._fuente_menu = ctk.CTkFont()
        self._fuente_menu_activo = ctk.CTkFont(weight="bold")
        visibles = filter_visible(MENU_ITEMS, self._session)
        for item in visibles:
            btn = ctk.CTkButton(
                sidebar,
                text=item.label,
                anchor="w",
                height=36,
                font=self._fuente_menu,
                fg_color="transparent",
                text_color=("gray10", "gray90"),
                hover_color=("gray80", "gray25"),
                command=lambda code=item.code: self._abrir(code),
            )
            btn.pack(padx=8, pady=2, fill="x")
            self._botones[item.code] = btn

        # Spacer para empujar el botón de logout al fondo.
        spacer = ctk.CTkFrame(sidebar, fg_color="transparent")
        spacer.pack(expand=True, fill="both")

        btn_logout = ctk.CTkButton(
            sidebar,
            text="Cerrar sesión",
            height=36,
            fg_color=theme.SECUNDARIO,
            hover_color=theme.SECUNDARIO_HOVER,
            text_color=theme.TEXTO,
            command=self._on_btn_logout,
        )
        btn_logout.pack(padx=8, pady=(8, 16), fill="x")

        return sidebar

    def _construir_status_bar(self) -> ctk.CTkFrame:
        """Barra inferior con usuario, rol y estado ZKTeco."""
        bar = ctk.CTkFrame(self, corner_radius=0, height=28)

        # Estado ZKTeco se guarda separado para poder refrescarlo a
        # demanda sin reconstruir el resto del label. El valor inicial
        # lo arma el composition root leyendo la última sync OK de BD
        # (Sub-2.7c) — así el estado persiste tras logout/login.
        self._zkteco_status = self._initial_zkteco_status
        self._status_label = ctk.CTkLabel(
            bar,
            text=self._build_status_text(),
            font=ctk.CTkFont(size=11),
            text_color=("gray30", "gray70"),
            anchor="w",
        )
        self._status_label.pack(padx=12, pady=4, fill="x")
        return bar

    def _build_status_text(self) -> str:
        """Compone el texto completo del status bar.

        Centraliza el formato para que ``refresh_status_zkteco`` no
        duplique el orden de los campos. El separador "·" rodeado de
        espacios mantiene legibilidad incluso con fuentes condensadas.
        """
        return (
            f"Usuario: {self._session.username}"
            f"   ·   Rol: {self._session.role_code}"
            f"   ·   ZKTeco: {self._zkteco_status}"
        )

    # ── API pública para refrescar el estado ZKTeco ───────────────────────

    def refresh_status_zkteco(self, nuevo_estado: str) -> None:
        """Actualiza el segmento "ZKTeco: ..." de la status bar.

        Lo invoca el composition root tras un sync exitoso (vía un
        callback inyectado en la ``SincronizacionView``). Es idempotente:
        llamarlo dos veces seguidas con el mismo texto no causa flicker.

        Args:
            nuevo_estado: Texto que reemplaza el estado actual. Se
                espera un string corto, en español, sin punto final
                (ej. ``"última sync 14:32"`` o ``"sin conexión"``).
        """
        if nuevo_estado == self._zkteco_status:
            return
        self._zkteco_status = nuevo_estado
        self._status_label.configure(text=self._build_status_text())

    # ── Handlers ──────────────────────────────────────────────────────────

    def _abrir(self, code: str) -> None:
        """Click en un botón del sidebar.

        Delega al controller (que valida el permiso con el decorador);
        el controller invoca el callback ``open_view`` que montamos aquí.
        Si el permiso falla, capturamos y mostramos un aviso en el área
        central — sin cerrar la app.

        Cualquier otra excepción al construir la vista también se captura
        para evitar que la app quede en estado inconsistente: en un .exe
        ``--windowed`` (sin consola) un raise no manejado se pierde
        silenciosamente y el usuario solo ve que el click "no hace nada".
        Loggeamos con traceback completo y mostramos un placeholder.
        """
        try:
            self._controller.open_by_code(code)
        except PermissionDeniedError:
            # Defensa secundaria: un botón visible sin permiso indicaría un
            # bug de filter_visible. Lo reportamos pero no tumbamos la app.
            self._log.error("Click en módulo sin permiso: %s", code)
            self._mostrar_acceso_denegado(code)
        except Exception:
            # Boundary handler de UI: la regla "nunca capturar Exception
            # genérica" se relaja aquí porque sin este catch, los errores
            # del bundle PyInstaller (hidden imports faltantes, data files
            # no incluidos, etc.) se pierden y la app queda muda.
            self._log.exception("Error inesperado al abrir módulo '%s'", code)
            self._mostrar_error_inesperado(code)

    def _on_btn_logout(self) -> None:
        """Click en el botón de cerrar sesión."""
        self._log.info("Logout: usuario=%s", self._session.username)
        self._on_logout()

    # ── Swap de vistas en el área central ─────────────────────────────────

    def _montar_view(self, nueva: ctk.CTkBaseClass, code: Optional[str] = None) -> None:
        """Muestra ``nueva`` en el contenedor y saca la anterior.

        La anterior se oculta si es una vista guardada (para volver rápido)
        y se destruye si no lo es (bienvenida, avisos, placeholders).
        """
        anterior = self._current_view
        if anterior is not None and anterior is not nueva:
            if self._es_guardada(anterior):
                anterior.grid_remove()
            else:
                anterior.destroy()
        nueva.grid(row=0, column=0, sticky="nsew")
        # Encima de todo: una vista precargada quedó más abajo en el apilado.
        nueva.lift()
        self._current_view = nueva
        self._marcar_activo(code)

    def _es_guardada(self, vista: ctk.CTkBaseClass) -> bool:
        return any(v is vista for v in self._vistas.values())

    def _marcar_activo(self, code: Optional[str]) -> None:
        """Resalta en el menú el módulo abierto: respuesta visual inmediata al clic."""
        if code == self._activo:
            return
        anterior = self._botones.get(self._activo or "")
        if anterior is not None:
            anterior.configure(fg_color="transparent", font=self._fuente_menu)
        boton = self._botones.get(code or "")
        if boton is not None:
            boton.configure(fg_color=theme.SELECCION, font=self._fuente_menu_activo)
        self._activo = code

    def _mostrar_bienvenida(self) -> None:
        """Monta la pantalla de bienvenida del MainFrame.

        Sub-3.4: si el composition root pasó un ``ResumenSistema``,
        se monta el ``WelcomeView`` rico (saludo + stats + tip). Si no
        — ej. tests viejos que no inyectan resumen — cae al
        ``PlaceholderView`` legacy para preservar retrocompatibilidad.
        """
        if self._resumen_sistema is not None:
            bienvenida: ctk.CTkBaseClass = WelcomeView(
                self._content,
                username=self._session.username,
                role_code=self._session.role_code,
                resumen=self._resumen_sistema,
            )
        else:
            bienvenida = PlaceholderView(
                self._content,
                title=f"Bienvenido, {self._session.username}",
                description=(
                    "Seleccione un módulo del menú lateral para comenzar. "
                    "Solo se muestran los módulos disponibles para su rol "
                    f"({self._session.role_code})."
                ),
                mostrar_aviso=False,
            )
        self._montar_view(bienvenida)

    def _mostrar_vista_por_code(self, code: str) -> None:
        """Muestra la vista del módulo; si no hay factory, ``PlaceholderView``.

        El lookup es por ``code`` del ``MenuItem`` (ej. ``"settings"``).
        La factory recibe el contenedor padre y devuelve el widget ya
        construido con todas sus dependencias inyectadas. La vista se
        construye una sola vez: las siguientes veces se vuelve a mostrar la
        misma y se refrescan sus datos justo después de pintarla.
        """
        self._cancelar_refresco()
        guardada = self._vistas.get(code)
        if guardada is not None:
            self._montar_view(guardada, code)
            self._job_refresco = self.after(
                _PAUSA_REFRESCO_MS, functools.partial(self._refrescar_vista, guardada)
            )
            return
        factory = self._view_factories.get(code)
        if factory is not None:
            vista = factory(self._content)
            self._vistas[code] = vista
            self._montar_view(vista, code)
            return
        item = self._items_by_code[code]
        placeholder = PlaceholderView(
            self._content,
            title=item.label,
            description=item.description,
        )
        self._montar_view(placeholder, code)

    def _mostrar_acceso_denegado(self, code: str) -> None:
        """Monta un placeholder de acceso denegado (fallback defensivo)."""
        aviso = PlaceholderView(
            self._content,
            title="Acceso denegado",
            description=(
                f"Su rol actual ({self._session.role_code}) no tiene permiso "
                f"para abrir este módulo ({code}). Si considera que es un "
                "error, contacte al SUPERADMIN."
            ),
        )
        self._montar_view(aviso, code)

    def _mostrar_error_inesperado(self, code: str) -> None:
        """Placeholder cuando una excepción no esperada rompe la apertura.

        Mensaje en español al usuario sin stack trace; el traceback ya
        quedó loggeado por ``_abrir`` con ``logger.exception``.
        """
        item = self._items_by_code.get(code)
        titulo_modulo = item.label if item is not None else code
        aviso = PlaceholderView(
            self._content,
            title="Error inesperado",
            description=(
                f"No se pudo abrir el módulo «{titulo_modulo}». "
                "Revise el archivo de log para más detalles "
                "o contacte al administrador."
            ),
        )
        self._montar_view(aviso, code)

    # ── Vistas guardadas: refresco y precarga ─────────────────────────────

    def _cancelar_refresco(self) -> None:
        if self._job_refresco is not None:
            self.after_cancel(self._job_refresco)
            self._job_refresco = None

    def _refrescar_vista(self, vista: ctk.CTkBaseClass) -> None:
        """Pone al día los datos de una vista que vuelve a mostrarse."""
        self._job_refresco = None
        if vista is not self._current_view:
            return  # el usuario ya se fue a otro módulo
        al_mostrar = getattr(vista, "al_mostrar", None)
        if not callable(al_mostrar):
            return
        try:
            al_mostrar()
        except Exception:  # noqa: BLE001 — un refresco fallido no debe tumbar la app
            self._log.exception("Error refrescando la vista %s", type(vista).__name__)

    def _pendientes_de_precarga(self) -> List[str]:
        return [
            item.code
            for item in filter_visible(MENU_ITEMS, self._session)
            if item.code in self._view_factories
            and item.code not in self._vistas
            and item.code not in self._sin_precarga
        ]

    def _precargar_siguiente(self) -> None:
        """Construye la siguiente vista aún no abierta, de a una por vez.

        Se acomoda un momento DETRÁS de la vista actual (misma celda, más
        abajo en el apilado): así Tk calcula su tamaño y la dibuja sin que
        se vea, y la primera vez que el usuario la abre ya está lista.
        """
        self._job_precarga = None
        pendientes = self._pendientes_de_precarga()
        if not pendientes:
            return
        code = pendientes[0]
        try:
            vista = self._view_factories[code](self._content)
        except Exception:  # noqa: BLE001 — se reintentará (y avisará) al abrirla
            self._log.exception("No se pudo precargar el módulo '%s'", code)
            self._sin_precarga.add(code)
            self._job_precarga = self.after(_PRECARGA_ENTRE_MS, self._precargar_siguiente)
            return
        self._vistas[code] = vista
        actual = self._current_view
        if actual is None:
            self._job_precarga = self.after(_PRECARGA_ENTRE_MS, self._precargar_siguiente)
            return
        # Justo debajo de la vista actual (no "al fondo": ahí la taparía el
        # lienzo de fondo del contenedor, que también es hijo suyo).
        vista.grid(row=0, column=0, sticky="nsew")
        vista.lower(actual)
        self._job_precarga = self.after(
            _PRECARGA_ENTRE_MS, functools.partial(self._ocultar_precargada, vista)
        )

    def _ocultar_precargada(self, vista: ctk.CTkBaseClass) -> None:
        self._job_precarga = None
        if vista is not self._current_view:  # el usuario no la abrió mientras tanto
            vista.grid_remove()
        self._job_precarga = self.after(_PRECARGA_ENTRE_MS, self._precargar_siguiente)

    # ── Callback público para el controller ───────────────────────────────

    def open_view(self, code: str) -> None:
        """Callback que el controller invoca tras pasar el decorador de permisos.

        Se expone como método público para que el composition root pueda
        pasárselo al ``MainController`` al construirlo.
        """
        self._mostrar_vista_por_code(code)
