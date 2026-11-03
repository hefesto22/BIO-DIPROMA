"""Textos y categorías de los estados de asistencia (sin Tk, testeable)."""

from __future__ import annotations

from datetime import date
from typing import Dict, Final, Optional

from core.models.asistencia import Asistencia, EstadoAsistencia
from ui.formato import DIAS

E = EstadoAsistencia

ESTADO_TEXTO: Final[Dict[str, str]] = {
    E.PRESENTE.value: "Presente",
    E.TARDE.value: "Tarde",
    E.SALIDA_TEMPRANA.value: "Salida temprana",
    E.TARDE_Y_SALIDA_TEMPRANA.value: "Tarde y salida temprana",
    E.AUSENTE.value: "Ausente",
    E.INCOMPLETO.value: "Incompleto",
    E.VACACIONES.value: "Vacaciones",
    E.DIA_LIBRE.value: "Día libre",
    E.PERMISO.value: "Permiso",
    E.INCAPACIDAD.value: "Incapacidad",
    E.FERIADO.value: "Feriado",
    E.SIN_TURNO.value: "Sin turno",
}

# Tarjetas-contador de la vista (cada estado cae en a lo sumo una).
A_TIEMPO: Final[str] = "A_TIEMPO"
CON_ATRASO: Final[str] = "CON_ATRASO"
AUSENTES: Final[str] = "AUSENTES"
INCOMPLETAS: Final[str] = "INCOMPLETAS"
JUSTIFICADAS: Final[str] = "JUSTIFICADAS"

CATEGORIA: Final[Dict[str, str]] = {
    E.PRESENTE.value: A_TIEMPO,
    E.TARDE.value: CON_ATRASO,
    E.SALIDA_TEMPRANA.value: CON_ATRASO,
    E.TARDE_Y_SALIDA_TEMPRANA.value: CON_ATRASO,
    E.AUSENTE.value: AUSENTES,
    E.INCOMPLETO.value: INCOMPLETAS,
    E.VACACIONES.value: JUSTIFICADAS,
    E.DIA_LIBRE.value: JUSTIFICADAS,
    E.PERMISO.value: JUSTIFICADAS,
    E.INCAPACIDAD.value: JUSTIFICADAS,
}

# Tag de color de la fila en la tabla.
TAG: Final[Dict[str, str]] = {
    E.TARDE.value: "alerta",
    E.SALIDA_TEMPRANA.value: "alerta",
    E.TARDE_Y_SALIDA_TEMPRANA.value: "alerta",
    E.INCOMPLETO.value: "alerta",
    E.AUSENTE.value: "ausente",
    E.VACACIONES.value: "justificada",
    E.DIA_LIBRE.value: "justificada",
    E.PERMISO.value: "justificada",
    E.INCAPACIDAD.value: "justificada",
    E.FERIADO.value: "apagado",
    E.SIN_TURNO.value: "apagado",
}

# Marca que deja la consolidación cuando no hubo segunda marca.
_SALIDA_ASUMIDA: Final[str] = "Salida asumida"


def texto_estado(a: Asistencia) -> str:
    """``"Tarde (+12 min)"``, ``"Salida temprana (−20 min)"``, ``"Presente"``…"""
    base = ESTADO_TEXTO.get(a.estado, a.estado)
    if a.estado == E.TARDE.value and a.minutos_tarde:
        return f"{base} (+{a.minutos_tarde} min)"
    if a.estado == E.SALIDA_TEMPRANA.value and a.minutos_salida_temprana:
        return f"{base} (−{a.minutos_salida_temprana} min)"
    if a.estado == E.TARDE_Y_SALIDA_TEMPRANA.value:
        return f"Tarde +{a.minutos_tarde} · salida −{a.minutos_salida_temprana} min"
    return base


def hora(valor: Optional[str]) -> str:
    """``"08:57:32"`` → ``"08:57"``; vacío → ``"—"``."""
    return valor[:5] if valor else "—"


def salida(a: Asistencia) -> str:
    """Hora de salida; con ``*`` si la puso el sistema porque no hubo marca."""
    texto = hora(a.hora_salida_real)
    if a.hora_salida_real and a.observaciones and _SALIDA_ASUMIDA in a.observaciones:
        return f"{texto} *"
    return texto


def salida_asumida(a: Asistencia) -> bool:
    return bool(a.observaciones and _SALIDA_ASUMIDA in a.observaciones)


def tiene_nota(a: Asistencia) -> bool:
    """``True`` si alguien escribió una observación (no la automática del sistema)."""
    if not a.observaciones:
        return False
    return not a.observaciones.strip().startswith(_SALIDA_ASUMIDA)


def fecha_con_dia(iso: str) -> str:
    """``"2026-10-26"`` → ``"Lun 26/10"`` (el año ya está en el período elegido)."""
    try:
        d = date.fromisoformat(iso[:10])
    except ValueError:
        return iso
    return f"{DIAS[d.weekday()][1]} {d:%d/%m}"
