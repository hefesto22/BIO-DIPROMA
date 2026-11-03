"""Modelo de dominio: lo que el reloj ZKTeco informa de sí mismo.

Se llena al "Probar conexión" desde Configuración → Relojes. No se guarda
en la base de datos: es una foto del reloj en ese momento.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Tuple


@dataclass(frozen=True)
class UsuarioReloj:
    """Una persona registrada en el reloj.

    Atributos:
        user_id: Número con el que marca (el "ID" que se ve en el reloj).
            Es el mismo número que se guarda como ``zkteco_id`` del
            empleado. El reloj lo maneja como texto.
        nombre: Nombre tal como se escribió en el reloj (puede venir vacío).
    """

    user_id: str
    nombre: str = ""

    @property
    def numero(self) -> Optional[int]:
        """``user_id`` como entero, o ``None`` si el reloj guardó otra cosa."""
        texto = self.user_id.strip()
        return int(texto) if texto.isdigit() else None


@dataclass(frozen=True)
class InfoReloj:
    """Datos que devuelve el reloj al conectarse.

    Los textos pueden venir vacíos: no todos los firmwares responden a
    todas las preguntas.
    """

    nombre_equipo: str = ""
    serie: str = ""
    firmware: str = ""
    hora: Optional[datetime] = None
    usuarios: Tuple[UsuarioReloj, ...] = field(default_factory=tuple)
    marcas: int = 0
    capacidad_marcas: int = 0
