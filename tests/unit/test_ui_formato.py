"""Tests de ``ui.formato`` (formato de fechas, días, duraciones)."""

from __future__ import annotations

from datetime import date

from core.models.turno import DIAS_LABORALES, DIAS_TODA_LA_SEMANA, LUNES, MIERCOLES, SABADO
from ui import formato as f


def test_fecha_dd_mm_aaaa() -> None:
    assert f.fecha("2026-10-05") == "05/10/2026"
    assert f.fecha("2026-10-05T16:00:00+00:00") == "05/10/2026"
    assert f.fecha(None) == "—"
    assert f.fecha("basura") == "basura"


def test_fecha_hora_formato() -> None:
    assert len(f.fecha_hora("2026-10-31T16:13:00+00:00")) == len("31/10/2026 16:13")
    assert f.fecha_hora(None) == "—"


def test_dias_texto() -> None:
    assert f.dias_texto(DIAS_LABORALES) == "Lun a Vie"
    assert f.dias_texto(SABADO) == "Sábado"
    assert f.dias_texto(DIAS_TODA_LA_SEMANA) == "Todos los días"
    assert f.dias_texto(DIAS_LABORALES | SABADO) == "Lun a Sáb"
    assert f.dias_texto(LUNES | MIERCOLES) == "Lun, Mié"
    assert f.dias_texto(0) == "—"


def test_dias_activos_orden_lunes_a_domingo() -> None:
    chips = f.dias_activos(DIAS_LABORALES)
    assert [c for c, _ in chips] == ["L", "M", "M", "J", "V", "S", "D"]
    assert [a for _, a in chips] == [True] * 5 + [False] * 2


def test_duracion() -> None:
    assert f.duracion(480) == "8 h"
    assert f.duracion(450) == "7 h 30 min"
    assert f.duracion(45) == "45 min"
    assert f.duracion(-5) == "0 min"


def test_horas_efectivas_descuenta_descanso() -> None:
    assert f.horas_efectivas("09:00", "17:00", 60) == 7 * 60
    assert f.horas_efectivas("22:00", "06:00", 30) == 7 * 60 + 30  # cruza medianoche


def test_antiguedad() -> None:
    hoy = date(2026, 10, 31)
    assert f.antiguedad("2023-08-15", hoy) == "3 años, 2 meses"
    assert f.antiguedad("2026-05-31", hoy) == "5 meses"
    assert f.antiguedad("2026-10-20", hoy) == "11 días"
    assert f.antiguedad("2025-10-31", hoy) == "1 año"
    assert f.antiguedad("2027-01-01", hoy) == "Aún no ingresa"
    assert f.antiguedad(None, hoy) == "—"


def test_segundos_entre() -> None:
    assert f.segundos_entre("2026-10-31T10:00:00+00:00", "2026-10-31T10:00:42+00:00") == "42 s"
    assert f.segundos_entre("2026-10-31T10:00:00+00:00", "2026-10-31T10:01:05+00:00") == "1 min 5 s"
    assert f.segundos_entre(None, "2026-10-31T10:00:00+00:00") == "—"


def test_formatear_dni_agrega_guiones_mientras_se_escribe() -> None:
    from ui.components.empleado_form_dialog import formatear_dni

    assert formatear_dni("0401") == "0401"
    assert formatear_dni("04011990") == "0401-1990"
    assert formatear_dni("0401199000123") == "0401-1990-00123"
    assert formatear_dni("0401-1990-00123") == "0401-1990-00123"
    assert formatear_dni("0401 1990 001234567") == "0401-1990-00123"
