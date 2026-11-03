# -*- mode: python ; coding: utf-8 -*-
#
# Spec file de PyInstaller para BIO-DIPROMA (DIPROMA).
#
# Modo de empaquetado: --onedir (aprobado en Sub-6.2). Decisión:
#   • Compatible con antivirus institucionales (no descomprime a TEMP).
#   • Arranque inmediato (~0.5–1 s).
#   • Encaja con el modo portable de Sub-6.1: <exe-dir>/{zkteco.exe, _internal/, data/}.
#
# Cómo ejecutar el build (desde la raíz del repo, en una máquina Windows
# con el venv activo y `pip install -r requirements-dev.txt` listo):
#
#   pyinstaller packaging/zkteco.spec --noconfirm
#
# El resultado queda en `dist/zkteco/` con `zkteco.exe` y la carpeta
# `_internal/` que PyInstaller 6.x usa para agrupar DLLs y recursos.
# Sub-6.3 traerá los scripts (build.ps1 / build.bat) y BUILD.md con
# instrucciones detalladas para Windows.
#
# Notas de diseño:
#   • Hidden imports DEFENSIVOS (collect_submodules) para libs UI/comm:
#     customtkinter, tkcalendar, openpyxl, zk (pyzk). Como dev es macOS
#     y el target es Windows, no podemos iterar localmente — pagar 5–10
#     MB extra al bundle es preferible a debuggear ImportError remoto.
#   • Los recursos read-only (migraciones SQL, ícono) se empotran con
#     --add-data y se leen vía `infrastructure.paths.resource_path()`.
#   • Excludes: stack de testing/lint/dev (pytest, mypy, black, etc.)
#     no debe terminar dentro del .exe — eso engorda sin razón.

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# ── Paths ─────────────────────────────────────────────────────────────────────
# El spec se ejecuta con SPECPATH = directorio del .spec. Subimos un nivel
# para llegar a la raíz del repo de forma robusta sin importar desde dónde
# se invoque pyinstaller.
REPO_ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 (SPECPATH viene del runtime)
ENTRY_POINT = REPO_ROOT / "main.py"
ICON_PATH = REPO_ROOT / "assets" / "icon.ico"
VERSION_INFO = Path(SPECPATH) / "version_info.txt"  # noqa: F821

# ── Recursos a empotrar (datas) ───────────────────────────────────────────────
# Cada tupla es (origen_en_disco, destino_dentro_del_bundle).
# Las migraciones SQL se leen vía paths.migrations_dir() en runtime.
datas = [
    (
        str(REPO_ROOT / "infrastructure" / "database" / "migrations" / "*.sql"),
        "infrastructure/database/migrations",
    ),
]
# Recursos auto-detectados de cada lib (themes de customtkinter, traducciones
# de tkcalendar/babel, etc.) — fundamentales para que la UI no se rompa.
datas += collect_data_files("customtkinter")
datas += collect_data_files("tkcalendar")
datas += collect_data_files("babel")  # tkcalendar depende de babel para locales

# ── Hidden imports ────────────────────────────────────────────────────────────
# PyInstaller no siempre detecta imports dinámicos (importlib, plugins).
# collect_submodules() trae el árbol completo de cada paquete.
hidden = []
hidden += collect_submodules("customtkinter")
hidden += collect_submodules("tkcalendar")
hidden += collect_submodules("openpyxl")
hidden += collect_submodules("zk")  # pyzk expone su módulo como `zk`

# ── Excludes (stack de dev que NO debe ir al .exe) ───────────────────────────
excludes = [
    "pytest",
    "_pytest",
    "mypy",
    "black",
    "flake8",
    "pre_commit",
    "PyInstaller",  # no auto-empotrarse
    "tkinter.test",
    "test",
    "tests",
]

# ── Análisis del entry point ──────────────────────────────────────────────────
a = Analysis(  # noqa: F821 (Analysis lo expone PyInstaller en el runtime del spec)
    [str(ENTRY_POINT)],
    pathex=[str(REPO_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=1,  # -O: quita asserts y docstrings; sin cambios de comportamiento.
)

pyz = PYZ(a.pure)  # noqa: F821

# ── Ejecutable ────────────────────────────────────────────────────────────────
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # --onedir: las binarias van en COLLECT, no aquí.
    name="zkteco",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX rompe firmas en Windows AV; preferimos no comprimir.
    console=False,  # --windowed: sin ventana de cmd al abrir el .exe.
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON_PATH),
    version=str(VERSION_INFO),
)

# ── Carpeta de distribución (--onedir) ───────────────────────────────────────
coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="zkteco",
)
