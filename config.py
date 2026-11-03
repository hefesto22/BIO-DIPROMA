"""Configuración global de la aplicación.

Contiene constantes del sistema: identidad de la app, parámetros de seguridad,
límites de sesión y rutas de archivos. Valores sensibles NUNCA se guardan aquí
(para eso se usará la tabla de configuración en BD, cifrada si aplica).

Notas sobre rutas (Fase 6 — packaging Windows)
----------------------------------------------
Las rutas de runtime se delegan a ``infrastructure.paths``, que resuelve
correctamente tanto en modo dev como bajo PyInstaller. Los nombres
``DATABASE_PATH`` y ``MIGRATIONS_DIR`` se preservan para no romper a los
consumidores existentes (``bin/setup_wizard.py``, ``ui/app.py``).
"""

from pathlib import Path

from infrastructure import paths

# ── Identidad de la aplicación ────────────────────────────────────────────────
# Branding: producto = BIO-DIPROMA, dueño operativo = DIPROMA,
# desarrollado por Grupo Olympo. Toda la UI lee estos valores: para
# re-marcar la app basta con cambiarlos aquí (y packaging/version_info.txt).
APP_NAME: str = "BIO-DIPROMA"
APP_VENDOR: str = "DIPROMA"
APP_TAGLINE: str = "Control de asistencia biométrica"
APP_AUTHOR: str = "Grupo Olympo"
APP_VERSION: str = "0.1.0"  # pre-alpha, en desarrollo

# ── Rutas ─────────────────────────────────────────────────────────────────────
# BASE_DIR: raíz del repo. Se mantiene para retro-compat (lo usa main.py
# como log informativo). Para resolver rutas reales, los nuevos consumidores
# deben usar ``infrastructure.paths``.
# TODO(mcruoz): deprecar tras Fase 6 — usar paths.app_dir(). 2026-04-24
BASE_DIR: Path = Path(__file__).resolve().parent

# Archivo SQLite. Bajo modo portable (Opción B aprobada en Sub-6.1) vive
# en ``<exe-dir>/data/zkteco_app.db``. En dev, ``<repo-root>/data/zkteco_app.db``.
DATABASE_FILENAME: str = "zkteco_app.db"
DATABASE_PATH: Path = paths.db_path()

# Carpeta de migraciones SQL (read-only). Bajo PyInstaller ``--onefile``
# resuelve a ``sys._MEIPASS / infrastructure/database/migrations``.
MIGRATIONS_DIR: Path = paths.migrations_dir()

# ── Seguridad ─────────────────────────────────────────────────────────────────
# bcrypt cost factor. Mínimo 12 según las reglas del proyecto.
BCRYPT_COST_FACTOR: int = 12

# Bloqueo de cuenta tras N intentos fallidos consecutivos.
MAX_FAILED_LOGIN_ATTEMPTS: int = 5

# Duración del bloqueo temporal tras MAX_FAILED_LOGIN_ATTEMPTS (en minutos).
# Política: lockout temporal fijo (Opción B aprobada). Tras este periodo, el
# siguiente login con password correcta resetea el contador y permite entrar.
LOCKOUT_DURATION_MINUTES: int = 15

# Expiración de sesión por inactividad (en minutos). 0 = nunca.
SESSION_TIMEOUT_MINUTES: int = 30

# ── Política de contraseñas ───────────────────────────────────────────────────
# Se aplica al crear el primer SUPERADMIN (Setup Wizard) y, más adelante, a
# cualquier otro flujo de creación o cambio de password.
MIN_PASSWORD_LENGTH: int = 8
PASSWORD_REQUIRE_DIGIT: bool = True
PASSWORD_REQUIRE_LETTER: bool = True

# ── Logging ───────────────────────────────────────────────────────────────────
# Nivel por defecto. En prod se baja a INFO o WARNING vía config en BD.
LOG_LEVEL: str = "DEBUG"
LOG_FORMAT: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# Tamaño máximo del archivo de log (5 MB) y cuántos backups rotar.
# Solo se usa cuando se instala el ``RotatingFileHandler`` — ver main.py.
LOG_FILE_MAX_BYTES: int = 5 * 1024 * 1024
LOG_FILE_BACKUP_COUNT: int = 3
LOG_FILE_NAME: str = "app.log"

# ── Backup automático de la BD (Sub-3.1) ──────────────────────────────────────
# Cantidad de días que se conservan los snapshots diarios en
# ``data/backups/``. Más allá de ese umbral se borran al arrancar la app.
# 0 = retención infinita (no recomendado, llena disco).
BACKUP_MAX_AGE_DAYS: int = 30

# ── Relojes en modo push (ADMS) ───────────────────────────────────────────────
# Puerto donde la app escucha a los relojes que envían las marcas solos. En el
# reloj: Menú → Comunicación → "Servidor en la nube" / ADMS → dirección = IP de
# esta computadora, puerto = este. No se usa 8081 porque es el de ZKBioTime.
PUERTO_PUSH: int = 8088
