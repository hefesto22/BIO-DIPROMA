"""``WelcomeView`` — pantalla de bienvenida del MainFrame.

Reemplaza al ``PlaceholderView`` que se mostraba al iniciar sesión por
una vista más rica:

    - Saludo dinámico según hora del día (Buenos días / tardes / noches).
    - Username + rol + fecha en español.
    - Mini-resumen del sistema (empleados activos, dispositivos K40,
      última sincronización).
    - Tip operativo según el rol.
    - Footer con la versión de la app.

Diseño visual (aprox 720x520 px contenido):

    ┌───────────────────────────────────────────────────────────────────┐
    │                                                                    │
    │  Buenas noches, mauricio 👋                                        │
    │  SUPERADMIN · viernes 1 de mayo de 2026                            │
    │                                                                    │
    │  Resumen del sistema                                               │
    │  ┌──────────┐  ┌──────────┐  ┌──────────────────────┐              │
    │  │    3     │  │    1     │  │  Última sync:        │              │
    │  │ Empleados│  │ Dispos.  │  │  hoy 23:16           │              │
    │  └──────────┘  └──────────┘  └──────────────────────┘              │
    │                                                                    │
    │  💡 Tip: ...                                                        │
    │                                                                    │
    │                              BIO-DIPROMA v0.1.0 · DIPROMA          │
    └───────────────────────────────────────────────────────────────────┘

La vista NO ejecuta queries por sí misma — recibe un ``ResumenSistema``
ya calculado por el composition root, manteniendo SOLID-S y SOLID-D.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Optional

import customtkinter as ctk

import config

# Mapas en español para el saludo y la fecha — evitamos ``locale.setlocale``
# por portabilidad (Windows + macOS no garantizan los mismos locales).
_DIAS_SEMANA = (
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
)
_MESES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


@dataclass(frozen=True)
class ResumenSistema:
    """Datos pre-calculados que la vista necesita renderizar.

    El composition root los arma una vez (al construir MainFrame) para
    no acoplar la vista a los servicios. Si en el futuro queremos
    refrescar en vivo, la vista ya queda lista para aceptar un nuevo
    resumen vía un método ``refrescar(resumen)``.

    Attributes:
        empleados_activos: Cantidad de empleados ``is_active = True``.
        dispositivos_activos: Cantidad de dispositivos K40 activos.
        ultima_sync_label: Texto corto a mostrar (ej. ``"hoy 23:16"`` o
            ``"sin sincronización"``). El service lo formatea según
            qué información tenga.
        app_version: Versión de la app a mostrar en el footer.
    """

    empleados_activos: int
    dispositivos_activos: int
    ultima_sync_label: str
    app_version: str


class WelcomeView(ctk.CTkFrame):
    """Pantalla de bienvenida (Sub-3.4)."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        username: str,
        role_code: str,
        resumen: ResumenSistema,
        ahora: Optional[_dt.datetime] = None,
    ) -> None:
        """Construye la vista.

        Args:
            master: Frame/Widget padre.
            username: Username del usuario logueado.
            role_code: Código del rol (SUPERADMIN/ADMIN/REPORTES/OPERADOR).
            resumen: ``ResumenSistema`` pre-calculado.
            ahora: ``datetime`` de referencia para el saludo + fecha.
                ``None`` usa ``datetime.now()`` — inyectable para tests.
        """
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self._ahora = ahora or _dt.datetime.now()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        self._render_saludo(username)
        self._render_subtitulo(role_code)
        self._render_resumen(resumen)
        self._render_tip(role_code)
        self._render_footer(resumen.app_version)

    # ── Renders ───────────────────────────────────────────────────────────

    def _render_saludo(self, username: str) -> None:
        saludo = self._calcular_saludo(self._ahora)
        ctk.CTkLabel(
            self,
            text=f"{saludo}, {username} 👋",
            font=ctk.CTkFont(size=28, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=32, pady=(40, 4))

    def _render_subtitulo(self, role_code: str) -> None:
        fecha_es = self._fecha_en_espanol(self._ahora)
        ctk.CTkLabel(
            self,
            text=f"{role_code} · {fecha_es}",
            font=ctk.CTkFont(size=14),
            text_color=("gray35", "gray70"),
            anchor="w",
        ).grid(row=1, column=0, sticky="ew", padx=32, pady=(0, 28))

    def _render_resumen(self, resumen: ResumenSistema) -> None:
        ctk.CTkLabel(
            self,
            text="Resumen del sistema",
            font=ctk.CTkFont(size=16, weight="bold"),
            anchor="w",
        ).grid(row=2, column=0, sticky="ew", padx=32, pady=(0, 12))

        cards = ctk.CTkFrame(self, fg_color="transparent")
        cards.grid(row=3, column=0, sticky="ew", padx=24, pady=(0, 28))
        cards.grid_columnconfigure((0, 1, 2), weight=1)

        self._card(
            parent=cards,
            col=0,
            valor=str(resumen.empleados_activos),
            etiqueta="Empleados activos",
        )
        self._card(
            parent=cards,
            col=1,
            valor=str(resumen.dispositivos_activos),
            etiqueta="Dispositivos K40",
        )
        self._card(
            parent=cards,
            col=2,
            valor=resumen.ultima_sync_label,
            etiqueta="Última sincronización",
            valor_grande=False,
        )

    def _render_tip(self, role_code: str) -> None:
        tip = self._tip_para_rol(role_code)
        if tip is None:
            return
        ctk.CTkLabel(
            self,
            text=f"💡 {tip}",
            font=ctk.CTkFont(size=13, slant="italic"),
            text_color=("gray30", "gray70"),
            wraplength=720,
            justify="left",
            anchor="w",
        ).grid(row=4, column=0, sticky="new", padx=32, pady=(0, 16))

    def _render_footer(self, version: str) -> None:
        ctk.CTkLabel(
            self,
            text=f"{config.APP_NAME} v{version}  ·  {config.APP_VENDOR}",
            font=ctk.CTkFont(size=11),
            text_color=("gray45", "gray55"),
            anchor="e",
        ).grid(row=5, column=0, sticky="ew", padx=32, pady=(0, 18))

    # ── Helpers de presentación ───────────────────────────────────────────

    def _card(
        self,
        parent: ctk.CTkBaseClass,
        col: int,
        valor: str,
        etiqueta: str,
        valor_grande: bool = True,
    ) -> None:
        """Render de una card 'estadística'."""
        card = ctk.CTkFrame(parent, corner_radius=12)
        card.grid(row=0, column=col, sticky="ew", padx=8, pady=4, ipady=12)
        card.grid_columnconfigure(0, weight=1)

        font_valor = (
            ctk.CTkFont(size=30, weight="bold")
            if valor_grande
            else ctk.CTkFont(size=15, weight="bold")
        )
        ctk.CTkLabel(card, text=valor, font=font_valor).grid(row=0, column=0, padx=12, pady=(8, 2))
        ctk.CTkLabel(
            card,
            text=etiqueta,
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray70"),
        ).grid(row=1, column=0, padx=12, pady=(0, 8))

    @staticmethod
    def _calcular_saludo(ahora: _dt.datetime) -> str:
        hora = ahora.hour
        if 5 <= hora < 12:
            return "Buenos días"
        if 12 <= hora < 19:
            return "Buenas tardes"
        return "Buenas noches"

    @staticmethod
    def _fecha_en_espanol(ahora: _dt.datetime) -> str:
        dia_semana = _DIAS_SEMANA[ahora.weekday()]
        mes = _MESES[ahora.month - 1]
        return f"{dia_semana} {ahora.day} de {mes} de {ahora.year}"

    @staticmethod
    def _tip_para_rol(role_code: str) -> Optional[str]:
        """Mensaje útil según el rol — desaparece para roles desconocidos."""
        tips: dict[str, str] = {
            "SUPERADMIN": (
                "Acceso total. Si necesitás recuperar la contraseña, "
                "ejecutá ``python -m bin.recover_superadmin`` desde la "
                "carpeta de instalación."
            ),
            "ADMIN": (
                "Podés gestionar empleados, turnos y dispositivos. Para "
                "habilitar un nuevo cargo o departamento entrá a "
                "Configuración → Catálogos."
            ),
            "REPORTES": (
                "Generá reportes Excel desde el módulo Reportes. Tu "
                "historial de descargas queda guardado para auditoría."
            ),
            "OPERADOR": (
                "Tu rol está enfocado en sincronizar el reloj y revisar "
                "asistencia. Si una marcada no aparece, esperá unos "
                "minutos y volvé a sincronizar."
            ),
        }
        return tips.get(role_code)
