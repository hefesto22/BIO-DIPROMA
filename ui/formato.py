"""Formato de datos para mostrar en pantalla (sin Tk, testeable).

Convenciones de la app:
    - Fechas: ``dd/mm/aaaa`` (Honduras), nunca ISO en pantalla.
    - Días de un turno: texto corto ("Lun a Vie", "Sábado"...).
    - Duraciones: "8 h", "7 h 30 min", "42 s".
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Dict, Final, List, Optional, Sequence, Tuple

from core.models.empleado import MotivoBaja
from core.models.turno import (
    DIAS_FIN_DE_SEMANA,
    DIAS_LABORALES,
    DIAS_TODA_LA_SEMANA,
    DOMINGO,
    JUEVES,
    LUNES,
    MARTES,
    MIERCOLES,
    SABADO,
    VIERNES,
    HorarioTurno,
    Turno,
)
from core.services.validators import duracion_turno_minutos

# (inicial, nombre corto, nombre largo, bit) en orden lunes → domingo.
DIAS: Final[Tuple[Tuple[str, str, str, int], ...]] = (
    ("L", "Lun", "Lunes", LUNES),
    ("M", "Mar", "Martes", MARTES),
    ("M", "Mié", "Miércoles", MIERCOLES),
    ("J", "Jue", "Jueves", JUEVES),
    ("V", "Vie", "Viernes", VIERNES),
    ("S", "Sáb", "Sábado", SABADO),
    ("D", "Dom", "Domingo", DOMINGO),
)

MOTIVO_BAJA_TEXTO: Final[Dict[str, str]] = {
    MotivoBaja.DESPIDO.value: "Despido",
    MotivoBaja.RENUNCIA.value: "Renuncia",
    MotivoBaja.JUBILACION.value: "Jubilación",
    MotivoBaja.FIN_CONTRATO.value: "Fin de contrato",
    MotivoBaja.FALLECIMIENTO.value: "Fallecimiento",
    MotivoBaja.OTRO.value: "Otro",
}


def fecha(iso: Optional[str]) -> str:
    """``"2026-10-05"`` (o un ISO con hora) → ``"05/10/2026"``; ``"—"`` si no hay dato."""
    if not iso:
        return "—"
    try:
        return date.fromisoformat(iso[:10]).strftime("%d/%m/%Y")
    except ValueError:
        return iso


_MESES: Final[Tuple[str, ...]] = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def fecha_larga(iso: Optional[str]) -> str:
    """``"2026-11-02"`` → ``"lunes 2 de noviembre de 2026"``."""
    if not iso:
        return "—"
    try:
        d = date.fromisoformat(iso[:10])
    except ValueError:
        return iso
    return f"{DIAS[d.weekday()][2].lower()} {d.day} de {_MESES[d.month - 1]} de {d.year}"


def rango_fechas(desde: str, hasta: str) -> str:
    """Rango compacto: ``"02/11/2026"`` (un día) o ``"02/11 al 06/11/2026"``."""
    if desde == hasta:
        return fecha(desde)
    try:
        d, h = date.fromisoformat(desde[:10]), date.fromisoformat(hasta[:10])
    except ValueError:
        return f"{fecha(desde)} al {fecha(hasta)}"
    if d.year == h.year:
        return f"{d:%d/%m} al {h:%d/%m/%Y}"
    return f"{d:%d/%m/%Y} al {h:%d/%m/%Y}"


def dias_cantidad(n: int) -> str:
    """``1`` → ``"1 día"``; ``5`` → ``"5 días"``."""
    return f"{n} día" if n == 1 else f"{n} días"


def fecha_hora(iso_utc: Optional[str]) -> str:
    """Timestamp ISO (UTC) → ``"31/10/2026 16:13"`` en hora local."""
    if not iso_utc:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_utc)
    except ValueError:
        return iso_utc
    if dt.tzinfo is not None:
        dt = dt.astimezone()
    return dt.strftime("%d/%m/%Y %H:%M")


def dias_texto(bitmask: int) -> str:
    """Bitmask de días → texto corto legible."""
    if bitmask == DIAS_TODA_LA_SEMANA:
        return "Todos los días"
    if bitmask == DIAS_LABORALES:
        return "Lun a Vie"
    if bitmask == DIAS_LABORALES | SABADO:
        return "Lun a Sáb"
    if bitmask == DIAS_FIN_DE_SEMANA:
        return "Sáb y Dom"
    activos = [d for d in DIAS if bitmask & d[3]]
    if len(activos) == 1:
        return activos[0][2]
    return ", ".join(d[1] for d in activos) if activos else "—"


def dias_corto(bitmask: int) -> str:
    """Como ``dias_texto`` pero un solo día va abreviado: ``"Sáb"``."""
    activos = [d for d in DIAS if bitmask & d[3]]
    if len(activos) == 1:
        return activos[0][1]
    return dias_texto(bitmask)


def horario_texto(turno: Turno, con_dias: bool = False) -> str:
    """Horas del turno, con los horarios alternos: ``"09:00–17:00 · Sáb 09:00–12:00"``.

    Con ``con_dias=True`` el principal también lleva sus días:
    ``"Lun a Vie 09:00–17:00 · Sáb 09:00–12:00"``.
    """
    partes = []
    for i, h in enumerate(turno.bloques()):
        horas = f"{h.hora_entrada}–{h.hora_salida}"
        partes.append(horas if i == 0 and not con_dias else f"{dias_corto(h.dias_semana)} {horas}")
    return "  ·  ".join(partes)


def minutos_por_semana(bloques: Sequence[HorarioTurno]) -> int:
    """Minutos efectivos de trabajo en una semana sumando todos los horarios."""
    total = 0
    for h in bloques:
        efectiva = horas_efectivas(h.hora_entrada, h.hora_salida, h.minutos_descanso)
        total += efectiva * bin(h.dias_semana).count("1")
    return total


def dias_activos(bitmask: int) -> List[Tuple[str, bool]]:
    """``[(inicial, activo), …]`` lunes → domingo, para los chips de días."""
    return [(inicial, bool(bitmask & bit)) for inicial, _c, _l, bit in DIAS]


def duracion(minutos: int) -> str:
    """``480`` → ``"8 h"``; ``450`` → ``"7 h 30 min"``; ``45`` → ``"45 min"``."""
    minutos = max(0, int(minutos))
    horas, resto = divmod(minutos, 60)
    if horas and resto:
        return f"{horas} h {resto} min"
    if horas:
        return f"{horas} h"
    return f"{resto} min"


def horas_efectivas(hora_entrada: str, hora_salida: str, minutos_descanso: int) -> int:
    """Minutos trabajados por día: duración del bloque menos el descanso."""
    return max(0, duracion_turno_minutos(hora_entrada, hora_salida) - minutos_descanso)


def antiguedad(fecha_ingreso: Optional[str], hoy: date) -> str:
    """Tiempo desde el ingreso: ``"3 años, 2 meses"``, ``"5 meses"``, ``"12 días"``."""
    if not fecha_ingreso:
        return "—"
    try:
        inicio = date.fromisoformat(fecha_ingreso[:10])
    except ValueError:
        return "—"
    if inicio > hoy:
        return "Aún no ingresa"
    meses = (hoy.year - inicio.year) * 12 + (hoy.month - inicio.month)
    if hoy.day < inicio.day:
        meses -= 1
    anios, meses = divmod(meses, 12)
    partes = []
    if anios:
        partes.append(f"{anios} año{'s' if anios != 1 else ''}")
    if meses:
        partes.append(f"{meses} mes{'es' if meses != 1 else ''}")
    if partes:
        return ", ".join(partes)
    dias = (hoy - inicio).days
    return f"{dias} día{'s' if dias != 1 else ''}"


def segundos_entre(inicio_iso: Optional[str], fin_iso: Optional[str]) -> str:
    """Duración entre dos timestamps ISO: ``"42 s"``, ``"1 min 5 s"``; ``"—"`` si falta uno."""
    if not inicio_iso or not fin_iso:
        return "—"
    try:
        total = int(
            (datetime.fromisoformat(fin_iso) - datetime.fromisoformat(inicio_iso)).total_seconds()
        )
    except (TypeError, ValueError):
        return "—"
    total = max(0, total)
    minutos, segundos = divmod(total, 60)
    return f"{minutos} min {segundos} s" if minutos else f"{segundos} s"
