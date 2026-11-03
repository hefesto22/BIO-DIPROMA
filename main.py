"""Punto de entrada de BIO-DIPROMA — control de asistencia biométrica de DIPROMA.

Secuencia de arranque:
    1. Asegurar que las carpetas de runtime existan (data/, logs/, exports/).
    2. Configurar logging (StreamHandler siempre; FileHandler rotativo
       cuando la app está congelada o cuando ``log_to_file=True``).
    3. Verificar que las dependencias críticas importan bien.
    4. Delegar a ``ui.app.run()`` — allí vive el composition root:
       abre BD + aplica migraciones + construye servicios + levanta UI.

El smoke-check de BD que existía en Fase 0 se movió a ``ui.app.run()``
para evitar abrir la conexión dos veces en el arranque real. Se puede
seguir corriendo ``python -m bin.setup_wizard`` para preparar el
sistema antes de abrir la UI.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

import config
from infrastructure import paths


def _setup_logging() -> None:
    """Configura el logging raíz.

    Siempre instala un ``StreamHandler`` a stdout. Cuando la app corre
    desde un bundle PyInstaller (``paths.is_frozen()``) además agrega
    un ``RotatingFileHandler`` apuntando a ``data/logs/app.log`` — sin
    eso, un .exe GUI no tiene forma de exponer logs cuando algo falla.
    """
    root = logging.getLogger()
    root.setLevel(config.LOG_LEVEL)

    # Limpiamos handlers previos (importante si ``main()`` se invoca
    # más de una vez, por ejemplo desde un test).
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(config.LOG_FORMAT)

    stream_handler = logging.StreamHandler(stream=sys.stdout)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    if paths.is_frozen():
        log_file = paths.logs_dir() / config.LOG_FILE_NAME
        file_handler = RotatingFileHandler(
            filename=str(log_file),
            maxBytes=config.LOG_FILE_MAX_BYTES,
            backupCount=config.LOG_FILE_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)


def _check_critical_imports(log: logging.Logger) -> bool:
    """Valida que las 5 dependencias de producción importen correctamente."""
    try:
        import bcrypt  # noqa: F401
        import customtkinter  # noqa: F401
        import openpyxl  # noqa: F401
        import zk  # noqa: F401  (pyzk expone el módulo `zk`)
    except ImportError as exc:
        log.error("Falta una dependencia crítica: %s", exc)
        return False
    log.info("Dependencias críticas OK (bcrypt, customtkinter, openpyxl, pyzk).")
    return True


def main() -> int:
    """Entry point. Retorna el exit code del proceso."""
    # Importante: crear las carpetas ANTES de instalar el FileHandler,
    # que necesita data/logs/ ya creada.
    paths.ensure_runtime_dirs()

    _setup_logging()
    log = logging.getLogger("main")
    log.info("Iniciando %s v%s (%s)", config.APP_NAME, config.APP_VERSION, config.APP_VENDOR)
    log.info("Modo congelado: %s", paths.is_frozen())
    log.info("app_dir: %s", paths.app_dir())
    log.info("BD: %s", paths.db_path())

    if not _check_critical_imports(log):
        return 1

    # Importamos ui.app aquí (no arriba) para que, si faltan dependencias
    # de UI, el error salga en ``_check_critical_imports`` — no como un
    # ImportError opaco al arrancar este módulo.
    from ui.app import run as run_ui

    return run_ui()


if __name__ == "__main__":
    sys.exit(main())
