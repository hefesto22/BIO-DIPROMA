"""Tests del modelo de dominio ``DescargaReporte``."""

from __future__ import annotations

from core.models.descarga_reporte import (
    ALL_TIPOS_REPORTE,
    DescargaReporte,
    TipoReporte,
)


def test_tipo_reporte_enum_solo_asistencia() -> None:
    """Por ahora el catálogo cerrado solo tiene ASISTENCIA (Sub-3.5)."""
    assert TipoReporte.ASISTENCIA.value == "ASISTENCIA"
    assert ALL_TIPOS_REPORTE == frozenset({"ASISTENCIA"})


def test_descarga_reporte_dataclass_acepta_campos_minimos() -> None:
    """Construir el dataclass con los campos obligatorios funciona."""
    descarga = DescargaReporte(
        id=None,
        user_id=42,
        fecha_hora_utc="2026-04-24T10:00:00+00:00",
        tipo_reporte=TipoReporte.ASISTENCIA.value,
        rango_desde="2026-04-01",
        rango_hasta="2026-04-30",
        ruta_archivo="/tmp/reporte.xlsx",
        filas_exportadas=120,
    )
    assert descarga.empleado_id_filtro is None  # default


def test_descarga_reporte_acepta_filtro_empleado_y_user_none() -> None:
    """``user_id`` puede ser None (caso de usuario eliminado, ON DELETE SET NULL)."""
    descarga = DescargaReporte(
        id=5,
        user_id=None,
        fecha_hora_utc="2026-04-24T10:00:00+00:00",
        tipo_reporte=TipoReporte.ASISTENCIA.value,
        rango_desde="2026-04-01",
        rango_hasta="2026-04-30",
        empleado_id_filtro=7,
        ruta_archivo="/tmp/reporte.xlsx",
        filas_exportadas=15,
    )
    assert descarga.user_id is None
    assert descarga.empleado_id_filtro == 7
