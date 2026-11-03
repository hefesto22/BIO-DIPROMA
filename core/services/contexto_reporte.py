"""Datos extra del reporte de asistencia: ficha, marcas, horarios y notas.

El reporte básico sale de la tabla ``asistencias`` (un renglón por
empleado y día). Para que el Excel sea completo y se pueda auditar hace
falta además:

    - La ficha de cada empleado: ID en el reloj, departamento y cargo.
    - TODAS las marcas guardadas del período (``registros_raw``), con el
      reloj que las registró. Las marcas nunca se borran: se acumulan
      sincronización tras sincronización, así que un reporte de mañana
      trae las de hoy más las nuevas.
    - El horario que tocaba cada día (los turnos pueden tener otro
      horario ciertos días), para el balance de atraso y tiempo extra.
    - Las vacaciones y permisos registrados (con su nota y quién los
      registró), para el control de ausencias.

Todo se lee en pocas consultas (una por tabla) y se arma en memoria.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from core.models.ausencia import TIPO_AUSENCIA_TEXTO, Ausencia
from core.models.empleado import Empleado
from core.repositories.ausencia_repository import IAusenciaReadRepository
from core.repositories.cargo_repository import ICargoReadRepository
from core.repositories.departamento_repository import IDepartamentoReadRepository
from core.repositories.dispositivo_repository import IDispositivoReadRepository
from core.repositories.empleado_repository import IEmpleadoReadRepository
from core.repositories.registro_raw_repository import IRegistroRawReadRepository
from core.repositories.turno_repository import ITurnoReadRepository
from core.repositories.usuario_repository import IUsuarioReadRepository
from core.services.asistencia_exporter import (
    AusenciaReporte,
    EmpleadoReporte,
    ExtrasReporte,
    MarcaReporte,
)
from core.services.balance_tiempo import HorarioDia


class ContextoReporte:
    """Junta lo que el reporte detallado necesita además de las asistencias."""

    def __init__(
        self,
        empleado_read: IEmpleadoReadRepository,
        departamento_read: IDepartamentoReadRepository,
        cargo_read: ICargoReadRepository,
        turno_read: ITurnoReadRepository,
        registro_raw_read: IRegistroRawReadRepository,
        dispositivo_read: IDispositivoReadRepository,
        ausencia_read: IAusenciaReadRepository,
        usuario_read: Optional[IUsuarioReadRepository] = None,
    ) -> None:
        self._empleados = empleado_read
        self._departamentos = departamento_read
        self._cargos = cargo_read
        self._turnos = turno_read
        self._marcas = registro_raw_read
        self._relojes = dispositivo_read
        self._ausencias = ausencia_read
        self._usuarios = usuario_read

    def cargar(self, desde: str, hasta: str, empleado_id: Optional[int] = None) -> ExtrasReporte:
        """Extras del período ``[desde, hasta]`` (ISO, inclusivo).

        Con ``empleado_id`` las marcas y notas se limitan a ese empleado.
        """
        empleados = [e for e in self._empleados.list_all() if e.id is not None]
        ausencias = [
            a
            for a in self._ausencias.list_en_rango(desde, hasta)
            if empleado_id is None or a.empleado_id == empleado_id
        ]
        return ExtrasReporte(
            empleados=self._fichas(empleados),
            marcas=self._marcas_del_periodo(empleados, desde, hasta, empleado_id),
            horarios=self._horarios(),
            notas_ausencia=self._notas(ausencias, desde, hasta),
            ausencias=self._registros(ausencias),
        )

    # ── Partes ────────────────────────────────────────────────────────────

    def _fichas(self, empleados: List[Empleado]) -> Dict[int, EmpleadoReporte]:
        deps = {d.id: d.nombre for d in self._departamentos.list_all() if d.id is not None}
        cargos = {c.id: c.nombre for c in self._cargos.list_all() if c.id is not None}
        return {
            e.id: EmpleadoReporte(
                nombre=f"{e.apellidos} {e.nombres}".strip(),
                dni=e.dni,
                id_reloj=e.zkteco_id,
                departamento=deps.get(e.departamento_id, ""),
                cargo=cargos.get(e.cargo_id, ""),
            )
            for e in empleados
            if e.id is not None
        }

    def _marcas_del_periodo(
        self,
        empleados: List[Empleado],
        desde: str,
        hasta: str,
        empleado_id: Optional[int],
    ) -> List[MarcaReporte]:
        por_id_reloj = {e.zkteco_id: e.id for e in empleados if e.zkteco_id is not None}
        relojes = {d.id: d.nombre for d in self._relojes.list_all() if d.id is not None}
        marcas = [
            MarcaReporte(
                momento=r.timestamp,
                id_reloj=r.zkteco_user_id,
                empleado_id=por_id_reloj.get(r.zkteco_user_id),
                reloj=relojes.get(r.dispositivo_id, f"Reloj #{r.dispositivo_id}"),
            )
            for r in self._marcas.list_by_rango(f"{desde}T00:00:00", f"{hasta}T23:59:59")
        ]
        if empleado_id is not None:
            marcas = [m for m in marcas if m.empleado_id == empleado_id]
        marcas.sort(key=lambda m: (m.momento, m.id_reloj))
        return marcas

    def _horarios(self) -> Dict[Tuple[int, int], HorarioDia]:
        horarios: Dict[Tuple[int, int], HorarioDia] = {}
        for turno in self._turnos.list_all():
            if turno.id is None:
                continue
            for dia in range(7):
                del_dia = turno.horario_para(dia)
                if del_dia is not None:
                    horarios[(turno.id, dia)] = HorarioDia(
                        entrada=del_dia.hora_entrada,
                        salida=del_dia.hora_salida,
                        cruza_medianoche=del_dia.cruza_medianoche,
                    )
        return horarios

    def _registros(self, ausencias: List[Ausencia]) -> List[AusenciaReporte]:
        usuarios: Dict[int, str] = {}
        if self._usuarios is not None:
            usuarios = {u.id: u.username for u in self._usuarios.list_all() if u.id is not None}
        registros = [
            AusenciaReporte(
                empleado_id=a.empleado_id,
                tipo=a.tipo,
                desde=a.fecha_desde,
                hasta=a.fecha_hasta,
                nota=(a.nota or "").strip() or None,
                registrado_por=(
                    usuarios.get(a.creado_por_user_id) if a.creado_por_user_id is not None else None
                ),
                registrado_el=a.creado_en,
            )
            for a in ausencias
        ]
        registros.sort(key=lambda r: (r.desde, r.empleado_id))
        return registros

    def _notas(
        self, ausencias: List[Ausencia], desde: str, hasta: str
    ) -> Dict[Tuple[int, str], str]:
        notas: Dict[Tuple[int, str], str] = {}
        inicio, fin = date.fromisoformat(desde), date.fromisoformat(hasta)
        for ausencia in ausencias:
            tipo = TIPO_AUSENCIA_TEXTO.get(ausencia.tipo, ausencia.tipo)
            nota = (ausencia.nota or "").strip()
            texto = f"{tipo}: {nota}" if nota else tipo
            dia = max(inicio, date.fromisoformat(ausencia.fecha_desde))
            ultimo = min(fin, date.fromisoformat(ausencia.fecha_hasta))
            while dia <= ultimo:
                notas[(ausencia.empleado_id, dia.isoformat())] = texto
                dia += timedelta(days=1)
        return notas
