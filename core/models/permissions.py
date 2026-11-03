"""Catálogo de códigos de permiso y de rol del sistema.

Estos códigos son la "fuente de verdad" en Python. La migración
``002_auth.sql`` contiene el mismo set como JSON literal por rol; si
alguno cambia acá, debe cambiar también allá (y viceversa).

Diseño:
    - ``ALL_PERMISSIONS`` es un ``frozenset`` inmutable. Se usa para
      validar, al cargar un rol desde la BD, que ningún permiso JSON
      huérfano se coló.
    - Los códigos de rol (ROLE_*) existen porque varios módulos los
      referencian directamente (setup wizard, guards, tests). Tenerlos
      centralizados evita typos.
"""

from __future__ import annotations

from typing import Final, FrozenSet

# ── Códigos de permiso ────────────────────────────────────────────────────────

MANAGE_USERS: Final[str] = "manage_users"
MANAGE_ROLES: Final[str] = "manage_roles"
MANAGE_SETTINGS: Final[str] = "manage_settings"
MANAGE_EMPLOYEES: Final[str] = "manage_employees"
RUN_ZKTECO_SYNC: Final[str] = "run_zkteco_sync"
VIEW_ATTENDANCE: Final[str] = "view_attendance"
EXPORT_REPORTS: Final[str] = "export_reports"
VIEW_EXPORT_HISTORY: Final[str] = "view_export_history"

ALL_PERMISSIONS: Final[FrozenSet[str]] = frozenset(
    {
        MANAGE_USERS,
        MANAGE_ROLES,
        MANAGE_SETTINGS,
        MANAGE_EMPLOYEES,
        RUN_ZKTECO_SYNC,
        VIEW_ATTENDANCE,
        EXPORT_REPORTS,
        VIEW_EXPORT_HISTORY,
    }
)


# ── Códigos de rol canónicos ──────────────────────────────────────────────────

ROLE_SUPERADMIN: Final[str] = "SUPERADMIN"
ROLE_ADMIN: Final[str] = "ADMIN"
ROLE_REPORTES: Final[str] = "REPORTES"
ROLE_OPERADOR: Final[str] = "OPERADOR"

ALL_ROLES: Final[FrozenSet[str]] = frozenset(
    {ROLE_SUPERADMIN, ROLE_ADMIN, ROLE_REPORTES, ROLE_OPERADOR}
)
