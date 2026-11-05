"""Excepciones de dominio de la capa de servicios.

Se definen aquí para que los controladores de UI puedan capturarlas
por tipo específico (regla del proyecto: "nunca capturar Exception
genérica").

Los mensajes asociados son en español y pensados para ser mostrados
al usuario final. Los mensajes de login son genéricos por seguridad
(nunca revelar si el usuario o la contraseña es lo incorrecto).
"""

from __future__ import annotations

from typing import List, Optional, Sequence


class AuthError(Exception):
    """Clase base para errores de autenticación."""


class InvalidCredentialsError(AuthError):
    """Credenciales inválidas (usuario o contraseña).

    El mensaje es intencionalmente ambiguo: no distingue entre
    "usuario no existe" y "password incorrecta". Esto evita que un
    atacante pueda enumerar usuarios válidos probando logins.
    """

    def __init__(self, mensaje: str = "Usuario o contraseña incorrectos.") -> None:
        super().__init__(mensaje)


class AccountLockedError(AuthError):
    """Cuenta bloqueada por demasiados intentos fallidos.

    Attributes:
        locked_until: ISO-8601 UTC hasta cuando la cuenta permanece
            bloqueada. El caller puede usarlo para calcular cuánto
            falta y mostrarlo al usuario.
    """

    def __init__(self, locked_until: str) -> None:
        super().__init__(
            f"La cuenta está bloqueada temporalmente. "
            f"Vuelva a intentar después de {locked_until}."
        )
        self.locked_until = locked_until


class AccountInactiveError(AuthError):
    """El usuario existe y la password es correcta, pero ``is_active=False``."""

    def __init__(self) -> None:
        super().__init__("La cuenta está desactivada. Contacte al administrador.")


class PermissionDeniedError(Exception):
    """El usuario autenticado no tiene permiso para la acción solicitada.

    Attributes:
        permission: Código del permiso que se intentó verificar.
    """

    def __init__(self, permission: str) -> None:
        super().__init__(f"No tiene permisos para realizar esta acción ({permission}).")
        self.permission = permission


class NotAuthenticatedError(Exception):
    """Se intentó verificar un permiso sin una sesión activa."""

    def __init__(self) -> None:
        super().__init__("Debe iniciar sesión para continuar.")


# ── Errores del Setup Wizard ──────────────────────────────────────────────────


class SetupError(Exception):
    """Clase base para errores del Setup Wizard."""


class SetupAlreadyCompletedError(SetupError):
    """Se intentó ejecutar el wizard cuando ya existe un SUPERADMIN.

    El wizard queda deshabilitado de forma derivada: mientras
    ``count(SUPERADMIN) > 0``, cualquier intento de ``create_superadmin``
    lanza este error. No hay bandera persistida que un atacante pueda
    manipular — la única fuente de verdad es el conteo de usuarios.
    """

    def __init__(self) -> None:
        super().__init__(
            "El sistema ya tiene un SUPERADMIN configurado. "
            "El asistente de configuración ya no está disponible."
        )


