"""Diagnóstico de un reloj ZKTeco: "¿responde?, ¿qué hora tiene?, ¿quién está?".

Lo usa el botón "Probar conexión" de Configuración → Relojes. Conecta,
lee la información del reloj y la cruza con los empleados activos:

    - Desfase de la hora del reloj contra la de esta computadora (un
      reloj atrasado corre todas las marcas y genera tardanzas falsas).
    - Usuarios del reloj cuyo número no corresponde a ningún empleado
      activo (gente que se fue o números mal escritos).
    - Empleados activos que no pueden marcar en este reloj: sin número
      asignado, o con un número que el reloj no tiene registrado.

También permite poner el reloj en hora con la de esta computadora.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Final, Optional, Tuple

from core.models.dispositivo import Dispositivo
from core.models.empleado import Empleado
from core.models.info_reloj import InfoReloj, UsuarioReloj
from core.repositories.dispositivo_repository import IDispositivoReadRepository
from core.repositories.empleado_repository import IEmpleadoReadRepository
from core.services.audit_logger import AuditLogger
from core.services.errors import DispositivoNotFoundError, RelojNoRespondeError
from infrastructure.zkteco.exceptions import ZKAdapterError
from infrastructure.zkteco.interface import IZKTecoAdapter

# Más de un minuto de diferencia ya se nota en las tardanzas.
DESFASE_TOLERADO_SEGUNDOS: Final[int] = 60
# A partir de aquí conviene avisar que la memoria de marcas se está llenando.
MEMORIA_CASI_LLENA: Final[float] = 0.9


@dataclass(frozen=True)
class DiagnosticoReloj:
    """Resultado de probar la conexión con un reloj."""

    dispositivo: Dispositivo
    info: InfoReloj
    probado_en: datetime
    desfase_segundos: Optional[int]
    usuarios_sin_empleado: Tuple[UsuarioReloj, ...]
    empleados_sin_id: Tuple[Empleado, ...]
    empleados_no_registrados: Tuple[Empleado, ...]

    @property
    def hora_desfasada(self) -> bool:
        """``True`` si la hora del reloj difiere más de un minuto de la de la PC."""
        return (
            self.desfase_segundos is not None
            and abs(self.desfase_segundos) > DESFASE_TOLERADO_SEGUNDOS
        )

    @property
    def uso_memoria(self) -> Optional[float]:
        """Fracción de la memoria de marcas ocupada (``None`` si el reloj no la informa)."""
        if self.info.capacidad_marcas <= 0:
            return None
        return self.info.marcas / self.info.capacidad_marcas

    @property
    def memoria_casi_llena(self) -> bool:
        uso = self.uso_memoria
        return uso is not None and uso >= MEMORIA_CASI_LLENA


class DiagnosticoRelojService:
    """Prueba la conexión con un reloj y lo pone en hora."""

    def __init__(
        self,
        adapter: IZKTecoAdapter,
        dispositivo_read: IDispositivoReadRepository,
        empleado_read: IEmpleadoReadRepository,
        audit_logger: AuditLogger,
        ahora: Callable[[], datetime] = datetime.now,
    ) -> None:
        """Inicializa el servicio.

        Args:
            adapter: Cliente del reloj (real o de prueba).
            dispositivo_read: Para buscar el reloj por id.
            empleado_read: Para cruzar los usuarios del reloj con los empleados.
            audit_logger: Registra cuando alguien cambia la hora del reloj.
            ahora: Hora local de esta computadora (inyectable para tests).
                El reloj trabaja en hora local, sin zona horaria.
        """
        self._adapter = adapter
        self._dispositivo_read = dispositivo_read
        self._empleado_read = empleado_read
        self._audit = audit_logger
        self._ahora = ahora
        self._log = logging.getLogger(self.__class__.__name__)

    def probar(self, dispositivo_id: int) -> DiagnosticoReloj:
        """Conecta con el reloj y arma el diagnóstico.

        Raises:
            DispositivoNotFoundError: El id no existe.
            RelojNoRespondeError: No se pudo hablar con el reloj.
        """
        dispositivo = self._dispositivo(dispositivo_id)
        try:
            info = self._adapter.leer_info(dispositivo)
        except ZKAdapterError as exc:
            self._log.warning("Reloj %s no responde: %s", dispositivo_id, exc)
            raise RelojNoRespondeError(str(exc)) from exc
        probado_en = self._ahora()
        desfase = round((info.hora - probado_en).total_seconds()) if info.hora is not None else None

        empleados = self._empleado_read.list_active()
        ids_empleados = {e.zkteco_id for e in empleados if e.zkteco_id is not None}
        numeros_reloj = {u.numero for u in info.usuarios if u.numero is not None}
        return DiagnosticoReloj(
            dispositivo=dispositivo,
            info=info,
            probado_en=probado_en,
            desfase_segundos=desfase,
            usuarios_sin_empleado=tuple(
                sorted(
                    (u for u in info.usuarios if u.numero not in ids_empleados),
                    key=lambda u: (u.numero is None, u.numero or 0, u.user_id),
                )
            ),
            empleados_sin_id=tuple(e for e in empleados if e.zkteco_id is None),
            empleados_no_registrados=tuple(
                e for e in empleados if e.zkteco_id is not None and e.zkteco_id not in numeros_reloj
            ),
        )

    def poner_en_hora(self, dispositivo_id: int, actor_user_id: int) -> datetime:
        """Pone el reloj con la fecha y hora de esta computadora. Devuelve la hora puesta.

        Raises:
            DispositivoNotFoundError: El id no existe.
            RelojNoRespondeError: No se pudo hablar con el reloj.
        """
        dispositivo = self._dispositivo(dispositivo_id)
        momento = self._ahora().replace(microsecond=0)
        try:
            self._adapter.poner_en_hora(dispositivo, momento)
        except ZKAdapterError as exc:
            raise RelojNoRespondeError(str(exc)) from exc
        self._audit.log(
            action="dispositivo_hora_ajustada",
            user_id=actor_user_id,
            details=f'{{"id": {dispositivo_id}, "hora": "{momento.isoformat()}"}}',
        )
        self._log.info("Reloj %s puesto en hora: %s", dispositivo_id, momento)
        return momento

    def _dispositivo(self, dispositivo_id: int) -> Dispositivo:
        dispositivo = self._dispositivo_read.get_by_id(dispositivo_id)
        if dispositivo is None:
            raise DispositivoNotFoundError(dispositivo_id)
        return dispositivo
