"""Helpers de validación puros para la capa de servicios de Fase 2.

Son funciones libres sin dependencias externas: solo ``datetime``, ``re``
y los errores del módulo ``errors``. Se importan desde ``CatalogoService``,
``TurnoService`` y ``EmpleadoService`` para evitar duplicar la misma
regla en tres lugares.

Los mensajes en las excepciones ya vienen en español listos para mostrar
al usuario final — los servicios no reformatean, simplemente re-lanzan.

Convenciones:
    - Las funciones con nombre ``validate_*`` devuelven ``None`` cuando
      la entrada es válida, o levantan una subclase de
      ``ValidationError``.
    - Las funciones con nombre ``normalize_*`` devuelven la versión
      canonicalizada (no mutan el input original).
    - ``cruza_medianoche`` y utilidades de horas devuelven el valor
      calculado — no son validaciones.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from core.services.errors import (
    InvalidBitmaskError,
    InvalidDateError,
    InvalidDNIError,
    InvalidTimeError,
    MissingRequiredFieldError,
)

# ── DNI hondureño ─────────────────────────────────────────────────────────────

# Formato: XXXX-XXXX-XXXXX (4 dígitos, guion, 4 dígitos, guion, 5 dígitos).
# No se valida el código de departamento (Level 3) — queda como deuda
# técnica registrada en el plan de Sub-2.3.
_DNI_RE = re.compile(r"^\d{4}-\d{4}-\d{5}$")


def validate_dni(dni: str) -> str:
    """Valida el formato del DNI hondureño y devuelve la versión normalizada.

    Args:
        dni: DNI crudo del caller (puede venir con espacios alrededor).

    Returns:
        El DNI con ``strip()`` aplicado. El formato interno no se
        cambia — el schema UNIQUE es case-sensitive pero el DNI solo
        tiene dígitos y guiones, así que la única normalización es
        recortar espacios.

    Raises:
        MissingRequiredFieldError: Si viene vacío o solo whitespace.
        InvalidDNIError: Si no matchea la máscara ``XXXX-XXXX-XXXXX``.
    """
    if dni is None or not dni.strip():
        raise MissingRequiredFieldError("dni")
    normalizado = dni.strip()
    if not _DNI_RE.match(normalizado):
        raise InvalidDNIError(normalizado)
    return normalizado


# ── Fechas ISO ────────────────────────────────────────────────────────────────


def validate_fecha_iso(valor: str, campo: str) -> date:
    """Valida que ``valor`` sea una fecha ISO ``YYYY-MM-DD`` válida.

    Args:
        valor: String candidato.
        campo: Nombre del campo (para el mensaje de error).

    Returns:
        El ``date`` parseado — útil para aritmética posterior.

    Raises:
        MissingRequiredFieldError: Si viene vacío.
        InvalidDateError: Si el formato o la fecha concreta son inválidos
            (ej. ``"2026-02-30"``).
    """
    if valor is None or not valor.strip():
        raise MissingRequiredFieldError(campo)
    try:
        return datetime.strptime(valor.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise InvalidDateError(valor, campo) from exc


# ── Horas 24h ─────────────────────────────────────────────────────────────────

_HORA_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def validate_hora_hhmm(valor: str, campo: str) -> None:
    """Valida que ``valor`` sea una hora ``HH:MM`` en rango 24h.

    Args:
        valor: String candidato.
        campo: Nombre del campo (para el mensaje de error).

    Raises:
        MissingRequiredFieldError: Si viene vacío.
        InvalidTimeError: Si no matchea ``HH:MM`` o los valores están
            fuera de rango (ej. ``"25:00"``).
    """
    if valor is None or not valor.strip():
        raise MissingRequiredFieldError(campo)
    if not _HORA_RE.match(valor.strip()):
        raise InvalidTimeError(valor, campo)


def minutos_desde_medianoche(hhmm: str) -> int:
    """Convierte ``"HH:MM"`` a minutos desde las 00:00.

    Pre-condición: ``hhmm`` ya fue validado con ``validate_hora_hhmm``.
    """
    hh_s, mm_s = hhmm.split(":")
    return int(hh_s) * 60 + int(mm_s)


def duracion_turno_minutos(hora_entrada: str, hora_salida: str) -> int:
    """Calcula la duración en minutos de un turno, considerando medianoche.

    Si la hora de salida es menor que la de entrada, se asume que el
    turno cruza medianoche y se suman 24 horas al tramo final.

    Pre-condición: ambas horas ya validadas con ``validate_hora_hhmm``.

    Returns:
        Minutos totales del bloque (ignora ``minutos_descanso``).
    """
    entrada_min = minutos_desde_medianoche(hora_entrada)
    salida_min = minutos_desde_medianoche(hora_salida)
    if salida_min <= entrada_min:
        salida_min += 24 * 60
    return salida_min - entrada_min


def cruza_medianoche(hora_entrada: str, hora_salida: str) -> bool:
    """Indica si el turno cruza medianoche (salida < entrada en el mismo día).

    Pre-condición: ambas horas ya validadas con ``validate_hora_hhmm``.
    Si ``hora_salida == hora_entrada`` se considera turno de 24 horas y
    se marca como cruza (caso borde raro pero posible en vigilancia).
    """
    return minutos_desde_medianoche(hora_salida) <= minutos_desde_medianoche(hora_entrada)


# ── Bitmask de días de la semana ──────────────────────────────────────────────


def validate_bitmask_dias(valor: int) -> None:
    """Valida que el bitmask de días esté en el rango 1..127.

    El 0 se rechaza explícitamente: un turno sin ningún día aplicable no
    tiene sentido (nunca se usaría).

    Raises:
        InvalidBitmaskError: Si ``valor`` está fuera de 1..127.
    """
    if not (1 <= valor <= 127):
        raise InvalidBitmaskError(valor)


# ── Minutos de descanso ───────────────────────────────────────────────────────


def validate_minutos_descanso(valor: int) -> None:
    """Valida que los minutos de descanso sean no-negativos.

    La comparación contra la duración del turno se hace en el servicio
    (que ya tiene las horas).

    Raises:
        ValueError: Si ``valor < 0``. Se mantiene como ``ValueError``
            estándar porque la UI ya restringe el input a un spinbox
            — llegar aquí indica un bug del caller, no un error del
            usuario.
    """
    if valor < 0:
        raise ValueError(f"Los minutos de descanso no pueden ser negativos (recibido: {valor}).")


# ── Texto libre (nombres de catálogo) ─────────────────────────────────────────


_MULTIPLE_SPACES_RE = re.compile(r"\s+")


def normalize_nombre(valor: str, campo: str) -> str:
    """Normaliza un nombre de catálogo: trim + colapsa espacios múltiples.

    NO cambia el case — el UNIQUE del schema es case-sensitive por diseño
    (ver decisión de Fase 2). "Obras Públicas" y "obras públicas" son
    entradas distintas a propósito.

    Args:
        valor: String crudo.
        campo: Nombre del campo (para el mensaje de error).

    Returns:
        String normalizado, nunca vacío.

    Raises:
        MissingRequiredFieldError: Si el string queda vacío después de
            normalizar.
    """
    if valor is None:
        raise MissingRequiredFieldError(campo)
    normalizado = _MULTIPLE_SPACES_RE.sub(" ", valor).strip()
    if not normalizado:
        raise MissingRequiredFieldError(campo)
    return normalizado
