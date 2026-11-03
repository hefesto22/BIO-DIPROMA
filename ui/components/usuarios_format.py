"""Helpers de presentación del módulo Usuarios y roles.

Funciones puras (sin Tk): estado visible de un usuario, formato de fechas
para mostrar, etiquetas en español de permisos y de acciones del audit
log, y la evaluación en vivo de las reglas de contraseña. Se mantienen
fuera de los widgets para poder testearlas sin levantar una ventana.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Final, List, Optional, Tuple

from core.models import permissions as perms
from core.models.usuario import Usuario

# ── Estado visible del usuario ────────────────────────────────────────────────

ESTADO_ACTIVO: Final[str] = "ACTIVO"
ESTADO_BLOQUEADO: Final[str] = "BLOQUEADO"
ESTADO_INACTIVO: Final[str] = "INACTIVO"

ETIQUETA_ESTADO: Final[Dict[str, str]] = {
    ESTADO_ACTIVO: "Activo",
    ESTADO_BLOQUEADO: "Bloqueado",
    ESTADO_INACTIVO: "Inactivo",
}


def _parse_iso(valor: Optional[str]) -> Optional[datetime]:
    """Parsea un ISO-8601; si viene sin zona se asume UTC. ``None`` si no se puede."""
    if not valor:
        return None
    try:
        dt = datetime.fromisoformat(valor)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def bloqueado_hasta(usuario: Usuario, ahora: datetime) -> Optional[datetime]:
    """Momento (UTC) hasta el que la cuenta sigue bloqueada, o ``None``.

    Un ``locked_until`` ya vencido NO cuenta como bloqueo: el login lo
    limpia en el siguiente intento correcto (ver ``AuthService``).
    """
    hasta = _parse_iso(usuario.locked_until)
    if hasta is None or hasta <= ahora:
        return None
    return hasta


def estado_usuario(usuario: Usuario, ahora: datetime) -> str:
    """Estado a mostrar: inactivo tiene prioridad sobre bloqueado."""
    if not usuario.is_active:
        return ESTADO_INACTIVO
    if bloqueado_hasta(usuario, ahora) is not None:
        return ESTADO_BLOQUEADO
    return ESTADO_ACTIVO


# ── Fechas ────────────────────────────────────────────────────────────────────


def fecha_hora_local(iso_utc: Optional[str]) -> str:
    """``"31/10/2026 16:13"`` en hora local, o ``"—"`` si no hay dato."""
    dt = _parse_iso(iso_utc)
    if dt is None:
        return "—"
    return dt.astimezone().strftime("%d/%m/%Y %H:%M")


def hace(iso_utc: Optional[str], ahora: datetime) -> str:
    """Tiempo relativo corto para tablas: ``"hace 5 min"``, ``"ayer 09:02"``…"""
    dt = _parse_iso(iso_utc)
    if dt is None:
        return "Nunca"
    segundos = (ahora - dt).total_seconds()
    if segundos < 60:
        return "Hace un momento"
    if segundos < 3600:
        return f"Hace {int(segundos // 60)} min"
    local = dt.astimezone()
    hoy = ahora.astimezone().date()
    if local.date() == hoy:
        return f"Hoy {local:%H:%M}"
    if (hoy - local.date()).days == 1:
        return f"Ayer {local:%H:%M}"
    return f"{local:%d/%m/%Y}"


# ── Iniciales para el avatar ──────────────────────────────────────────────────


def iniciales(nombre: str) -> str:
    """Dos iniciales en mayúscula (``"Ana Lara"`` → ``"AL"``)."""
    partes = [p for p in nombre.split() if p[:1].isalnum()]
    if not partes:
        return "?"
    if len(partes) == 1:
        return partes[0][:2].upper()
    return (partes[0][0] + partes[1][0]).upper()


# ── Permisos (orden y textos de la matriz de roles) ───────────────────────────


@dataclass(frozen=True)
class PermisoUI:
    code: str
    nombre: str
    descripcion: str


PERMISOS_UI: Final[Tuple[PermisoUI, ...]] = (
    PermisoUI(
        perms.VIEW_ATTENDANCE,
        "Ver asistencia",
        "Consultar marcas y asistencias calculadas.",
    ),
    PermisoUI(
        perms.RUN_ZKTECO_SYNC,
        "Sincronizar el reloj",
        "Descargar las marcas del biométrico.",
    ),
    PermisoUI(
        perms.EXPORT_REPORTS,
        "Exportar reportes",
        "Generar y descargar reportes en Excel.",
    ),
    PermisoUI(
        perms.VIEW_EXPORT_HISTORY,
        "Historial de reportes",
        "Ver quién descargó qué reporte y cuándo.",
    ),
    PermisoUI(
        perms.MANAGE_EMPLOYEES,
        "Empleados y turnos",
        "Altas, bajas, cambios de empleados y sus turnos.",
    ),
    PermisoUI(
        perms.MANAGE_SETTINGS,
        "Configuración",
        "Departamentos, cargos y relojes biométricos.",
    ),
    PermisoUI(
        perms.MANAGE_USERS,
        "Usuarios",
        "Crear usuarios, cambiar contraseñas y desactivar cuentas.",
    ),
    PermisoUI(
        perms.MANAGE_ROLES,
        "Roles",
        "Administrar los roles del sistema.",
    ),
)


# ── Acciones del audit log ────────────────────────────────────────────────────

_ACCIONES: Final[Dict[str, str]] = {
    "login_ok": "Inició sesión",
    "login_fail": "Intento de acceso fallido",
    "login_fail_inactive": "Intento de acceso con cuenta inactiva",
    "login_fail_role_missing": "Intento de acceso sin rol válido",
    "account_locked": "Cuenta bloqueada por intentos fallidos",
    "setup_completed": "Configuró el sistema por primera vez",
    "usuario_created": "Creó un usuario",
    "usuario_updated": "Modificó un usuario",
    "usuario_password_reset": "Cambió una contraseña",
    "usuario_deactivated": "Desactivó un usuario",
    "usuario_reactivated": "Reactivó un usuario",
    "usuario_unlocked": "Desbloqueó un usuario",
    "empleado_created": "Registró un empleado",
    "empleado_updated": "Modificó un empleado",
    "empleado_deactivated": "Dio de baja a un empleado",
    "empleado_reactivated": "Reactivó a un empleado",
    "turno_created": "Creó un turno",
    "turno_updated": "Modificó un turno",
    "turno_archived": "Archivó un turno",
    "turno_unarchived": "Restauró un turno",
    "turno_asignado": "Asignó un turno",
    "turno_cambiado": "Cambió un turno",
    "turno_cerrado": "Cerró una asignación de turno",
    "ausencia_registrada": "Registró vacaciones o un permiso",
    "ausencia_actualizada": "Modificó vacaciones o un permiso",
    "ausencia_eliminada": "Eliminó vacaciones o un permiso",
    "departamento_created": "Creó un departamento",
    "departamento_renamed": "Renombró un departamento",
    "departamento_archived": "Archivó un departamento",
    "departamento_merged": "Archivó un departamento y pasó su personal a otro",
    "departamento_unarchived": "Restauró un departamento",
    "cargo_created": "Creó un cargo",
    "cargo_renamed": "Renombró un cargo",
    "cargo_depto_changed": "Cambió el departamento de un cargo",
    "cargo_archived": "Archivó un cargo",
    "cargo_merged": "Archivó un cargo y pasó sus empleados a otro",
    "cargo_unarchived": "Restauró un cargo",
    "dispositivo_created": "Registró un reloj",
    "dispositivo_updated": "Modificó un reloj",
    "dispositivo_archived": "Archivó un reloj",
    "dispositivo_unarchived": "Restauró un reloj",
    "dispositivo_hora_ajustada": "Puso en hora un reloj",
    "sync.ok": "Sincronizó el reloj",
    "sync.fallida": "Sincronización fallida",
    "asistencia_re_consolidada": "Recalculó asistencia",
    "asistencia_observaciones_updated": "Editó una observación de asistencia",
    "reporte_asistencia_exportado": "Exportó un reporte de asistencia",
}


def etiqueta_accion(action: str) -> str:
    """Texto en español para una acción del audit log (con respaldo legible)."""
    if action in _ACCIONES:
        return _ACCIONES[action]
    texto = action.replace(".", " ").replace("_", " ").strip()
    return texto[:1].upper() + texto[1:] if texto else "Acción"


# ── Reglas de contraseña (checklist en vivo de los diálogos) ──────────────────


def evaluar_password(
    password: str,
    min_length: int,
    require_digit: bool,
    require_letter: bool,
) -> List[Tuple[str, bool]]:
    """Lista ``(regla, cumple)`` en el mismo orden en que se muestra en pantalla.

    Refleja ``PasswordPolicy``; la validación real sigue ocurriendo en el
    servicio al guardar — esto es solo ayuda visual mientras se escribe.
    """
    reglas: List[Tuple[str, bool]] = [
        (f"Al menos {min_length} caracteres", len(password) >= min_length),
    ]
    if require_letter:
        reglas.append(("Al menos una letra (A-Z)", any(c in _LETRAS_ASCII for c in password)))
    if require_digit:
        reglas.append(("Al menos un número (0-9)", any(c in "0123456789" for c in password)))
    return reglas


# Mismo criterio que ``PasswordPolicy``: solo letras ASCII cuentan.
_LETRAS_ASCII: Final[str] = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
