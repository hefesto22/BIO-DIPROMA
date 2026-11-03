"""Adaptador de bcrypt para hashing y verificación de passwords.

Responsabilidad única (SRP): envolver las operaciones de bcrypt con una
API simple (``hash`` / ``verify``) y garantizar que el cost factor
cumpla el mínimo del proyecto (``>= 12`` por REGLA).

Diseño:
    - El cost factor se fija al construir el hasher (inyección explícita).
    - ``verify`` nunca propaga excepciones de bcrypt — siempre devuelve
      ``bool``. Esto evita que un hash malformado en BD reviente la UI.
    - Todas las operaciones trabajan con ``bytes`` internamente; la API
      pública acepta ``str`` por comodidad del caller.
"""

from __future__ import annotations

import logging

import bcrypt

_MIN_COST_FACTOR = 12


class BcryptHasher:
    """Wrapper minimalista sobre la librería ``bcrypt``."""

    def __init__(self, cost_factor: int) -> None:
        """Inicializa el hasher con un cost factor específico.

        Args:
            cost_factor: Rondas de bcrypt (parámetro ``rounds``).
                Debe ser ``>= 12`` por política del proyecto.

        Raises:
            ValueError: Si ``cost_factor`` es menor que 12.
        """
        if cost_factor < _MIN_COST_FACTOR:
            raise ValueError(f"cost_factor debe ser >= {_MIN_COST_FACTOR}, recibido: {cost_factor}")
        self._cost_factor = cost_factor
        self._log = logging.getLogger(self.__class__.__name__)

    @property
    def cost_factor(self) -> int:
        """Cost factor configurado (expuesto para tests y auditoría)."""
        return self._cost_factor

    def hash(self, password: str) -> str:
        """Genera un hash bcrypt del password en texto plano.

        Args:
            password: Password en texto plano.

        Returns:
            Hash bcrypt como ``str`` (formato ``$2b$...``).

        Raises:
            ValueError: Si ``password`` es vacío.
        """
        if not password:
            raise ValueError("password no puede ser vacío")
        salt = bcrypt.gensalt(rounds=self._cost_factor)
        hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
        return hashed.decode("utf-8")

    def verify(self, password: str, password_hash: str) -> bool:
        """Verifica si ``password`` produce ``password_hash``.

        Args:
            password: Password en texto plano a verificar.
            password_hash: Hash almacenado contra el cual comparar.

        Returns:
            ``True`` si el password coincide, ``False`` en cualquier otro
            caso (incluyendo hash malformado o bytes inválidos).
        """
        if not password or not password_hash:
            return False
        try:
            return bcrypt.checkpw(
                password.encode("utf-8"),
                password_hash.encode("utf-8"),
            )
        except ValueError:
            # Hash malformado, no-bcrypt, o corrupto. Lo reportamos al log
            # técnico pero el caller recibe el mismo ``False`` que para un
            # password incorrecto — no revelamos detalles al atacante.
            self._log.warning("Hash inválido al verificar password — tratando como fallo.")
            return False
