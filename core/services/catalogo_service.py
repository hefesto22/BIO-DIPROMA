"""Servicio de catálogos maestros: departamentos y cargos.

Responsabilidad única: orquestar el CRUD de los dos catálogos simples de
la municipalidad (``departamentos`` y ``cargos``). Ambos son "gemelos"
por diseño (Decisión 3 del PRD de Fase 2) — mismo esquema, mismas
reglas — por eso se agrupan en un solo servicio para evitar duplicar
métodos y inyecciones.

Reglas de negocio aplicadas:

    - Nombres con ``strip()`` + colapso de espacios. Case-sensitive
      (consistente con el ``UNIQUE`` del schema).
    - Check explícito de duplicado antes del INSERT/UPDATE — así se
      puede lanzar ``DuplicateNombreError`` con mensaje en español en
      lugar de dejar que SQLite levante un ``IntegrityError`` críptico.
    - Al archivar, se valida que no haya empleados activos asignados a
      ese catálogo; si los hay → ``CatalogoInUseError``.
    - Cada mutación genera una entrada en ``audit_log`` con el
      ``actor_user_id`` pasado por el controller.

Diseño (SOLID):
    - S: solo gestiona catálogos. No toca empleados ni turnos.
    - D: recibe repos + audit_logger por inyección; no conoce SQLite.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Set

from core.models.cargo import Cargo
from core.models.departamento import Departamento
from core.repositories.cargo_repository import (
    ICargoReadRepository,
    ICargoWriteRepository,
)
from core.repositories.departamento_repository import (
    IDepartamentoReadRepository,
    IDepartamentoWriteRepository,
)
from core.repositories.empleado_repository import IEmpleadoReadRepository
from core.services.audit_logger import AuditLogger
from core.services.errors import (
    CargoIncompatibleError,
    CargoUsadoEnOtrosDepartamentosError,
    CatalogoInUseError,
    CatalogoNotFoundError,
    DestinoInvalidoError,
    DuplicateNombreError,
)
from core.services.validators import normalize_nombre

# Literales de "qué catálogo" que aparecen en audit_log y en los
# mensajes de error — centralizados para que el día que agreguemos un
# tercer catálogo no haya que grepar strings sueltas.
_CAT_DEPARTAMENTO = "departamento"
_CAT_CARGO = "cargo"


@dataclass(frozen=True)
class ResultadoMovimiento:
    """Qué se movió al archivar pasando el personal a otro catálogo."""

    empleados: int
    cargos: int = 0


class CatalogoService:
    """CRUD + archivado de los dos catálogos maestros."""

    def __init__(
        self,
        dep_read: IDepartamentoReadRepository,
        dep_write: IDepartamentoWriteRepository,
        cargo_read: ICargoReadRepository,
        cargo_write: ICargoWriteRepository,
        empleado_read: IEmpleadoReadRepository,
        audit_logger: AuditLogger,
    ) -> None:
        """Inicializa el servicio con todas sus dependencias inyectadas."""
        self._dep_read = dep_read
        self._dep_write = dep_write
        self._cargo_read = cargo_read
        self._cargo_write = cargo_write
        self._empleado_read = empleado_read
        self._audit = audit_logger
        self._log = logging.getLogger(self.__class__.__name__)

    # ── Departamentos ─────────────────────────────────────────────────────

    def list_departamentos(self, solo_activos: bool = True) -> List[Departamento]:
        """Devuelve la lista de departamentos (activos por default)."""
        if solo_activos:
            return self._dep_read.list_active()
        return self._dep_read.list_all()

    def get_departamento(self, departamento_id: int) -> Departamento:
        """Devuelve un departamento por id.

        Raises:
            CatalogoNotFoundError: Si no existe el id.
        """
        dep = self._dep_read.get_by_id(departamento_id)
        if dep is None:
            raise CatalogoNotFoundError(_CAT_DEPARTAMENTO, departamento_id)
        return dep

    def create_departamento(self, nombre: str, actor_user_id: int) -> Departamento:
        """Crea un departamento nuevo.

        Args:
            nombre: Texto libre. Se normaliza (trim + colapso de espacios).
            actor_user_id: Usuario que ejecuta la acción (para audit_log).

        Raises:
            MissingRequiredFieldError: ``nombre`` vacío tras normalizar.
            DuplicateNombreError: Ya existe un departamento con ese nombre.
        """
        nombre_norm = normalize_nombre(nombre, "nombre")
        if self._dep_read.get_by_nombre(nombre_norm) is not None:
            raise DuplicateNombreError(_CAT_DEPARTAMENTO, nombre_norm)
        creado = self._dep_write.create(Departamento(id=None, nombre=nombre_norm))
        assert creado.id is not None
        self._audit.log(
            action="departamento_created",
            user_id=actor_user_id,
            details=f'{{"id": {creado.id}, "nombre": "{nombre_norm}"}}',
        )
        self._log.info("Departamento creado: id=%s nombre=%s", creado.id, nombre_norm)
        return creado

    def rename_departamento(
        self,
        departamento_id: int,
        nuevo_nombre: str,
        actor_user_id: int,
    ) -> None:
        """Renombra un departamento existente.

        Raises:
            CatalogoNotFoundError: ``departamento_id`` no existe.
            DuplicateNombreError: El nuevo nombre colisiona con otro.
        """
        dep = self.get_departamento(departamento_id)
        nombre_norm = normalize_nombre(nuevo_nombre, "nombre")
        if nombre_norm == dep.nombre:
            return  # Nombre idéntico — no-op, no se audita.
        existente = self._dep_read.get_by_nombre(nombre_norm)
        if existente is not None and existente.id != departamento_id:
            raise DuplicateNombreError(_CAT_DEPARTAMENTO, nombre_norm)
        self._dep_write.rename(departamento_id, nombre_norm)
        self._audit.log(
            action="departamento_renamed",
            user_id=actor_user_id,
            details=(
                f'{{"id": {departamento_id}, ' f'"old": "{dep.nombre}", "new": "{nombre_norm}"}}'
            ),
        )
        self._log.info(
            "Departamento renombrado: id=%s %s -> %s",
            departamento_id,
            dep.nombre,
            nombre_norm,
        )

    def archive_departamento(self, departamento_id: int, actor_user_id: int) -> None:
        """Archiva un departamento.

        Raises:
            CatalogoNotFoundError: ``departamento_id`` no existe.
            CatalogoInUseError: Hay empleados activos asignados.
        """
        dep = self.get_departamento(departamento_id)
        if not dep.is_active:
            return  # Ya archivado — no-op idempotente.
        empleados = self._empleado_read.list_by_departamento(departamento_id, solo_activos=True)
        if empleados:
            raise CatalogoInUseError(_CAT_DEPARTAMENTO, departamento_id, len(empleados))
        self._dep_write.archive(departamento_id)
        self._audit.log(
            action="departamento_archived",
            user_id=actor_user_id,
            details=f'{{"id": {departamento_id}}}',
        )
        self._log.info("Departamento archivado: id=%s", departamento_id)

    def archive_departamento_moviendo(
        self,
        origen_id: int,
        destino_id: int,
        actor_user_id: int,
    ) -> ResultadoMovimiento:
        """Pasa los empleados activos y los cargos propios a otro departamento y archiva.

        Sirve para cerrar o unir departamentos sin editar empleado por
        empleado. Todo ocurre en una sola transacción.

        Raises:
            CatalogoNotFoundError: Alguno de los dos ids no existe.
            DestinoInvalidoError: El destino es el mismo o está archivado.
        """
        origen = self.get_departamento(origen_id)
        destino = self.get_departamento(destino_id)
        if origen_id == destino_id:
            raise DestinoInvalidoError(f"Elija un departamento distinto de «{origen.nombre}».")
        if not destino.is_active:
            raise DestinoInvalidoError(
                f"«{destino.nombre}» está archivado: elija un departamento activo."
            )
        if not origen.is_active:
            return ResultadoMovimiento(empleados=0)
        empleados, cargos = self._dep_write.archive_moviendo(origen_id, destino_id)
        self._audit.log(
            action="departamento_merged",
            user_id=actor_user_id,
            details=(
                f'{{"id": {origen_id}, "destino_id": {destino_id}, '
                f'"empleados": {empleados}, "cargos": {cargos}}}'
            ),
        )
        self._log.info(
            "Departamento archivado moviendo: id=%s -> %s (%s empleados, %s cargos)",
            origen_id,
            destino_id,
            empleados,
            cargos,
        )
        return ResultadoMovimiento(empleados=empleados, cargos=cargos)

    def unarchive_departamento(self, departamento_id: int, actor_user_id: int) -> None:
        """Reactiva un departamento archivado.

        Raises:
            CatalogoNotFoundError: ``departamento_id`` no existe.
        """
        dep = self.get_departamento(departamento_id)
        if dep.is_active:
            return  # Ya activo — no-op idempotente.
        self._dep_write.unarchive(departamento_id)
        self._audit.log(
            action="departamento_unarchived",
            user_id=actor_user_id,
            details=f'{{"id": {departamento_id}}}',
        )
        self._log.info("Departamento reactivado: id=%s", departamento_id)

    # ── Cargos ────────────────────────────────────────────────────────────

    def list_cargos(self, solo_activos: bool = True) -> List[Cargo]:
        """Devuelve la lista de cargos (activos por default)."""
        if solo_activos:
            return self._cargo_read.list_active()
        return self._cargo_read.list_all()

    def list_cargos_para_departamento(self, departamento_id: int) -> List[Cargo]:
        """Sub-3.2.A: cargos disponibles para un departamento específico.

        Combina cargos globales + cargos asignados a ese departamento.
        Útil para poblar el dropdown de cargo en el formulario de
        empleados según el departamento elegido.

        Raises:
            CatalogoNotFoundError: ``departamento_id`` no existe.
        """
        # Validamos que el departamento exista para evitar dropdowns
        # silenciosamente vacíos por bugs del caller.
        self.get_departamento(departamento_id)
        return self._cargo_read.list_active_para_departamento(departamento_id)

    def get_cargo(self, cargo_id: int) -> Cargo:
        """Devuelve un cargo por id.

        Raises:
            CatalogoNotFoundError: Si no existe el id.
        """
        cargo = self._cargo_read.get_by_id(cargo_id)
        if cargo is None:
            raise CatalogoNotFoundError(_CAT_CARGO, cargo_id)
        return cargo

    def create_cargo(
        self,
        nombre: str,
        actor_user_id: int,
        departamento_id: Optional[int] = None,
    ) -> Cargo:
        """Crea un cargo nuevo.

        Args:
            nombre: Texto único después de normalizar.
            actor_user_id: Usuario que ejecuta (audit_log).
            departamento_id: Si se pasa, el cargo queda restringido a
                ese departamento (debe existir y estar activo). Si es
                ``None`` el cargo es global.

        Raises:
            MissingRequiredFieldError: ``nombre`` vacío tras normalizar.
            DuplicateNombreError: Ya existe un cargo con ese nombre.
            CatalogoNotFoundError: ``departamento_id`` no existe.
        """
        nombre_norm = normalize_nombre(nombre, "nombre")
        if self._cargo_read.get_by_nombre(nombre_norm) is not None:
            raise DuplicateNombreError(_CAT_CARGO, nombre_norm)
        if departamento_id is not None:
            # Reusa la validación de existencia (lanza CatalogoNotFoundError).
            self.get_departamento(departamento_id)
        creado = self._cargo_write.create(
            Cargo(id=None, nombre=nombre_norm, departamento_id=departamento_id)
        )
        assert creado.id is not None
        self._audit.log(
            action="cargo_created",
            user_id=actor_user_id,
            details=(
                f'{{"id": {creado.id}, "nombre": "{nombre_norm}", '
                f'"departamento_id": {departamento_id if departamento_id is not None else "null"}}}'
            ),
        )
        self._log.info(
            "Cargo creado: id=%s nombre=%s depto=%s",
            creado.id,
            nombre_norm,
            departamento_id,
        )
        return creado

    def set_cargo_departamento(
        self,
        cargo_id: int,
        departamento_id: Optional[int],
        actor_user_id: int,
    ) -> None:
        """Sub-3.2.A: cambia (o desasigna con ``None``) el departamento de un cargo.

        Raises:
            CatalogoNotFoundError: ``cargo_id`` o ``departamento_id`` no existen.
            CargoUsadoEnOtrosDepartamentosError: Al dejarlo solo para un
                departamento, hay empleados activos de otros que lo usan.
        """
        cargo = self.get_cargo(cargo_id)
        destino = self.get_departamento(departamento_id) if departamento_id is not None else None
        if cargo.departamento_id == departamento_id:
            return
        if destino is not None:
            otros = [
                e
                for e in self._empleado_read.list_by_cargo(cargo_id, solo_activos=True)
                if e.departamento_id != departamento_id
            ]
            if otros:
                raise CargoUsadoEnOtrosDepartamentosError(
                    cargo.nombre,
                    destino.nombre,
                    len(otros),
                    self._nombres_departamentos({e.departamento_id for e in otros}),
                )
        self._cargo_write.update_departamento(cargo_id, departamento_id)
        self._audit.log(
            action="cargo_depto_changed",
            user_id=actor_user_id,
            details=(
                f'{{"id": {cargo_id}, '
                f'"old": {cargo.departamento_id if cargo.departamento_id is not None else "null"}, '
                f'"new": {departamento_id if departamento_id is not None else "null"}}}'
            ),
        )
        self._log.info(
            "Cargo depto actualizado: id=%s %s -> %s",
            cargo_id,
            cargo.departamento_id,
            departamento_id,
        )

    def rename_cargo(
        self,
        cargo_id: int,
        nuevo_nombre: str,
        actor_user_id: int,
    ) -> None:
        """Renombra un cargo existente.

        Raises:
            CatalogoNotFoundError: ``cargo_id`` no existe.
            DuplicateNombreError: El nuevo nombre colisiona con otro.
        """
        cargo = self.get_cargo(cargo_id)
        nombre_norm = normalize_nombre(nuevo_nombre, "nombre")
        if nombre_norm == cargo.nombre:
            return
        existente = self._cargo_read.get_by_nombre(nombre_norm)
        if existente is not None and existente.id != cargo_id:
            raise DuplicateNombreError(_CAT_CARGO, nombre_norm)
        self._cargo_write.rename(cargo_id, nombre_norm)
        self._audit.log(
            action="cargo_renamed",
            user_id=actor_user_id,
            details=(f'{{"id": {cargo_id}, ' f'"old": "{cargo.nombre}", "new": "{nombre_norm}"}}'),
        )
        self._log.info(
            "Cargo renombrado: id=%s %s -> %s",
            cargo_id,
            cargo.nombre,
            nombre_norm,
        )

    def archive_cargo(self, cargo_id: int, actor_user_id: int) -> None:
        """Archiva un cargo.

        Raises:
            CatalogoNotFoundError: ``cargo_id`` no existe.
            CatalogoInUseError: Hay empleados activos asignados.
        """
        cargo = self.get_cargo(cargo_id)
        if not cargo.is_active:
            return
        empleados = self._empleado_read.list_by_cargo(cargo_id, solo_activos=True)
        if empleados:
            raise CatalogoInUseError(_CAT_CARGO, cargo_id, len(empleados))
        self._cargo_write.archive(cargo_id)
        self._audit.log(
            action="cargo_archived",
            user_id=actor_user_id,
            details=f'{{"id": {cargo_id}}}',
        )
        self._log.info("Cargo archivado: id=%s", cargo_id)

    def cargos_destino_para(self, cargo_id: int) -> List[Cargo]:
        """Cargos activos a los que pueden pasar TODOS los empleados de ``cargo_id``.

        Los de cualquier departamento siempre sirven; los propios de un
        departamento solo si todos sus empleados activos son de ahí.
        """
        self.get_cargo(cargo_id)
        deptos = {
            e.departamento_id
            for e in self._empleado_read.list_by_cargo(cargo_id, solo_activos=True)
        }
        return [
            c
            for c in self._cargo_read.list_active()
            if c.id != cargo_id and (c.departamento_id is None or deptos <= {c.departamento_id})
        ]

    def archive_cargo_moviendo(
        self,
        origen_id: int,
        destino_id: int,
        actor_user_id: int,
    ) -> ResultadoMovimiento:
        """Pasa los empleados activos del cargo a otro y lo archiva (una transacción).

        Raises:
            CatalogoNotFoundError: Alguno de los dos ids no existe.
            DestinoInvalidoError: El destino es el mismo o está archivado.
            CargoIncompatibleError: El destino es propio de un departamento
                y hay empleados de otros.
        """
        origen = self.get_cargo(origen_id)
        destino = self.get_cargo(destino_id)
        if origen_id == destino_id:
            raise DestinoInvalidoError(f"Elija un cargo distinto de «{origen.nombre}».")
        if not destino.is_active:
            raise DestinoInvalidoError(f"«{destino.nombre}» está archivado: elija un cargo activo.")
        if not origen.is_active:
            return ResultadoMovimiento(empleados=0)
        if destino.departamento_id is not None:
            otros = [
                e
                for e in self._empleado_read.list_by_cargo(origen_id, solo_activos=True)
                if e.departamento_id != destino.departamento_id
            ]
            if otros:
                raise CargoIncompatibleError(
                    destino.nombre,
                    self.get_departamento(destino.departamento_id).nombre,
                    len(otros),
                    self._nombres_departamentos({e.departamento_id for e in otros}),
                )
        empleados = self._cargo_write.archive_moviendo(origen_id, destino_id)
        self._audit.log(
            action="cargo_merged",
            user_id=actor_user_id,
            details=(
                f'{{"id": {origen_id}, "destino_id": {destino_id}, "empleados": {empleados}}}'
            ),
        )
        self._log.info(
            "Cargo archivado moviendo: id=%s -> %s (%s empleados)",
            origen_id,
            destino_id,
            empleados,
        )
        return ResultadoMovimiento(empleados=empleados)

    def unarchive_cargo(self, cargo_id: int, actor_user_id: int) -> None:
        """Reactiva un cargo archivado.

        Raises:
            CatalogoNotFoundError: ``cargo_id`` no existe.
        """
        cargo = self.get_cargo(cargo_id)
        if cargo.is_active:
            return
        self._cargo_write.unarchive(cargo_id)
        self._audit.log(
            action="cargo_unarchived",
            user_id=actor_user_id,
            details=f'{{"id": {cargo_id}}}',
        )
        self._log.info("Cargo reactivado: id=%s", cargo_id)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _nombres_departamentos(self, ids: Set[int]) -> List[str]:
        nombres = []
        for dep_id in ids:
            dep = self._dep_read.get_by_id(dep_id)
            nombres.append(dep.nombre if dep is not None else f"#{dep_id}")
        return sorted(nombres, key=str.casefold)