class WeakPasswordError(SetupError):
    """La password no cumple la política mínima del proyecto.

    Attributes:
        reason: Descripción en español de qué regla no se cumplió.
            Es seguro mostrarla al usuario — el wizard es un flujo
            local, no expuesto por red, así que no aplican
            consideraciones de enumeración.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class DuplicateUsernameError(SetupError):
    """El username ya existe en la tabla ``usuarios``.

    Attributes:
        username: El nombre que colisionó. Se expone porque el caller
            (CLI local) ya conoce el valor — lo acaba de tipear.
    """

    def __init__(self, username: str) -> None:
        super().__init__(f"El usuario '{username}' ya existe.")
        self.username = username


# ── Errores de validación genéricos (Fase 2) ──────────────────────────────────


class ValidationError(Exception):
    """Clase base para errores de validación de entradas en servicios Fase 2.

    Los servicios de dominio levantan subclases específicas antes de
    tocar el repositorio. Los mensajes son en español y seguros para
    mostrar al usuario final.
    """


class InvalidDNIError(ValidationError):
    """El DNI no cumple el formato hondureño ``XXXX-XXXX-XXXXX``."""

    def __init__(self, dni: str) -> None:
        super().__init__(
            f"El DNI '{dni}' no tiene el formato esperado (XXXX-XXXX-XXXXX, "
            "solo dígitos y guiones)."
        )
        self.dni = dni


class InvalidDateError(ValidationError):
    """Una fecha ISO (``YYYY-MM-DD``) no es parseable o no existe."""

    def __init__(self, valor: str, campo: str) -> None:
        super().__init__(f"La fecha «{valor}» no es válida. Use el formato dd/mm/aaaa.")
        self.valor = valor
        self.campo = campo


class InvalidTimeError(ValidationError):
    """Una hora 24h (``HH:MM``) no es parseable."""

    def __init__(self, valor: str, campo: str) -> None:
        super().__init__(
            f"La hora '{valor}' en el campo '{campo}' no es válida. "
            "Use el formato HH:MM (24 horas)."
        )
        self.valor = valor
        self.campo = campo


class InvalidBitmaskError(ValidationError):
    """El bitmask de días de la semana no está en el rango 0..127."""

    def __init__(self, valor: int) -> None:
        super().__init__(
            f"El bitmask de días '{valor}' está fuera del rango válido "
            "(0 a 127). Debe seleccionar al menos un día."
        )
        self.valor = valor


class InvalidMotivoBajaError(ValidationError):
    """El motivo de baja no está en el catálogo cerrado ``MotivoBaja``."""

    def __init__(self, valor: str) -> None:
        super().__init__(
            f"El motivo de baja '{valor}' no es válido. "
            "Debe ser uno de los motivos predefinidos."
        )
        self.valor = valor


class MissingRequiredFieldError(ValidationError):
    """Un campo obligatorio vino vacío o en ``None``."""

    def __init__(self, campo: str) -> None:
        super().__init__(f"Falta {_NOMBRE_CAMPO.get(campo, campo)}.")
        self.campo = campo


# ── Errores del catálogo (departamentos / cargos) ─────────────────────────────


class CatalogoError(Exception):
    """Clase base para errores de CatalogoService."""


class DuplicateNombreError(CatalogoError):
    """Ya existe una entrada con ese nombre en el catálogo."""

    def __init__(self, catalogo: str, nombre: str) -> None:
        super().__init__(f"Ya existe un {catalogo} llamado «{nombre}».")
        self.catalogo = catalogo
        self.nombre = nombre


class CatalogoNotFoundError(CatalogoError):
    """No existe una entrada con ese id en el catálogo."""

    def __init__(self, catalogo: str, id_: int) -> None:
        super().__init__(f"No se encontró el '{catalogo}' con id {id_}.")
        self.catalogo = catalogo
        self.id_ = id_


class CatalogoInUseError(CatalogoError):
    """Se intentó archivar un catálogo que aún tiene empleados activos."""

    def __init__(self, catalogo: str, id_: int, empleados_activos: int) -> None:
        quien = (
            "1 empleado activo"
            if empleados_activos == 1
            else f"{empleados_activos} empleados activos"
        )
        super().__init__(
            f"No se puede archivar: el {catalogo} tiene {quien}. "
            "Cámbielos a otro antes de archivarlo."
        )
        self.catalogo = catalogo
        self.id_ = id_
        self.empleados_activos = empleados_activos


def lista_natural(nombres: Sequence[str]) -> str:
    """``["A"]`` → ``A``; ``["A", "B", "C"]`` → ``A, B y C``."""
    if len(nombres) <= 1:
        return "".join(nombres)
    return ", ".join(nombres[:-1]) + " y " + nombres[-1]


class DestinoInvalidoError(CatalogoError):
    """El destino elegido para pasar el personal no sirve (es el mismo o está archivado)."""


class CargoUsadoEnOtrosDepartamentosError(CatalogoError):
    """Se quiso dejar un cargo solo para un departamento, pero lo usan empleados de otros."""

    def __init__(self, cargo: str, departamento: str, empleados: int, otros: Sequence[str]) -> None:
        quien = "lo tiene 1 empleado" if empleados == 1 else f"lo tienen {empleados} empleados"
        cambie = "Cámbielo" if empleados == 1 else "Cámbielos"
        super().__init__(
            f"No se puede dejar «{cargo}» solo para {departamento}: {quien} de "
            f"{lista_natural(otros)}. {cambie} de cargo primero o deje el cargo para "
            "cualquier departamento."
        )
        self.empleados = empleados


class CargoIncompatibleError(CatalogoError):
    """El cargo destino es de un departamento y hay empleados de otros."""

    def __init__(self, cargo: str, departamento: str, empleados: int, otros: Sequence[str]) -> None:
        quien = "1 empleado es" if empleados == 1 else f"{empleados} empleados son"
        super().__init__(
            f"«{cargo}» es solo para {departamento}, pero {quien} de {lista_natural(otros)}. "
            "Elija un cargo de cualquier departamento."
        )
        self.empleados = empleados


# ── Errores de turnos ─────────────────────────────────────────────────────────


class TurnoError(Exception):
    """Clase base para errores de TurnoService."""


class DuplicateTurnoNombreError(TurnoError):
    """Ya existe un turno con ese nombre."""

    def __init__(self, nombre: str) -> None:
        super().__init__(f"Ya existe un turno con el nombre '{nombre}'.")
        self.nombre = nombre


class TurnoNotFoundError(TurnoError):
    """No existe un turno con ese id."""

    def __init__(self, turno_id: int) -> None:
        super().__init__(f"No se encontró el turno con id {turno_id}.")
        self.turno_id = turno_id


class TurnoInactiveError(TurnoError):
    """Se intentó asignar un turno que está archivado."""

    def __init__(self, turno_id: int) -> None:
        super().__init__(f"El turno con id {turno_id} está archivado y no puede asignarse.")
        self.turno_id = turno_id


class InvalidDescansoError(TurnoError):
    """Los minutos de descanso son mayores o iguales a la duración del turno."""

    def __init__(self, minutos_descanso: int, duracion_minutos: int) -> None:
        super().__init__(
            f"Los {minutos_descanso} minutos de descanso exceden la "
            f"duración del turno ({duracion_minutos} minutos)."
        )
        self.minutos_descanso = minutos_descanso
        self.duracion_minutos = duracion_minutos


class InvalidAlmuerzoError(TurnoError):
    """La hora de almuerzo está incompleta, al revés o fuera del horario."""

    def __init__(self, mensaje: str) -> None:
        super().__init__(mensaje)


class HorariosSolapadosError(TurnoError):
    """Un mismo día de la semana aparece en dos horarios del turno."""

    def __init__(self, dias: List[str]) -> None:
        if len(dias) == 1:
            texto = f"El {dias[0]} está en dos horarios del turno."
        else:
            texto = f"Los días {_enumerar(dias)} están en dos horarios del turno."
        super().__init__(f"{texto} Cada día solo puede tener un horario.")
        self.dias = dias


# ── Errores de empleados ──────────────────────────────────────────────────────


class EmpleadoError(Exception):
    """Clase base para errores de EmpleadoService."""


class DuplicateDNIError(EmpleadoError):
    """Ya existe un empleado con ese DNI."""

    def __init__(self, dni: str) -> None:
        super().__init__(f"Ya existe un empleado con el DNI '{dni}'.")
        self.dni = dni


class DuplicateZktecoIdError(EmpleadoError):
    """Ya existe un empleado con ese ``zkteco_id``."""

    def __init__(self, zkteco_id: int) -> None:
        super().__init__(f"Ya existe un empleado con el ZKTeco ID {zkteco_id}.")
        self.zkteco_id = zkteco_id


class EmpleadoNotFoundError(EmpleadoError):
    """No existe un empleado con ese id."""

    def __init__(self, empleado_id: int) -> None:
        super().__init__(f"No se encontró el empleado con id {empleado_id}.")
        self.empleado_id = empleado_id


class EmpleadoAlreadyInactiveError(EmpleadoError):
    """Se intentó desactivar un empleado que ya está archivado."""

    def __init__(self, empleado_id: int) -> None:
        super().__init__(f"El empleado con id {empleado_id} ya está archivado.")
        self.empleado_id = empleado_id


class EmpleadoAlreadyActiveError(EmpleadoError):
    """Se intentó reactivar un empleado que ya está activo."""

    def __init__(self, empleado_id: int) -> None:
        super().__init__(f"El empleado con id {empleado_id} ya está activo.")
        self.empleado_id = empleado_id


class SinTurnoVigenteError(EmpleadoError):
    """Se intentó cambiar el turno de un empleado que no tiene vigente."""

    def __init__(self, empleado_id: int) -> None:
        super().__init__(
            f"El empleado con id {empleado_id} no tiene asignación de turno "
            "vigente. Use asignar_turno() en vez de cambiar_turno()."
        )
        self.empleado_id = empleado_id


class TurnoYaAsignadoError(EmpleadoError):
    """Se intentó asignar un turno cuando el empleado ya tiene uno vigente."""

    def __init__(self, empleado_id: int) -> None:
        super().__init__(
            f"El empleado con id {empleado_id} ya tiene un turno vigente. "
            "Use cambiar_turno() para reemplazarlo."
        )
        self.empleado_id = empleado_id


class TurnoBitmaskSolapadoError(EmpleadoError):
    """Sub-3.2.B: el bitmask del turno nuevo se solapa con un vigente.

    Ocurre cuando el empleado intenta sumar un segundo turno paralelo
    (ej. uno para sábado) pero los días que cubre se solapan con un
    turno ya vigente. La regla es: el conjunto de días cubiertos por
    todas las asignaciones vigentes debe ser disjunto (cada día de la
    semana lo cubre a lo sumo un turno).
    """

    def __init__(self, empleado_id: int, turno_id_existente: int) -> None:
        super().__init__(
            f"El empleado {empleado_id} ya tiene una asignación vigente "
            f"(turno_id={turno_id_existente}) cuyos días de la semana se "
            "solapan con los del nuevo turno. Cerrá la vigente o usá un "
            "turno con bitmask de días disjunto."
        )
        self.empleado_id = empleado_id
        self.turno_id_existente = turno_id_existente


class TurnoConflictoEmpleadosError(EmpleadoError):
    """Cambiar los días del turno haría que algunos empleados tengan dos turnos el mismo día.

    Pasa, por ejemplo, al agregar el sábado a "Administrativo" cuando sus
    empleados ya tienen asignado aparte "Administrativo Sábado". La UI
    ofrece quitarles el turno que choca y volver a guardar.

    Attributes:
        empleados: Nombres de los empleados afectados.
        turnos: Nombres de los otros turnos que chocan (sin repetir).
        dias: Días (en minúscula) donde ocurre el choque.
    """

    def __init__(self, empleados: List[str], turnos: List[str], dias: List[str]) -> None:
        cantidad = len(empleados)
        quien = "1 empleado ya tiene" if cantidad == 1 else f"{cantidad} empleados ya tienen"
        otros = _enumerar([f"«{t}»" for t in turnos])
        muestra = _enumerar(empleados[:3] + ([f"{cantidad - 3} más"] if cantidad > 3 else []))
        super().__init__(
            f"{quien} otro turno el {_enumerar(dias)} ({otros}): {muestra}. "
            "Un empleado no puede tener dos turnos el mismo día."
        )
        self.empleados = empleados
        self.turnos = turnos
        self.dias = dias


# ── Errores de sincronización (Fase 3) ────────────────────────────────────────


class SincronizacionError(Exception):
    """Clase base para errores de ``SincronizacionService``."""


class DispositivoNotFoundError(SincronizacionError):
    """No existe un dispositivo con ese id."""

    def __init__(self, dispositivo_id: int) -> None:
        super().__init__(f"No se encontró el dispositivo con id {dispositivo_id}.")
        self.dispositivo_id = dispositivo_id


class RelojNoRespondeError(SincronizacionError):
    """No se pudo hablar con el reloj (apagado, otra IP, red, firmware)."""


class DispositivoInactiveError(SincronizacionError):
    """Se intentó sincronizar un dispositivo archivado."""

    def __init__(self, dispositivo_id: int) -> None:
        super().__init__(
            f"El dispositivo con id {dispositivo_id} está archivado y no "
            "puede sincronizarse. Reactívelo primero."
        )
        self.dispositivo_id = dispositivo_id


class InvalidRangoError(SincronizacionError):
    """El rango de sincronización es inválido (``desde > hasta``)."""

    def __init__(self, desde: str, hasta: str) -> None:
        super().__init__(
            f"El rango solicitado es inválido: desde={desde} es posterior a hasta={hasta}."
        )
        self.desde = desde
        self.hasta = hasta


# ── Errores de configuración de dispositivos (Plan B / Sub-2.4b) ──────────────


class InvalidIPError(ValidationError):
    """La dirección IP no tiene formato IPv4 válido.

    Se valida con ``ipaddress.IPv4Address``: rechaza IPv6, notación octal,
    octetos fuera de 0..255 y cualquier formato no canónico. El mensaje
    es seguro para mostrar al usuario.

    Attributes:
        ip: Valor recibido (sin trim — para reportar exactamente lo que
            tipeó el operador).
    """

    def __init__(self, ip: str) -> None:
        super().__init__(
            f"La dirección IP «{ip}» no es válida. Escríbala como en el reloj, "
            "por ejemplo 192.168.1.201."
        )
        self.ip = ip


class InvalidPuertoError(ValidationError):
    """El puerto TCP está fuera del rango válido 1..65535."""

    def __init__(self, puerto: int) -> None:
        super().__init__(
            f"El puerto {puerto} no es válido: debe ser un número entre 1 y 65535 "
            "(casi siempre 4370)."
        )
        self.puerto = puerto


class DuplicateDispositivoNombreError(SincronizacionError):
    """Ya existe un dispositivo con ese nombre."""

    def __init__(self, nombre: str) -> None:
        super().__init__(f"Ya existe un reloj llamado «{nombre}».")
        self.nombre = nombre


class DuplicateNumeroSerieError(SincronizacionError):
    """Ya hay otro reloj con ese número de serie."""

    def __init__(self, numero_serie: str, nombre: str) -> None:
        super().__init__(f"El número de serie {numero_serie} ya es del reloj «{nombre}».")
        self.numero_serie = numero_serie


class InvalidClaveComunicacionError(ValidationError):
    """La clave de comunicación (Comm Key) no es un número de 0 a 999999."""

    def __init__(self, valor: object) -> None:
        super().__init__(
            f"La clave de comunicación «{valor}» no es válida: debe ser un número de 0 a "
            "999999 (0 si el reloj no tiene clave)."
        )


class DuplicateDispositivoEndpointError(SincronizacionError):
    """Ya existe un dispositivo registrado en ese ``(ip, puerto)``."""

    def __init__(self, ip: str, puerto: int) -> None:
        super().__init__(
            f"Ya hay un reloj registrado en {ip}:{puerto}. "
            "Un mismo reloj no se puede registrar dos veces."
        )
        self.ip = ip
        self.puerto = puerto


# ── Errores de administración de usuarios (Sub-2.7a) ──────────────────────────


class UsuarioAdminError(Exception):
    """Clase base para errores del módulo de administración de usuarios."""


class UsuarioNotFoundError(UsuarioAdminError):
    """No existe un usuario con ese id."""

    def __init__(self, user_id: int) -> None:
        super().__init__(f"No se encontró el usuario con id {user_id}.")
        self.user_id = user_id


class InvalidUsernameError(UsuarioAdminError):
    """El username no cumple el formato (3-32 chars, solo alfanumérico/.-_)."""

    def __init__(self, username: str) -> None:
        super().__init__(
            f"El nombre de usuario '{username}' no es válido. "
            "Debe tener entre 3 y 32 caracteres y solo letras, números, "
            "puntos, guiones o guiones bajos."
        )
        self.username = username


class CannotDeactivateSelfError(UsuarioAdminError):
    """Un usuario intentó desactivarse a sí mismo."""

    def __init__(self) -> None:
        super().__init__(
            "No puede desactivar su propia cuenta. " "Otra persona con permisos debe hacerlo."
        )


class CannotDeactivateOnlySuperadminError(UsuarioAdminError):
    """Se intentó desactivar al único SUPERADMIN del sistema."""

    def __init__(self) -> None:
        super().__init__(
            "No se puede desactivar al único SUPERADMIN del sistema. "
            "Sin SUPERADMIN activo, nadie podría administrar la app."
        )


class CannotAssignSuperadminRoleError(UsuarioAdminError):
    """Un usuario sin rol SUPERADMIN intentó asignar el rol SUPERADMIN."""

    def __init__(self) -> None:
        super().__init__(
            "No tiene autorización para asignar el rol SUPERADMIN. "
            "Solo el SUPERADMIN actual puede transferir ese rol."
        )


class CannotResetSuperadminPasswordError(UsuarioAdminError):
    """Un usuario sin rol SUPERADMIN intentó resetear la password del SUPERADMIN."""

    def __init__(self) -> None:
        super().__init__(
            "No tiene autorización para cambiar la contraseña del SUPERADMIN. "
            "Solo el propio SUPERADMIN puede hacerlo."
        )


class CannotChangeSelfRoleError(UsuarioAdminError):
    """Un usuario intentó cambiar su propio rol."""

    def __init__(self) -> None:
        super().__init__(
            "No puede cambiar su propio rol. " "Otra persona con permisos debe hacerlo."
        )


class PasswordMismatchError(UsuarioAdminError):
    """La nueva contraseña y su confirmación no coinciden."""

    def __init__(self) -> None:
        super().__init__("La contraseña y su confirmación no coinciden.")


# ── Errores de consolidación (Fase 3) ─────────────────────────────────────────


class ConsolidacionError(Exception):
    """Clase base para errores de ``ConsolidacionService``."""


# ── Errores de asistencia (Fase 3.4b) ─────────────────────────────────────────


class AsistenciaError(Exception):
    """Clase base para errores de ``AsistenciaService``."""


class AsistenciaNotFoundError(AsistenciaError):
    """No existe una fila de asistencia con ese id."""

    def __init__(self, asistencia_id: int) -> None:
        super().__init__(f"No se encontró la asistencia con id {asistencia_id}.")
        self.asistencia_id = asistencia_id


# ── Errores de reportes (Fase 3.5) ────────────────────────────────────────────


class ReporteError(Exception):
    """Clase base para errores de ``ReporteService``."""


class ReporteIOError(ReporteError):
    """No se pudo escribir el archivo de reporte en disco.

    Causas típicas: permisos insuficientes, disco lleno, ruta inválida,
    archivo abierto en otro programa (Excel mantiene un lock).

    Attributes:
        ruta: Ruta que se intentó usar.
        causa: Mensaje original del SO traducido al español cuando es
            posible. Seguro de mostrar al usuario.
    """

    def __init__(self, ruta: str, causa: str) -> None:
        super().__init__(
            f"No se pudo escribir el archivo en '{ruta}': {causa}. "
            "Verifique que la ruta sea válida y que el archivo no esté "
            "abierto en otro programa."
        )
        self.ruta = ruta
        self.causa = causa


class ReporteSinDatosError(ReporteError):
    """El rango/filtro pedido no contiene asistencias para exportar.

    Decisión de diseño: NO generamos un Excel vacío. Si la query no
    devuelve filas, el servicio levanta este error y la UI muestra el
    mensaje sin escribir nada en disco ni registrar historial.
    """

    def __init__(self, desde: str, hasta: str, empleado_id: Optional[int]) -> None:
        if empleado_id is None:
            mensaje = (
                f"No hay asistencias registradas entre {desde} y {hasta}. " "No se generó archivo."
            )
        else:
            mensaje = (
                f"No hay asistencias del empleado seleccionado entre "
                f"{desde} y {hasta}. No se generó archivo."
            )
        super().__init__(mensaje)
        self.desde = desde
        self.hasta = hasta
        self.empleado_id = empleado_id


# ── Errores de ausencias (vacaciones, días libres, permisos) ──────────────────


class AusenciaError(Exception):
    """Clase base para errores de ``AusenciaService``."""


class AusenciaNotFoundError(AusenciaError):
    """No existe una ausencia con ese id (pudo haberla borrado otro usuario)."""

    def __init__(self, ausencia_id: int) -> None:
        super().__init__("Ese registro ya no existe. Actualice la lista e intente de nuevo.")
        self.ausencia_id = ausencia_id


class TipoAusenciaInvalidoError(AusenciaError):
    """El tipo no es uno de ``TipoAusencia``."""

    def __init__(self, tipo: str) -> None:
        super().__init__(f"Tipo de ausencia no reconocido: «{tipo}».")
        self.tipo = tipo


class RangoAusenciaInvalidoError(AusenciaError):
    """La fecha final es anterior a la inicial o el rango es demasiado largo."""

    def __init__(self, mensaje: str) -> None:
        super().__init__(mensaje)


class AusenciaEmpleadoInactivoError(AusenciaError):
    """No se registran ausencias a empleados dados de baja."""

    def __init__(self, empleado_id: int) -> None:
        super().__init__(
            "El empleado está dado de baja. Reactívelo primero si necesita registrarle ausencias."
        )
        self.empleado_id = empleado_id


class AusenciaSolapadaError(AusenciaError):
    """El empleado ya tiene otra ausencia en alguna de esas fechas."""

    def __init__(self, tipo_texto: str, desde: str, hasta: str) -> None:
        if desde == hasta:
            cuando = f"el {_fecha_corta(desde)}"
        else:
            cuando = f"del {_fecha_corta(desde)} al {_fecha_corta(hasta)}"
        super().__init__(
            f"El empleado ya tiene un registro de «{tipo_texto}» {cuando}, "
            "que se cruza con estas fechas. Edite ese registro o elija otras fechas."
        )
        self.desde = desde
        self.hasta = hasta


def _enumerar(partes: List[str]) -> str:
    """``["a", "b", "c"]`` → ``"a, b y c"``."""
    if not partes:
        return ""
    if len(partes) == 1:
        return partes[0]
    return ", ".join(partes[:-1]) + " y " + partes[-1]


def _fecha_corta(iso: str) -> str:
    """``"2026-11-02"`` → ``"02/11/2026"`` (sin depender de la capa UI)."""
    anio, mes, dia = iso[:10].split("-")
    return f"{dia}/{mes}/{anio}"


# ── Errores de feriados ───────────────────────────────────────────────────────


class FeriadoError(Exception):
    """Clase base para errores de ``FeriadoService``."""


class FeriadoNotFoundError(FeriadoError):
    """El feriado ya no existe (pudo borrarlo otro usuario)."""

    def __init__(self, feriado_id: int) -> None:
        super().__init__("Ese feriado ya no existe. Actualice la lista.")
        self.feriado_id = feriado_id


class FeriadoDuplicadoError(FeriadoError):
    """Ya hay un feriado registrado en esa fecha."""

    def __init__(self, fecha: str, descripcion: str) -> None:
        super().__init__(f"El {_fecha_corta(fecha)} ya es feriado: «{descripcion}».")
        self.fecha = fecha


# Nombres legibles de los campos que aparecen en ``MissingRequiredFieldError``.
_NOMBRE_CAMPO = {
    "nombre": "el nombre",
    "numero_serie": "el número de serie del reloj",
    "nombres": "los nombres",
    "apellidos": "los apellidos",
    "dni": "el DNI",
    "descripcion": "la descripción",
    "fecha": "la fecha",
    "fecha_desde": "la fecha inicial",
    "fecha_hasta": "la fecha final",
    "fecha_ingreso": "la fecha de ingreso",
    "fecha_baja": "la fecha de baja",
    "fecha_fin": "la fecha final",
    "desde": "la fecha inicial",
    "hasta": "la fecha final",
    "hora_entrada": "la hora de entrada",
    "hora_salida": "la hora de salida",
    "ip": "la dirección IP",
}
