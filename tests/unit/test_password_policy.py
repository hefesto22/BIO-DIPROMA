"""Tests de PasswordPolicy.

PasswordPolicy es una clase puramente funcional (sin side-effects),
por lo que no necesita mocks ni fixtures complejos.
"""

from __future__ import annotations

import pytest

from core.services.errors import WeakPasswordError
from core.services.password_policy import PasswordPolicy

# ── Validación del constructor ────────────────────────────────────────────────


def test_constructor_rechaza_min_length_cero() -> None:
    with pytest.raises(ValueError):
        PasswordPolicy(min_length=0, require_digit=True, require_letter=True)


def test_constructor_rechaza_min_length_negativo() -> None:
    with pytest.raises(ValueError):
        PasswordPolicy(min_length=-1, require_digit=False, require_letter=False)


# ── Casos que deben pasar ─────────────────────────────────────────────────────


def test_password_valida_cumple_reglas() -> None:
    policy = PasswordPolicy(min_length=8, require_digit=True, require_letter=True)
    policy.validate("Secreto1")  # no lanza


def test_password_valida_con_simbolos_tambien_pasa() -> None:
    policy = PasswordPolicy(min_length=8, require_digit=True, require_letter=True)
    policy.validate("Mi-Clave-9!")  # no lanza


def test_policy_sin_letra_ni_digito_acepta_cualquier_string() -> None:
    policy = PasswordPolicy(min_length=4, require_digit=False, require_letter=False)
    policy.validate("!!!!")  # no lanza


# ── Casos que deben fallar ────────────────────────────────────────────────────


def test_password_vacia_lanza() -> None:
    policy = PasswordPolicy(min_length=8, require_digit=True, require_letter=True)
    with pytest.raises(WeakPasswordError) as exc:
        policy.validate("")
    assert "vacía" in exc.value.reason.lower()


def test_password_corta_lanza_con_min_length_en_mensaje() -> None:
    policy = PasswordPolicy(min_length=8, require_digit=True, require_letter=True)
    with pytest.raises(WeakPasswordError) as exc:
        policy.validate("Ab1")
    assert "8" in exc.value.reason


def test_password_sin_letra_lanza() -> None:
    policy = PasswordPolicy(min_length=4, require_digit=True, require_letter=True)
    with pytest.raises(WeakPasswordError) as exc:
        policy.validate("12345678")
    assert "letra" in exc.value.reason.lower()


def test_password_sin_digito_lanza() -> None:
    policy = PasswordPolicy(min_length=4, require_digit=True, require_letter=True)
    with pytest.raises(WeakPasswordError) as exc:
        policy.validate("abcdefgh")
    assert "número" in exc.value.reason.lower() or "digito" in exc.value.reason.lower()


def test_policy_solo_pide_digito_rechaza_si_no_hay_digito() -> None:
    policy = PasswordPolicy(min_length=4, require_digit=True, require_letter=False)
    with pytest.raises(WeakPasswordError):
        policy.validate("solo-letras")


def test_policy_solo_pide_letra_rechaza_si_no_hay_letra() -> None:
    policy = PasswordPolicy(min_length=4, require_digit=False, require_letter=True)
    with pytest.raises(WeakPasswordError):
        policy.validate("12345678")


def test_min_length_expuesto_por_property() -> None:
    policy = PasswordPolicy(min_length=12, require_digit=False, require_letter=False)
    assert policy.min_length == 12


# ── Edge case: unicode no-ASCII ───────────────────────────────────────────────


def test_caracteres_unicode_no_ascii_no_cuentan_como_letra() -> None:
    """Decisión de diseño: ``require_letter`` exige ASCII (A-Z/a-z).

    Una contraseña como "ññññññññ1" NO tiene letras ASCII, así que falla.
    Esto evita ambigüedad con IMEs o copy/paste de textos exóticos.
    """
    policy = PasswordPolicy(min_length=4, require_digit=True, require_letter=True)
    with pytest.raises(WeakPasswordError):
        policy.validate("ññññññññ1")
