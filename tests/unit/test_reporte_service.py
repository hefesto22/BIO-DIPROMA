"""Tests del ReporteService (Sub-3.5a).

Cubre el caso de uso "exportar reporte de asistencia":
    - Path feliz: genera archivo + persiste descarga + audita.
    - Sin datos en el rango → ``ReporteSinDatosError`` y nada se persiste.
    - Falla del exporter (OSError) → ``ReporteIOError`` y nada se persiste.
    - Validación de rango: fecha inválida → InvalidDateError; desde > hasta
      → InvalidRangoError.
    - ``contar_asistencias`` delega al servicio de asistencia.
    - ``list_historial_descargas`` delega al repo.

Diseño: el ReporteService trabaja con stubs/mocks de los colaboradores
para mantener los tests rápidos y enfocados; los integration tests del
exporter y del repo viven en sus propios archivos.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple
from unittest.mock import MagicMock

import pytest

from core.models.asistencia import Asistencia, EstadoAsistencia
from core.models.descarga_reporte import DescargaReporte, TipoReporte
from core.repositories.descarga_reporte_repository import (
    IDescargaReporteReadRepository,
    IDescargaReporteWriteRepository,
)
from core.services.asistencia_exporter import (
    DatosReporteAsistencia,
    ExtrasReporte,
    IAsistenciaExporter,
    MarcaReporte,
)
from core.services.asistencia_service import AsistenciaService, AsistenciaVista
from core.services.audit_logger import AuditLogger
from core.services.errors import (
    InvalidDateError,
    InvalidRangoError,
    ReporteIOError,
    ReporteSinDatosError,
)
from core.services.contexto_reporte import ContextoReporte
from core.services.reporte_service import (
    ReporteService,
    nombre_archivo_reporte,
    ruta_disponible,
)


# ── Fábricas y stubs ────────────────────────────────────────────────────────


def _vista(emp_id: int = 1, nombre: str = "Pérez Juan") -> AsistenciaVista:
    return AsistenciaVista(
        asistencia=Asistencia(
            id=10,
            empleado_id=emp_id,
            fecha="2026-04-15",
            estado=EstadoAsistencia.PRESENTE.value,
            turno_id_aplicado=1,
            hora_entrada_real="08:00:00",
            hora_salida_real="17:00:00",
        ),
        empleado_nombre_completo=nombre,
        empleado_dni="0801-1990-12345",
        turno_nombre="Admin",
    )


class _StubExporter(IAsistenciaExporter):
    def __init__(self, *, raises: Optional[OSError] = None) -> None:
        self.calls: List[Tuple[DatosReporteAsistencia, str]] = []
        self._raises = raises

    def exportar(self, datos: DatosReporteAsistencia, output_path: str) -> int:
        self.calls.append((datos, output_path))
        if self._raises is not None:
            raise self._raises
        return len(datos.items)


class _StubDescargaRepo(IDescargaReporteReadRepository, IDescargaReporteWriteRepository):
    def __init__(self) -> None:
        self.inserted: List[DescargaReporte] = []
        self._next_id = 1
        self.recientes: List[DescargaReporte] = []

    def insert(self, descarga: DescargaReporte) -> DescargaReporte:
        nueva = DescargaReporte(
            id=self._next_id,
            user_id=descarga.user_id,
            fecha_hora_utc=descarga.fecha_hora_utc,
            tipo_reporte=descarga.tipo_reporte,
            rango_desde=descarga.rango_desde,
            rango_hasta=descarga.rango_hasta,
            empleado_id_filtro=descarga.empleado_id_filtro,
            ruta_archivo=descarga.ruta_archivo,
            filas_exportadas=descarga.filas_exportadas,
        )
        self.inserted.append(nueva)
        self._next_id += 1
        return nueva

    def get_by_id(self, descarga_id: int) -> Optional[DescargaReporte]:
        for d in self.inserted:
            if d.id == descarga_id:
                return d
        return None

    def list_recientes(self, limit: int = 50) -> List[DescargaReporte]:
        return self.recientes[:limit]

    def list_by_user(self, user_id: int, limit: int = 50) -> List[DescargaReporte]:
        return [d for d in self.recientes if d.user_id == user_id][:limit]


def _build_service(
    *,
    list_result: Optional[List[AsistenciaVista]] = None,
    count_result: int = 0,
    exporter_raises: Optional[OSError] = None,
    empleados_result: Optional[List[Tuple[int, str]]] = None,
) -> Tuple[ReporteService, _StubExporter, _StubDescargaRepo, MagicMock, MagicMock]:
    """Arma el servicio con stubs/mocks; devuelve también las refs para asserts."""
    asist_service = MagicMock(spec=AsistenciaService)
    asist_service.list_asistencias_para_reporte.return_value = (
        list_result if list_result is not None else []
    )
    asist_service.contar_asistencias_para_reporte.return_value = count_result
    asist_service.list_empleados_para_filtro.return_value = (
        empleados_result if empleados_result is not None else []
    )
    descarga_repo = _StubDescargaRepo()
    exporter = _StubExporter(raises=exporter_raises)
    audit_logger = MagicMock(spec=AuditLogger)
    service = ReporteService(
        asistencia_service=asist_service,
        descarga_read=descarga_repo,
        descarga_write=descarga_repo,
        exporter=exporter,
        audit_logger=audit_logger,
    )
    return service, exporter, descarga_repo, audit_logger, asist_service


# ── exportar_asistencia: path feliz ─────────────────────────────────────────


def test_exportar_genera_archivo_y_persiste_historial() -> None:
    service, exporter, descarga_repo, audit_logger, _ = _build_service(list_result=[_vista()])
    descarga = service.exportar_asistencia(
        desde="2026-04-01",
        hasta="2026-04-30",
        output_path="/tmp/x.xlsx",
        actor_user_id=42,
        actor_username="alice",
    )
    # Exporter llamado UNA vez con la ruta correcta.
    assert len(exporter.calls) == 1
    datos_export, ruta = exporter.calls[0]
    assert ruta == "/tmp/x.xlsx"
    assert datos_export.actor_username == "alice"
    # Historial persistido con id asignado.
    assert descarga.id == 1
    assert descarga.user_id == 42
    assert descarga.filas_exportadas == 1
    assert descarga.ruta_archivo == "/tmp/x.xlsx"
    # Audit log invocado UNA vez.
    audit_logger.log.assert_called_once()
    args = audit_logger.log.call_args
    assert args.kwargs["action"] == "reporte_asistencia_exportado"
    assert args.kwargs["user_id"] == 42


def test_exportar_pasa_filtro_empleado_y_resuelve_nombre() -> None:
    """Cuando hay filtro, el bundle incluye el nombre del primer item."""
    service, exporter, _, _, _ = _build_service(list_result=[_vista(emp_id=7, nombre="Pérez Juan")])
    service.exportar_asistencia(
        desde="2026-04-01",
        hasta="2026-04-30",
        output_path="/tmp/x.xlsx",
        actor_user_id=1,
        actor_username="bob",
        empleado_id=7,
    )
    datos_export, _ = exporter.calls[0]
    assert datos_export.empleado_id_filtro == 7
    assert datos_export.empleado_nombre_filtro == "Pérez Juan"


def test_exportar_sin_filtro_deja_nombre_filtro_none() -> None:
    service, exporter, _, _, _ = _build_service(list_result=[_vista()])
    service.exportar_asistencia(
        desde="2026-04-01",
        hasta="2026-04-30",
        output_path="/tmp/x.xlsx",
        actor_user_id=1,
        actor_username="bob",
    )
    datos_export, _ = exporter.calls[0]
    assert datos_export.empleado_id_filtro is None
    assert datos_export.empleado_nombre_filtro is None


# ── exportar_asistencia: errores ─────────────────────────────────────────────


def test_exportar_sin_datos_lanza_y_no_persiste() -> None:
    service, exporter, descarga_repo, audit_logger, _ = _build_service(list_result=[])
    with pytest.raises(ReporteSinDatosError):
        service.exportar_asistencia(
            desde="2026-04-01",
            hasta="2026-04-30",
            output_path="/tmp/x.xlsx",
            actor_user_id=1,
            actor_username="alice",
        )
    assert exporter.calls == []
    assert descarga_repo.inserted == []
    audit_logger.log.assert_not_called()


def test_exportar_falla_io_lanza_reporte_io_error_y_no_persiste() -> None:
    service, _, descarga_repo, audit_logger, _ = _build_service(
        list_result=[_vista()],
        exporter_raises=PermissionError("Permiso denegado"),
    )
    with pytest.raises(ReporteIOError) as exc_info:
        service.exportar_asistencia(
            desde="2026-04-01",
            hasta="2026-04-30",
            output_path="/tmp/x.xlsx",
            actor_user_id=1,
            actor_username="alice",
        )
    assert "/tmp/x.xlsx" in str(exc_info.value)
    assert descarga_repo.inserted == []
    audit_logger.log.assert_not_called()


def test_exportar_rango_invertido_lanza_invalid_rango() -> None:
    service, exporter, _, _, asist_service = _build_service()
    with pytest.raises(InvalidRangoError):
        service.exportar_asistencia(
            desde="2026-04-30",
            hasta="2026-04-01",
            output_path="/tmp/x.xlsx",
            actor_user_id=1,
            actor_username="a",
        )
    # Ni siquiera se intenta listar.
    asist_service.list_asistencias_para_reporte.assert_not_called()
    assert exporter.calls == []


def test_exportar_fecha_invalida_lanza_invalid_date_error() -> None:
    service, _, _, _, _ = _build_service()
    with pytest.raises(InvalidDateError):
        service.exportar_asistencia(
            desde="no-es-fecha",
            hasta="2026-04-30",
            output_path="/tmp/x.xlsx",
            actor_user_id=1,
            actor_username="a",
        )


# ── contar_asistencias ──────────────────────────────────────────────────────


def test_contar_asistencias_delega_al_servicio() -> None:
    service, _, _, _, asist_service = _build_service(count_result=42)
    assert service.contar_asistencias(desde="2026-04-01", hasta="2026-04-30") == 42
    asist_service.contar_asistencias_para_reporte.assert_called_once_with(
        desde="2026-04-01", hasta="2026-04-30", empleado_id=None
    )


def test_contar_asistencias_valida_rango() -> None:
    service, _, _, _, asist_service = _build_service()
    with pytest.raises(InvalidRangoError):
        service.contar_asistencias(desde="2026-04-30", hasta="2026-04-01")
    asist_service.contar_asistencias_para_reporte.assert_not_called()


# ── list_historial_descargas ────────────────────────────────────────────────


def test_list_historial_descargas_delega_al_repo() -> None:
    service, _, descarga_repo, _, _ = _build_service()
    descarga_repo.recientes = [
        DescargaReporte(
            id=10,
            user_id=1,
            fecha_hora_utc="2026-04-24T10:00:00+00:00",
            tipo_reporte=TipoReporte.ASISTENCIA.value,
            rango_desde="2026-04-01",
            rango_hasta="2026-04-30",
            ruta_archivo="/tmp/x.xlsx",
            filas_exportadas=12,
        ),
    ]
    result = service.list_historial_descargas(limit=5)
    assert len(result) == 1
    assert result[0].id == 10


# ── Sanity: el bundle pasado al exporter contiene resumen no vacío ──────────


def test_exportar_arma_bundle_con_resumen_calculado() -> None:
    """El servicio NO confía en el exporter para calcular — pasa el resumen."""
    items = [_vista(emp_id=1)]
    service, exporter, _, _, _ = _build_service(list_result=items)
    service.exportar_asistencia(
        desde="2026-04-01",
        hasta="2026-04-30",
        output_path="/tmp/x.xlsx",
        actor_user_id=1,
        actor_username="a",
    )
    datos_export, _ = exporter.calls[0]
    assert len(datos_export.resumen_por_empleado) == 1
    assert datos_export.resumen_por_empleado[0].dias_presente == 1


# ── list_empleados_para_filtro ──────────────────────────────────────────────


def test_list_empleados_para_filtro_delega_al_asistencia_service() -> None:
    """El servicio delega 1:1 sin transformar los datos."""
    empleados = [(1, "Pérez Juan"), (2, "Gómez María")]
    service, _, _, _, asist_service = _build_service(empleados_result=empleados)
    resultado = service.list_empleados_para_filtro()
    assert resultado == empleados
    asist_service.list_empleados_para_filtro.assert_called_once_with()


# ── Cobertura: inserted preserva timestamp del servicio ─────────────────────


def test_exportar_timestamp_es_consistente_entre_descarga_y_bundle() -> None:
    """El servicio genera UN timestamp y lo usa tanto en la descarga como
    en los metadatos del bundle (consistencia auditable)."""
    items = [_vista()]
    service, exporter, descarga_repo, _, _ = _build_service(list_result=items)
    service.exportar_asistencia(
        desde="2026-04-01",
        hasta="2026-04-30",
        output_path="/tmp/x.xlsx",
        actor_user_id=1,
        actor_username="a",
    )
    bundle, _ = exporter.calls[0]
    descarga = descarga_repo.inserted[0]
    assert bundle.timestamp_iso == descarga.fecha_hora_utc


# ── Nombre del archivo y guardado en carpeta ────────────────────────────────


def test_nombre_del_reporte_lleva_fecha_y_hora() -> None:
    cuando = datetime(2026, 11, 2, 23, 44, 5)
    assert nombre_archivo_reporte(cuando) == "Reporte_2026-11-02_23-44-05.xlsx"


def test_nombre_del_reporte_de_un_empleado_lleva_su_nombre_sin_tildes() -> None:
    cuando = datetime(2026, 11, 2, 23, 44, 5)
    assert (
        nombre_archivo_reporte(cuando, "Paz Mejía Ricardo Alberto")
        == "Reporte_2026-11-02_23-44-05_Paz_Mejia_Ricardo_Alberto.xlsx"
    )
    assert nombre_archivo_reporte(cuando, "Núñez O'Brien, José") == (
        "Reporte_2026-11-02_23-44-05_Nunez_O_Brien_Jose.xlsx"
    )


def test_ruta_disponible_nunca_pisa_un_reporte_existente(tmp_path: Path) -> None:
    nombre = "Reporte_2026-11-02_23-44-05.xlsx"
    assert ruta_disponible(tmp_path, nombre) == tmp_path / nombre
    (tmp_path / nombre).write_bytes(b"x")
    assert ruta_disponible(tmp_path, nombre).name == "Reporte_2026-11-02_23-44-05_2.xlsx"
    (tmp_path / "Reporte_2026-11-02_23-44-05_2.xlsx").write_bytes(b"x")
    assert ruta_disponible(tmp_path, nombre).name == "Reporte_2026-11-02_23-44-05_3.xlsx"


def _servicio_con_reloj(
    tmp_path: Path, list_result: List[AsistenciaVista]
) -> Tuple[ReporteService, _StubExporter]:
    asist_service = MagicMock(spec=AsistenciaService)
    asist_service.list_asistencias_para_reporte.return_value = list_result
    exporter = _StubExporter()
    repo = _StubDescargaRepo()
    service = ReporteService(
        asistencia_service=asist_service,
        descarga_read=repo,
        descarga_write=repo,
        exporter=exporter,
        audit_logger=MagicMock(spec=AuditLogger),
        ahora=lambda: datetime(2026, 11, 2, 23, 44, 5),
    )
    return service, exporter


def test_exportar_en_carpeta_usa_nombre_unico_y_crea_la_carpeta(tmp_path: Path) -> None:
    service, exporter = _servicio_con_reloj(tmp_path, [_vista()])
    carpeta = tmp_path / "Reportes"
    descarga = service.exportar_asistencia_en_carpeta(
        "2026-11-01", "2026-11-02", str(carpeta), actor_user_id=1, actor_username="admin"
    )
    assert carpeta.is_dir()
    assert descarga.ruta_archivo == str(carpeta / "Reporte_2026-11-02_23-44-05.xlsx")
    datos, ruta = exporter.calls[0]
    assert ruta == descarga.ruta_archivo
    assert datos.generado_local == "2026-11-02T23:44:05"


def test_exportar_en_carpeta_de_un_empleado_agrega_su_nombre(tmp_path: Path) -> None:
    service, _ = _servicio_con_reloj(tmp_path, [_vista(7, "Paz Mejía Ricardo")])
    descarga = service.exportar_asistencia_en_carpeta(
        "2026-11-01", "2026-11-02", str(tmp_path), 1, "admin", empleado_id=7
    )
    assert descarga.ruta_archivo.endswith("Reporte_2026-11-02_23-44-05_Paz_Mejia_Ricardo.xlsx")


def test_exportar_dos_veces_en_el_mismo_segundo_no_pisa_el_primero(tmp_path: Path) -> None:
    service, _ = _servicio_con_reloj(tmp_path, [_vista()])
    primero = service.exportar_asistencia_en_carpeta(
        "2026-11-01", "2026-11-02", str(tmp_path), 1, "admin"
    )
    Path(primero.ruta_archivo).write_bytes(b"xlsx")  # el stub no escribe el archivo
    segundo = service.exportar_asistencia_en_carpeta(
        "2026-11-01", "2026-11-02", str(tmp_path), 1, "admin"
    )
    assert segundo.ruta_archivo != primero.ruta_archivo
    assert segundo.ruta_archivo.endswith("_2.xlsx")


def test_exportar_en_carpeta_sin_datos_no_crea_nada(tmp_path: Path) -> None:
    service, exporter = _servicio_con_reloj(tmp_path, [])
    carpeta = tmp_path / "Reportes"
    with pytest.raises(ReporteSinDatosError):
        service.exportar_asistencia_en_carpeta("2026-11-01", "2026-11-02", str(carpeta), 1, "a")
    assert exporter.calls == []
    assert not carpeta.exists()


def test_exportar_lleva_los_extras_del_contexto() -> None:
    asist_service = MagicMock(spec=AsistenciaService)
    asist_service.list_asistencias_para_reporte.return_value = [_vista()]
    contexto = MagicMock(spec=ContextoReporte)
    extras = ExtrasReporte(marcas=[MarcaReporte("2026-04-15T08:00:00", 7, 1, "Reloj")])
    contexto.cargar.return_value = extras
    exporter = _StubExporter()
    repo = _StubDescargaRepo()
    service = ReporteService(
        asistencia_service=asist_service,
        descarga_read=repo,
        descarga_write=repo,
        exporter=exporter,
        audit_logger=MagicMock(spec=AuditLogger),
        contexto=contexto,
    )
    service.exportar_asistencia("2026-04-01", "2026-04-30", "/tmp/x.xlsx", 1, "a", empleado_id=1)
    contexto.cargar.assert_called_once_with("2026-04-01", "2026-04-30", 1)
    assert exporter.calls[0][0].extras is extras


def test_primera_fecha_delega_al_servicio_de_asistencia() -> None:
    service, _, _, _, asist = _build_service()
    asist.primera_fecha.return_value = "2026-09-03"
    assert service.primera_fecha() == "2026-09-03"
