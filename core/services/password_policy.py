"""Política de complejidad de passwords.

Módulo aislado (SRP) que centraliza las reglas de validación. Se usa
en el Setup Wizard para crear el primer SUPERADMIN y queda disponible
para futuros flujos (cambio de password, creación de usuarios por el
SUPERADMIN, etc.).

Diseño:
    - ``PasswordPolicy`` se inyecta con sus parámetros (min_length,
      require_digit, require_letter). Los valores por defecto se
      toman de ``config.py`` a nivel de composición, no dentro del
      servicio — esto mantiene el servicio libre de side-effects y
      fácil de testear con políticas distintas.
    - ``validate`` lanza ``WeakPasswordError`` con un mensaje en español
      apto para mostrar al usuario final.
"""

from __future__ import annotations

from core.services.errors import WeakPasswordError


class PasswordPolicy:
    """Valida que una password cumpla las reglas mínimas del proyecto."""

    def __init__(
        self,
        min_length: int,
        require_digit: bool,
        require_letter: bool,
    ) -> None:
        """Inicializa la política.

        Args:
            min_length: Longitud mínima (inclusive). Debe ser ``>= 1``.
            require_digit: Si ``True``, exige al menos un dígito (0-9).
            require_letter: Si ``True``, exige al menos una letra
                (A-Z / a-z, ASCII). Se evita ``str.isalpha`` a propósito
                para no aceptar caracteres Unicode raros en contexto de
                usuarios técnicos.

        Raises:
            ValueError: Si ``min_length`` es menor que 1.
        """
        if min_length < 1:
            raise ValueError("min_length debe ser >= 1")
        self._min_length = min_length
        self._require_digit = require_digit
        self._require_letter = require_letter

    @property
    def min_length(self) -> int:
        """Longitud mínima configurada (expuesta para tests y UI)."""
        return self._min_length

    def validate(self, password: str) -> None:
        """Valida el password. No devuelve nada; lanza si falla.

        Raises:
            WeakPasswordError: Si alguna regla de la política no se cumple.
                El mensaje describe la primera regla incumplida (no se
                acumulan errores — el caller arregla una cosa a la vez).
        """
        if not password:
            raise WeakPasswordError("La contraseña no puede estar vacía.")
        if len(password) < self._min_length:
            raise WeakPasswordError(
                f"La contraseña debe tener al menos {self._min_length} caracteres."
            )
        if self._require_letter and not _contiene_letra_ascii(password):
            raise WeakPasswordError("La contraseña debe contener al menos una letra.")
        if self._require_digit and not _contiene_digito(password):
            raise WeakPasswordError("La contraseña debe contener al menos un número.")


def _contiene_letra_ascii(texto: str) -> bool:
    """True si ``texto`` contiene al menos una letra ASCII (A-Z o a-z)."""
    return any(("a" <= c <= "z") or ("A" <= c <= "Z") for c in texto)


def _contiene_digito(texto: str) -> bool:
    """True si ``texto`` contiene al menos un dígito (0-9)."""
    return any("0" <= c <= "9" for c in texto)
