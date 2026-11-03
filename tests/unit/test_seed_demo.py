"""Tests del script ``bin.seed_demo`` (datos ficticios de demostración).

Smoke test: el seed corre limpio sobre una BD vacía, deja datos en todas
las tablas que la UI muestra y se niega a correr sobre una BD que ya
tiene empleados (para no ensuciar una BD real por accidente).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from bin import seed_demo


def _contar(db_path: Path, tabla: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0])
    finally:
        conn.close()


def test_seed_demo_puebla_todas_las_tablas_de_la_ui(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "zkteco_app.db"

    codigo = seed_demo.main(["--db-path", str(db_path), "--semanas", "2"])

    assert codigo == 0
    assert _contar(db_path, "usuarios") == 4
    assert _contar(db_path, "empleados") == 20
    assert _contar(db_path, "turnos") == 3
    assert _contar(db_path, "turno_horarios") == 2  # sábado de Administrativo y Operativo
    assert _contar(db_path, "ausencias") == 5
    assert _contar(db_path, "dispositivos") == 1
    assert _contar(db_path, "sincronizaciones") >= 2
    assert _contar(db_path, "registros_raw") > 0
    assert _contar(db_path, "asistencias") > 0
    assert _contar(db_path, "descargas_reportes") == 1
    assert (tmp_path / "data" / "exports").is_dir()


def test_seed_demo_no_corre_sobre_bd_con_empleados(tmp_path: Path) -> None:
    db_path = tmp_path / "zkteco_app.db"
    assert seed_demo.main(["--db-path", str(db_path), "--semanas", "1"]) == 0

    segundo = seed_demo.main(["--db-path", str(db_path), "--semanas", "1"])

    assert segundo == 1
    assert _contar(db_path, "empleados") == 20


def test_seed_demo_es_reproducible_con_la_misma_semilla(tmp_path: Path) -> None:
    a = tmp_path / "a.db"
    b = tmp_path / "b.db"

    seed_demo.main(["--db-path", str(a), "--semanas", "1", "--semilla", "7"])
    seed_demo.main(["--db-path", str(b), "--semanas", "1", "--semilla", "7"])

    assert _contar(a, "registros_raw") == _contar(b, "registros_raw")
