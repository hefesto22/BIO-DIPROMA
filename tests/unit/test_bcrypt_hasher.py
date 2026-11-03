"""Tests del BcryptHasher."""

from __future__ import annotations

import pytest

from infrastructure.security.bcrypt_hasher import BcryptHasher

# Cost factor bajo permitido (mínimo del proyecto) para que los tests corran rápido.
# La REGLA exige >= 12, y 12 ya es aceptable para dev/test.
COST = 12


@pytest.fixture
def hasher() -> BcryptHasher:
    return BcryptHasher(cost_factor=COST)


def test_cost_factor_menor_a_12_falla() -> None:
    with pytest.raises(ValueError, match="cost_factor debe ser >= 12"):
        BcryptHasher(cost_factor=11)


def test_cost_factor_cero_falla() -> None:
    with pytest.raises(ValueError):
        BcryptHasher(cost_factor=0)


def test_cost_factor_aceptable(hasher: BcryptHasher) -> None:
    assert hasher.cost_factor == COST


def test_hash_produce_string_bcrypt(hasher: BcryptHasher) -> None:
    h = hasher.hash("mi-password-super-secreto")
    assert isinstance(h, str)
    # Prefijo bcrypt estándar para rounds 2b.
    assert h.startswith("$2b$12$")


def test_hash_password_vacio_falla(hasher: BcryptHasher) -> None:
    with pytest.raises(ValueError):
        hasher.hash("")


def test_hash_mismo_password_produce_diferentes_hashes(
    hasher: BcryptHasher,
) -> None:
    """bcrypt usa salt aleatorio → dos hashes del mismo texto nunca coinciden."""
    h1 = hasher.hash("abc123")
    h2 = hasher.hash("abc123")
    assert h1 != h2


def test_verify_correcto(hasher: BcryptHasher) -> None:
    h = hasher.hash("password-real")
    assert hasher.verify("password-real", h) is True


def test_verify_incorrecto(hasher: BcryptHasher) -> None:
    h = hasher.hash("password-real")
    assert hasher.verify("password-mala", h) is False


def test_verify_password_vacio_devuelve_false(hasher: BcryptHasher) -> None:
    h = hasher.hash("x")
    assert hasher.verify("", h) is False


def test_verify_hash_malformado_devuelve_false(hasher: BcryptHasher) -> None:
    """Un hash corrupto no debe crashear — solo retornar False."""
    assert hasher.verify("password", "esto-no-es-un-hash-bcrypt") is False
