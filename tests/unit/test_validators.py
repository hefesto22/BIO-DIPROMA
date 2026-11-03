"""Tests de los helpers de validación de servicios Fase 2.

Cubre DNI, fechas ISO, horas 24h, bitmask de días, minutos de descanso,
``cruza_medianoche`` y ``normalize_nombre``. Son funciones puras — los
tests son rápidos y no tocan BD.
"""

from __future__ import annotations

from datetime import date

import pytest

from core.services.errors import (
    InvalidBitmaskError,
    InvalidDateError,
    InvalidDNIError,
    InvalidTimeError,
    MissingRequiredFieldError,
)
from core.services.validators import (
    cruza_medianoche,
    duracion_turno_minutos,
    minutos_desde_medianoche,
    normalize_nombre,
    validate_bitmask_dias,
    validate_dni,
    validate_fecha_iso,
    validate_hora_hhmm,
    validate_minutos_descanso,
)

# ── validate_dni ──────────────────────────────────────────────────────────────


def test_validate_dni_formato_correcto_devuelve_normalizado() -> None:
    assert validate_dni("0501-1990-12345") == "0501-1990-12345"


def test_validate_dni_trim_whitespace() -> None:
    assert validate_dni("  0501-1990-12345  ") == "0501-1990-12345"


def test_validate_dni_vacio_levanta_missing() -> None:
    with pytest.raises(MissingRequiredFieldError):
        validate_dni("")


def test_validate_dni_solo_whitespace_levanta_missing() -> None:
    with pytest.raises(MissingRequiredFieldError):
        validate_dni("   ")


@pytest.mark.parametrize(
    "malo",
    [
        "0501199012345",  # sin guiones
        "501-1990-12345",  # primer grupo con 3 dígitos
        "0501-19900-12345",  # segundo grupo con 5 dígitos
        "0501-1990-1234",  # tercer grupo con 4 dígitos
        "0501-1990-123456",  # tercer grupo con 6 dígitos
        "ABCD-1990-12345",  # letras
        "0501/1990/12345",  # separador incorrecto
    ],
)
def test_validate_dni_formato_malo_levanta_invalid(malo: str) -> None:
    with pytest.raises(InvalidDNIError):
        validate_dni(malo)


# ── validate_fecha_iso ────────────────────────────────────────────────────────


def test_validate_fecha_iso_valida_devuelve_date() -> None:
    resultado = validate_fecha_iso("2026-04-22", "fecha_ingreso")
    assert resultado == date(2026, 4, 22)


def test_validate_fecha_iso_vacio_levanta_missing() -> None:
    with pytest.raises(MissingRequiredFieldError):
        validate_fecha_iso("", "fecha_ingreso")


@pytest.mark.parametrize(
    "malo",
    ["2026/04/22", "22-04-2026", "2026-02-30", "2026-13-01", "hola"],
)
def test_validate_fecha_iso_invalida_levanta_invalid(malo: str) -> None:
    with pytest.raises(InvalidDateError):
        validate_fecha_iso(malo, "fecha_ingreso")


# ── validate_hora_hhmm ────────────────────────────────────────────────────────


@pytest.mark.parametrize("hora", ["00:00", "08:00", "17:30", "23:59"])
def test_validate_hora_hhmm_validas(hora: str) -> None:
    # No levanta.
    validate_hora_hhmm(hora, "hora_entrada")


@pytest.mark.parametrize(
    "hora",
    ["24:00", "25:00", "8:00", "08:60", "08-00", "", "  "],
)
def test_validate_hora_hhmm_invalidas_o_vacias(hora: str) -> None:
    with pytest.raises((InvalidTimeError, MissingRequiredFieldError)):
        validate_hora_hhmm(hora, "hora_entrada")


# ── minutos_desde_medianoche ──────────────────────────────────────────────────


def test_minutos_desde_medianoche_calcula_correcto() -> None:
    assert minutos_desde_medianoche("00:00") == 0
    assert minutos_desde_medianoche("08:00") == 480
    assert minutos_desde_medianoche("17:30") == 17 * 60 + 30
    assert minutos_desde_medianoche("23:59") == 23 * 60 + 59


# ── duracion_turno_minutos ────────────────────────────────────────────────────


def test_duracion_turno_diurno() -> None:
    assert duracion_turno_minutos("08:00", "17:00") == 9 * 60


def test_duracion_turno_cruza_medianoche() -> None:
    # 22:00 → 06:00 = 8 horas.
    assert duracion_turno_minutos("22:00", "06:00") == 8 * 60


def test_duracion_turno_24_horas_exactas() -> None:
    # Caso borde: salida == entrada se interpreta como 24h completas.
    assert duracion_turno_minutos("08:00", "08:00") == 24 * 60


# ── cruza_medianoche ──────────────────────────────────────────────────────────


def test_cruza_medianoche_turno_diurno_false() -> None:
    assert cruza_medianoche("08:00", "17:00") is False


def test_cruza_medianoche_turno_nocturno_true() -> None:
    assert cruza_medianoche("22:00", "06:00") is True


def test_cruza_medianoche_24_horas_true() -> None:
    assert cruza_medianoche("08:00", "08:00") is True


# ── validate_bitmask_dias ─────────────────────────────────────────────────────


@pytest.mark.parametrize("valor", [1, 2, 64, 124, 127])
def test_validate_bitmask_dias_validos(valor: int) -> None:
    validate_bitmask_dias(valor)


@pytest.mark.parametrize("valor", [0, -1, 128, 256, 999])
def test_validate_bitmask_dias_invalidos(valor: int) -> None:
    with pytest.raises(InvalidBitmaskError):
        validate_bitmask_dias(valor)


# ── validate_minutos_descanso ─────────────────────────────────────────────────


def test_validate_minutos_descanso_cero_ok() -> None:
    validate_minutos_descanso(0)


def test_validate_minutos_descanso_positivo_ok() -> None:
    validate_minutos_descanso(45)


def test_validate_minutos_descanso_negativo_falla() -> None:
    with pytest.raises(ValueError):
        validate_minutos_descanso(-1)


# ── normalize_nombre ──────────────────────────────────────────────────────────


def test_normalize_nombre_trim_basico() -> None:
    assert normalize_nombre("  Obras Públicas  ", "nombre") == "Obras Públicas"


def test_normalize_nombre_colapsa_espacios_multiples() -> None:
    assert normalize_nombre("Obras   Públicas", "nombre") == "Obras Públicas"


def test_normalize_nombre_preserva_case() -> None:
    # El UNIQUE es case-sensitive por diseño.
    assert normalize_nombre("obras públicas", "nombre") == "obras públicas"
    assert normalize_nombre("OBRAS PÚBLICAS", "nombre") == "OBRAS PÚBLICAS"


def test_normalize_nombre_vacio_levanta_missing() -> None:
    with pytest.raises(MissingRequiredFieldError):
        normalize_nombre("", "nombre")


def test_normalize_nombre_solo_whitespace_levanta_missing() -> None:
    with pytest.raises(MissingRequiredFieldError):
        normalize_nombre("     ", "nombre")
