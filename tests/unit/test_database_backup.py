"""Tests del módulo ``infrastructure.database.backup`` (Sub-3.1)."""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import date
from pathlib import Path

import pytest

from infrastructure.database.backup import (
    _purgar_expirados,
    run_daily_backup,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _crear_bd_simple(path: Path) -> None:
    """Crea una BD SQLite mínima en ``path`` con una tabla y una fila."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE marcador (id INTEGER PRIMARY KEY, valor TEXT)")
        conn.execute("INSERT INTO marcador (valor) VALUES ('hola')")
        conn.commit()
    finally:
        conn.close()


def _backups(carpeta: Path) -> list[str]:
    """Devuelve los nombres de archivos de backup ordenados."""
    return sorted(p.name for p in carpeta.glob("zkteco_app_*.db"))


def _set_mtime_dias_atras(archivo: Path, dias: int) -> None:
    """Cambia el mtime del archivo para simularlo creado ``dias`` atrás."""
    nuevo = time.time() - dias * 86_400
    os.utime(archivo, (nuevo, nuevo))


# ── Caso happy path: primer backup del día ───────────────────────────────────


def test_run_daily_backup_crea_archivo_con_nombre_canonico(tmp_path: Path) -> None:
    db = tmp_path / "zkteco_app.db"
    _crear_bd_simple(db)
    backup_dir = tmp_path / "backups"

    creado = run_daily_backup(db_path=db, backup_dir=backup_dir, today=date(2026, 4, 30))

    assert creado is not None
    assert creado.name == "zkteco_app_2026-04-30.db"
    assert creado.exists()
    assert creado.parent == backup_dir


def test_run_daily_backup_es_un_sqlite_valido(tmp_path: Path) -> None:
    """El backup creado debe poder abrirse como SQLite válido y tener los datos."""
    db = tmp_path / "zkteco_app.db"
    _crear_bd_simple(db)
    backup_dir = tmp_path / "backups"

    creado = run_daily_backup(db_path=db, backup_dir=backup_dir, today=date(2026, 4, 30))
    assert creado is not None

    conn = sqlite3.connect(str(creado))
    try:
        valor = conn.execute("SELECT valor FROM marcador").fetchone()
    finally:
        conn.close()
    assert valor == ("hola",)


def test_run_daily_backup_crea_carpeta_destino_si_no_existe(tmp_path: Path) -> None:
    db = tmp_path / "zkteco_app.db"
    _crear_bd_simple(db)
    backup_dir = tmp_path / "subdir" / "anidada" / "backups"
    assert not backup_dir.exists()

    run_daily_backup(db_path=db, backup_dir=backup_dir, today=date(2026, 4, 30))

    assert backup_dir.is_dir()


# ── Idempotencia ─────────────────────────────────────────────────────────────


def test_run_daily_backup_no_duplica_si_ya_existe_el_de_hoy(tmp_path: Path) -> None:
    db = tmp_path / "zkteco_app.db"
    _crear_bd_simple(db)
    backup_dir = tmp_path / "backups"
    today = date(2026, 4, 30)

    primero = run_daily_backup(db_path=db, backup_dir=backup_dir, today=today)
    segundo = run_daily_backup(db_path=db, backup_dir=backup_dir, today=today)

    assert primero is not None
    assert segundo is None  # ya había uno
    assert _backups(backup_dir) == ["zkteco_app_2026-04-30.db"]


def test_run_daily_backup_dias_distintos_crea_archivos_distintos(tmp_path: Path) -> None:
    db = tmp_path / "zkteco_app.db"
    _crear_bd_simple(db)
    backup_dir = tmp_path / "backups"

    run_daily_backup(db_path=db, backup_dir=backup_dir, today=date(2026, 4, 29))
    run_daily_backup(db_path=db, backup_dir=backup_dir, today=date(2026, 4, 30))

    assert _backups(backup_dir) == [
        "zkteco_app_2026-04-29.db",
        "zkteco_app_2026-04-30.db",
    ]


# ── BD origen ausente ────────────────────────────────────────────────────────


def test_run_daily_backup_devuelve_none_si_db_no_existe(tmp_path: Path) -> None:
    """Si la BD principal no existe (primera ejecución), no debe explotar."""
    db = tmp_path / "no_existe.db"
    backup_dir = tmp_path / "backups"

    resultado = run_daily_backup(db_path=db, backup_dir=backup_dir, today=date(2026, 4, 30))

    assert resultado is None
    assert not backup_dir.exists() or _backups(backup_dir) == []


# ── Purga de expirados ───────────────────────────────────────────────────────


def test_purgar_expirados_borra_los_mas_viejos_que_max_age(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    viejo = backup_dir / "zkteco_app_2025-01-01.db"
    medio = backup_dir / "zkteco_app_2026-04-01.db"
    nuevo = backup_dir / "zkteco_app_2026-04-29.db"
    for archivo in (viejo, medio, nuevo):
        archivo.write_bytes(b"contenido")
    _set_mtime_dias_atras(viejo, 400)  # > 30 días: se borra
    _set_mtime_dias_atras(medio, 60)  # > 30 días: se borra
    _set_mtime_dias_atras(nuevo, 1)  # < 30 días: se conserva

    eliminados = _purgar_expirados(backup_dir, max_age_days=30)

    assert eliminados == 2
    assert _backups(backup_dir) == ["zkteco_app_2026-04-29.db"]


def test_purgar_expirados_con_max_age_cero_no_borra_nada(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    archivo = backup_dir / "zkteco_app_2024-01-01.db"
    archivo.write_bytes(b"x")
    _set_mtime_dias_atras(archivo, 1000)

    eliminados = _purgar_expirados(backup_dir, max_age_days=0)

    assert eliminados == 0
    assert _backups(backup_dir) == ["zkteco_app_2024-01-01.db"]


def test_purgar_expirados_ignora_archivos_que_no_son_backups(tmp_path: Path) -> None:
    """Solo gestiona archivos que matchean ``zkteco_app_*.db``."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    backup_real = backup_dir / "zkteco_app_2024-01-01.db"
    intruso = backup_dir / "notas_del_operador.txt"
    backup_real.write_bytes(b"x")
    intruso.write_bytes(b"y")
    _set_mtime_dias_atras(backup_real, 100)
    _set_mtime_dias_atras(intruso, 100)

    _purgar_expirados(backup_dir, max_age_days=30)

    assert not backup_real.exists()
    assert intruso.exists()  # el archivo del operador queda intacto


# ── Integración: backup + purga en un solo run_daily_backup ──────────────────


def test_run_daily_backup_crea_y_purga_en_la_misma_corrida(tmp_path: Path) -> None:
    """Al crear el backup de hoy también se purgan los expirados."""
    db = tmp_path / "zkteco_app.db"
    _crear_bd_simple(db)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    # Sembramos un backup viejo que debe ser purgado.
    viejo = backup_dir / "zkteco_app_2024-01-01.db"
    viejo.write_bytes(b"x")
    _set_mtime_dias_atras(viejo, 365)

    run_daily_backup(
        db_path=db,
        backup_dir=backup_dir,
        today=date(2026, 4, 30),
        max_age_days=30,
    )

    archivos = _backups(backup_dir)
    assert "zkteco_app_2024-01-01.db" not in archivos
    assert "zkteco_app_2026-04-30.db" in archivos


def test_run_daily_backup_purga_aunque_no_cree_nuevo_backup(tmp_path: Path) -> None:
    """Si el backup de hoy ya existe, igual purga los viejos."""
    db = tmp_path / "zkteco_app.db"
    _crear_bd_simple(db)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    # Backup de hoy ya existe.
    hoy = backup_dir / "zkteco_app_2026-04-30.db"
    hoy.write_bytes(b"x")

    # Y un backup viejo expirado.
    viejo = backup_dir / "zkteco_app_2024-01-01.db"
    viejo.write_bytes(b"x")
    _set_mtime_dias_atras(viejo, 400)

    resultado = run_daily_backup(
        db_path=db, backup_dir=backup_dir, today=date(2026, 4, 30), max_age_days=30
    )

    assert resultado is None  # no creó nada nuevo
    assert not viejo.exists()  # pero purgó el viejo


# ── Robustez frente a errores de I/O ─────────────────────────────────────────


def test_run_daily_backup_no_propaga_si_falla_la_copia(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Si la copia interna levanta ``sqlite3.Error``, debe quedar logueado
    pero NO propagar — la app continúa arrancando."""
    from infrastructure.database import backup as backup_mod

    db = tmp_path / "zkteco_app.db"
    _crear_bd_simple(db)
    backup_dir = tmp_path / "backups"

    def _falla(_src: Path, _dst: Path) -> None:
        raise sqlite3.OperationalError("disk I/O simulado")

    # Inyectamos el fallo en el helper interno (más limpio que monkeypatchear
    # un método inmutable de sqlite3.Connection).
    monkeypatch.setattr(backup_mod, "_copiar_consistente", _falla)

    resultado = run_daily_backup(db_path=db, backup_dir=backup_dir, today=date(2026, 4, 30))

    assert resultado is None
    # Si quedó un archivo a medio crear, la implementación lo limpia.
    assert _backups(backup_dir) == []


def test_run_daily_backup_no_propaga_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mismo contrato pero con ``OSError`` (disco lleno, permisos, etc.)."""
    from infrastructure.database import backup as backup_mod

    db = tmp_path / "zkteco_app.db"
    _crear_bd_simple(db)
    backup_dir = tmp_path / "backups"

    def _falla(_src: Path, _dst: Path) -> None:
        raise OSError("disco lleno simulado")

    monkeypatch.setattr(backup_mod, "_copiar_consistente", _falla)

    resultado = run_daily_backup(db_path=db, backup_dir=backup_dir, today=date(2026, 4, 30))

    assert resultado is None
    assert _backups(backup_dir) == []


# ── today por default ────────────────────────────────────────────────────────


def test_run_daily_backup_usa_hoy_si_no_se_pasa_today(tmp_path: Path) -> None:
    db = tmp_path / "zkteco_app.db"
    _crear_bd_simple(db)
    backup_dir = tmp_path / "backups"

    resultado = run_daily_backup(db_path=db, backup_dir=backup_dir)

    assert resultado is not None
    esperado = f"zkteco_app_{date.today().isoformat()}.db"
    assert resultado.name == esperado


# ── ensure_runtime_dirs incluye backups/ ─────────────────────────────────────


def test_ensure_runtime_dirs_crea_carpeta_backups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``paths.ensure_runtime_dirs`` debe crear data/backups/ junto con las demás."""
    from infrastructure import paths as paths_mod

    monkeypatch.setattr(paths_mod, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(paths_mod, "is_frozen", lambda: False)

    paths_mod.ensure_runtime_dirs()

    assert (tmp_path / "data").is_dir()
    assert (tmp_path / "data" / "logs").is_dir()
    assert (tmp_path / "data" / "exports").is_dir()
    assert (tmp_path / "data" / "backups").is_dir()


def test_purgar_expirados_descarta_dia_extra_si_max_age_es_pequeno(
    tmp_path: Path,
) -> None:
    """Edge: max_age_days=1 debe purgar archivos con más de 1 día."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    archivo = backup_dir / "zkteco_app_2024-01-01.db"
    archivo.write_bytes(b"x")
    _set_mtime_dias_atras(archivo, 2)

    eliminados = _purgar_expirados(backup_dir, max_age_days=1)

    assert eliminados == 1
    assert not archivo.exists()


def test_purgar_expirados_max_age_grande_no_toca_archivos_recientes(
    tmp_path: Path,
) -> None:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    reciente = backup_dir / "zkteco_app_2026-04-29.db"
    reciente.write_bytes(b"x")
    _set_mtime_dias_atras(reciente, 5)
    expirado = backup_dir / "zkteco_app_2026-01-01.db"
    expirado.write_bytes(b"x")
    _set_mtime_dias_atras(expirado, 200)

    eliminados = _purgar_expirados(backup_dir, max_age_days=180)

    assert eliminados == 1
    assert reciente.exists()
    assert not expirado.exists()


def test_run_daily_backup_devuelve_path_que_caller_puede_usar(tmp_path: Path) -> None:
    """El Path devuelto debe ser absoluto y existir."""
    db = tmp_path / "zkteco_app.db"
    _crear_bd_simple(db)
    backup_dir = tmp_path / "backups"

    creado = run_daily_backup(db_path=db, backup_dir=backup_dir, today=date(2026, 4, 30))

    assert creado is not None
    assert creado.is_file()
    assert creado.is_absolute()
