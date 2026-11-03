# Manual de usuario — BioMuni

**Cliente:** Municipalidad de Ocotepeque
**Aporte académico de:** Sammy Alberto Oliva Molina — UCENM
**Versión del manual:** 1.0 · **Fecha:** 2026-04-30

Este manual está pensado para el operador que usa la aplicación día a día (alta de empleados, sincronización del reloj K40, generación de reportes) y para el administrador técnico que la mantiene (backups, recuperación de cuentas, actualizaciones).

---

## 1. Requisitos e instalación

La aplicación se distribuye como ejecutable portable para Windows (`zkteco.exe`). No requiere instalador. Para correrla:

1. Copiá la carpeta `zkteco-portable-vX.Y.Z.zip` a la PC operativa (preferentemente en `C:\zkteco\`).
2. Descomprimila — vas a obtener una carpeta `zkteco\` con el `.exe` y los recursos.
3. Doble click en `zkteco.exe`. La primera vez tarda unos segundos en arrancar mientras crea la base de datos local.

La aplicación guarda todos sus datos en `zkteco\data\`:

- `zkteco_app.db` — la base de datos SQLite con empleados, turnos, asistencias, etc.
- `data\logs\` — logs rotativos de la aplicación.
- `data\exports\` — sugerencia para guardar los Excel exportados.
- `data\backups\` — snapshots automáticos de la base.

**No borres la carpeta `data\` entre actualizaciones** — perdés todo el histórico. Las nuevas versiones del `.exe` están preparadas para preservarla.

---

## 2. Primer arranque — Setup Wizard

La primera vez que abrís la aplicación, te pide crear el único **SUPERADMIN** del sistema. Este usuario tiene acceso total y es el único que puede crear o modificar otros usuarios y roles.

Pasos:

1. **Username** — el nombre con que vas a iniciar sesión (3 a 32 caracteres, solo letras, números, punto, guion bajo o guion).
2. **Nombre completo** — para mostrar en pantalla y reportes.
3. **Contraseña** — mínimo 8 caracteres, con al menos una letra y un número. Debe confirmarse dos veces.

Si por error cancelás antes de terminar, podés reabrir el `.exe` y el wizard arranca de nuevo. Una vez creado el SUPERADMIN, el wizard ya no se muestra más.

> **Anotá la contraseña en un lugar seguro.** Si la perdés, hay que correr el script de recuperación (sección 12) desde la línea de comandos.

---

## 3. Inicio de sesión y roles

Después del wizard, cada arranque te lleva a la pantalla de **login**. Los mensajes de error son genéricos por seguridad ("usuario o contraseña incorrectos") — no revelan si el usuario existe.

Tras 5 intentos fallidos consecutivos, la cuenta queda bloqueada por 15 minutos. Pasado ese tiempo, podés volver a intentar.

**Roles del sistema:**

| Rol | Para quién | Qué puede hacer |
|-----|------------|-----------------|
| SUPERADMIN | Vos (Mauricio) | Todo — crea usuarios, roles, configuración, todo lo demás. |
| ADMIN | Jefe de RRHH | Empleados, turnos, dispositivos, sincronización, reportes. NO puede tocar roles del sistema. |
| REPORTES | Contabilidad | Solo descargar y ver reportes Excel. |
| OPERADOR | Personal de portería | Solo ejecutar la sincronización con el K40 y ver asistencia en pantalla. |

El menú lateral muestra solo las opciones que tu rol puede ver. La barra inferior siempre indica usuario activo, rol y estado de conexión con el K40.

---

## 4. Gestión de empleados

Menú: **Empleados**.

**Alta:**

1. Click en **+ Nuevo empleado**.
2. Completá DNI (formato `XXXX-XXXX-XXXXX`), nombres, apellidos, departamento, cargo, fecha de ingreso.
3. **ZKTeco ID** — opcional al crear. Es el número con que el empleado figura en el reloj K40. Se puede cargar después.
4. Teléfono y email — opcionales.

**Edición:** click en cualquier fila → botón **Editar**.

**Archivado (baja):** botón **Archivar**. Pide motivo (RENUNCIA / DESPIDO / JUBILACION / OTRO) y, si elegís OTRO, una nota explicativa. El empleado queda oculto del listado normal pero su histórico se preserva. Para verlo de vuelta, marcá el checkbox **Ver archivados**.

**Reactivar:** desde el detalle de un empleado archivado → botón **Reactivar**.

---

## 5. Departamentos y cargos

Menú: **Configuración → Catálogos**.

Antes de dar de alta empleados necesitás crear al menos un departamento (ej. "Tesorería", "Obras Públicas") y un cargo (ej. "Auxiliar", "Director"). Tienen el mismo flujo CRUD que empleados: nuevo, editar, archivar.

Un departamento o cargo con empleados activos asignados **no se puede archivar** hasta reasignar o archivar a esos empleados.

**Cargos globales vs específicos de un departamento (Sub-3.2.A):**

Cuando creás un cargo, podés dejarlo **global** (aplica a cualquier departamento, ej. "Auxiliar") o **restringirlo a un departamento** (ej. "Tesorero" solo en "Tesorería"). En el formulario de empleado, al elegir el departamento, el dropdown de cargo se refresca y muestra solo los cargos elegibles: los globales más los específicos de ese departamento. Esto evita errores como asignar "Tesorero" a alguien de "Obras Públicas".

Si un cargo ya tiene empleados activos asignados a un departamento, no se puede mover a otro departamento incompatible — primero hay que reasignar o archivar a esos empleados.

---

## 6. Turnos y asignación

Menú: **Turnos**.

Cada turno define:

- **Nombre** — texto libre (ej. "Diurno 08-17", "Nocturno 22-06").
- **Hora de entrada y salida** — formato `HH:MM` 24 horas.
- **Días de la semana** — bitmask configurable (lunes a domingo).
- **Minutos de descanso** — para cálculo de horas trabajadas.
- **Tolerancia de entrada / salida** — minutos de gracia que NO cuentan como tarde / salida temprana.

Si la hora de salida es menor o igual que la de entrada, el turno se considera **nocturno** (cruza medianoche).

**Asignar turno a un empleado:** desde el detalle del empleado → **Asignar turno** → seleccionar turno y fecha de inicio. Para cambiar de turno, usar **Cambiar turno** (cierra el actual y abre el nuevo en una operación atómica).

**Turnos múltiples por empleado (Sub-3.2.B — backend):**

A nivel de la base de datos, un empleado puede tener varias asignaciones de turno vigentes en paralelo, siempre que los días de la semana de cada bitmask **no se solapen**. Caso típico: el mismo empleado trabaja lun-vie 8-17 (turno A) y sábados 8-13 (turno B). Para usarlo:

1. Crear los dos turnos por separado, cada uno con su propio bitmask de días (turno A: lun-vie; turno B: sábado).
2. Asignar primero el turno A al empleado.
3. Asignar el turno B al mismo empleado (con fecha de inicio distinta para no chocar con el UNIQUE de empleado/fecha).

La consolidación de asistencia identifica automáticamente cuál de las asignaciones vigentes aplica a cada día según el bitmask del turno. Si los días se solapan (ej. dos turnos para el lunes), el sistema rechaza la segunda asignación con un mensaje explícito.

> Nota: la UI actual permite asignar un turno a la vez. Para múltiples vigentes en el corto plazo, usar el script de utilidad o esperar Sub-3.3 que expondrá el flujo completo desde la pantalla del empleado.

---

## 7. Configuración del dispositivo K40

Menú: **Configuración → Dispositivos**.

Para conectar la aplicación con el reloj físico:

1. Asegurate que el K40 esté en la misma red local que la PC (cable Ethernet o Wi-Fi).
2. En el K40, andá a **Menú → Conexión → IP** y anotá la IP (típicamente `192.168.0.X`).
3. En la app, click **+ Nuevo dispositivo** y completá:
   - Nombre — texto libre (ej. "K40 Principal").
   - IP — la del paso anterior.
   - Puerto — `4370` por defecto (estándar ZKTeco).
   - Activo — marcado.
4. Guardar.

Podés tener varios dispositivos registrados (ej. K40 entrada + K40 salida). La aplicación trata las marcadas de cualquier dispositivo del mismo empleado como del mismo día.

---

## 8. Sincronización con el K40

Menú: **Sincronización ZKTeco**.

1. Seleccioná el **dispositivo** del dropdown.
2. Elegí el **rango de fechas** a importar (típicamente solo el día actual).
3. Click **Sincronizar**.

La app se conecta al reloj, lee todas las marcadas del rango, las guarda en la tabla `registros_raw` y luego las consolida en asistencias usando el algoritmo descrito en la sección 9. Toda la operación corre en segundo plano — la UI no se traba.

El **"Último resultado"** muestra:

- **Registros recibidos** — cuántas marcadas trajo el K40.
- **Asistencias consolidadas** — cuántas filas se generaron en la tabla `asistencias`.
- **Empleados / Días** — cobertura.
- **Advertencias** — IDs del K40 que no están mapeados a ningún empleado de la BD (típicamente, alguien marcó pero no está dado de alta en el sistema).

La barra inferior pasa a mostrar `ZKTeco: última sync HH:MM` — esa marca de tiempo se preserva entre logins.

---

## 9. Algoritmo de cálculo de asistencia

La app usa un algoritmo llamado **"ventana dinámica"** que respeta la realidad operativa de los relojes K40 (que no distinguen entrada vs salida explícita en cada marcada).

Reglas:

1. La **primera marcada del día** es la entrada, sin importar la hora (sea 7:00, 10:30 o 14:00). Esto cubre llegadas tardías sin perder la información.
2. Las marcadas dentro de la **primera hora** después de la entrada se consideran **rebotes** (el empleado pasó el dedo varias veces al entrar) y se ignoran.
3. La marcada **más tardía después de esa hora** es la salida — incluso si está después del fin del turno. Si entró 7:00 y se fue 19:00, la salida es 19:00.
4. Si NO hay segunda marcada y ya **pasaron las 20:00**, la salida se asume igual a la hora oficial del turno y queda anotado en observaciones como "Salida asumida según turno: sin segunda marcada".
5. Si todavía no son las 20:00, la asistencia queda como **INCOMPLETO** porque el empleado todavía puede volver a marcar.

**Estados posibles de una asistencia:**

| Estado | Significado |
|--------|-------------|
| PRESENTE | Entrada y salida dentro de tolerancia. |
| TARDE | Entrada fuera de tolerancia. |
| SALIDA_TEMPRANA | Salida anticipada fuera de tolerancia. |
| TARDE_Y_SALIDA_TEMPRANA | Ambos. |
| AUSENTE | Día laborable sin marcadas. |
| INCOMPLETO | Solo entrada o solo salida (día aún abierto). |
| SIN_TURNO | El día no aplica al turno asignado (ej. domingo). |
| FERIADO | Fecha registrada en la tabla de feriados. |

---

## 10. Visualización y edición de asistencia

Menú: **Asistencia**.

Filtros disponibles: por empleado, por rango de fechas. La tabla muestra una fila por (empleado, día) con su estado, hora de entrada real, hora de salida real y observaciones.

**Editar observación manual:** click en la fila → **Editar obs.**. El texto manual se preserva incluso si re-consolidás más tarde — la consolidación automática NO sobrescribe observaciones que vos hayas escrito.

**Re-consolidar:** botón **Re-consolidar** arriba a la derecha. Útil cuando:

- Cambiaste un turno y querés recalcular todos los días afectados.
- Editaste un empleado (DNI, ZKTeco ID) y necesitás que las asistencias viejas reflejen el cambio.
- Pasaron las 20:00 y querés cerrar los días "incompletos" con la salida asumida.

---

## 11. Reportes Excel

Menú: **Reportes → Asistencia**.

1. Elegí rango de fechas (por defecto: del 1 del mes actual al día de hoy).
2. Opcionalmente, filtrá por un empleado específico.
3. Click **Exportar Excel**.

El archivo se guarda en `C:\Users\<vos>\Downloads\` con nombre `reporte_asistencia_<desde>_a_<hasta>.xlsx`. Si ya hay un archivo con ese nombre **abierto en Excel**, vas a recibir un error de permisos — cerrá Excel y volvé a generar.

El archivo tiene tres hojas:

- **Resumen** — agregado por empleado: días presente, tarde, ausente, etc., minutos totales de tardanza.
- **Detalle** — una fila por (empleado, fecha): hora real de entrada, hora real de salida, estado, minutos, observaciones.
- **Metadatos** — quién generó el reporte, cuándo y para qué rango.

El **Historial de descargas** (subtab dentro de Reportes) lista los últimos reportes generados.

---

## 12. Gestión de usuarios y roles

Menú: **Usuarios y roles** (solo SUPERADMIN ve todas las opciones; ADMIN puede crear/editar usuarios pero NO roles).

**Crear usuario:**

1. Click **Nuevo usuario**.
2. Username, nombre completo, rol (no incluye SUPERADMIN si lo crea ADMIN), contraseña inicial.
3. La contraseña inicial debe cumplir las reglas de complejidad (mínimo 8, con letra y número).

**Resetear contraseña:** click en el usuario → **Resetear contraseña**. El SUPERADMIN no se puede resetear desde la UI por seguridad (ver sección 12 si vos sos el SUPERADMIN y olvidaste tu password).

**Desactivar usuario:** click en el usuario → **Desactivar**. La cuenta queda inhabilitada para login pero su histórico se preserva. No se permite desactivar al único SUPERADMIN ni a vos mismo.

**Destrabar cuenta:** si un usuario quedó bloqueado por intentos fallidos, click en su fila → **Destrabar**. Limpia el contador y deja la cuenta lista para usar.

---

## 13. Backup automático de la base de datos

Cada vez que abrís la aplicación, antes de mostrar el login, se crea automáticamente un snapshot del archivo `zkteco_app.db` en `data\backups\zkteco_app_YYYY-MM-DD.db`. Los snapshots se conservan **30 días** y los más viejos se borran automáticamente al arrancar.

Si necesitás más de 30 días de retención, copiá los archivos a una ubicación externa (USB, OneDrive, network share) periódicamente.

**Para restaurar un backup:**

1. Cerrá la aplicación.
2. Renombrá el actual `data\zkteco_app.db` a `data\zkteco_app.db.viejo` (por si necesitás revertir).
3. Copiá `data\backups\zkteco_app_YYYY-MM-DD.db` → `data\zkteco_app.db`.
4. Volvé a abrir la app.

---

## 14. Recuperación del SUPERADMIN

Si olvidaste la contraseña del SUPERADMIN o quedó bloqueado y no hay otro admin disponible para destrabarlo, hay un script de recuperación de emergencia que se corre desde la terminal:

```powershell
cd C:\zkteco\
python -m bin.recover_superadmin --db-path "data\zkteco_app.db"
```

Te va a pedir:

1. Confirmar el username del SUPERADMIN (escribirlo exacto).
2. Nueva contraseña, dos veces.

El script actualiza la contraseña, limpia el lockout y deja huella en el `audit_log` con la acción `superadmin_recovered`. La operación queda registrada para auditoría.

> Este script tiene acceso directo a la BD y NO requiere autenticación. Por eso solo debe ejecutarse desde la PC operativa, nunca desde una conexión remota expuesta.

---

## 15. Actualizar la aplicación

Cuando llegue una nueva versión del `.exe`:

1. Cerrá la aplicación abierta.
2. Hacé un backup manual extra de `data\zkteco_app.db` (copiá el archivo a otra carpeta — paranoia recomendada).
3. Reemplazá el `.exe` y los archivos de programa con los de la nueva versión, **dejando intacta la carpeta `data\`**.
4. Abrí la aplicación. Si hay migraciones nuevas de base de datos, se aplican automáticamente al arrancar.

Si después de actualizar la app no abre, restaurá el backup del paso 2 y avisá al proveedor con el log de `data\logs\app.log`.

---

## 16. Solución de problemas comunes

| Síntoma | Probable causa | Solución |
|---------|----------------|----------|
| "ZKTeco: sin conexión" en la barra inferior | El K40 está apagado, en otra red o con la IP cambiada. | Verificar IP en el K40 y editarla en Configuración → Dispositivos. |
| Sincronización lenta o falla con timeout | Red congestionada o K40 con muchas marcadas pendientes. | Sincronizar rangos chicos (un día a la vez) hasta ponerse al día. |
| "No se pudo escribir el archivo en Downloads: Permission denied" | El Excel destino está abierto. | Cerrar Excel y volver a generar. |
| Empleado marcó pero no aparece en asistencia | El ZKTeco ID del empleado en la BD no coincide con el ID que usa el K40. | Editar empleado y poner el ZKTeco ID correcto, después re-sincronizar. |
| Empleado dice "marqué" pero el reporte dice AUSENTE | La marcada quedó fuera de la ventana del turno (ej. el turno termina 17:00 con tolerancia 0 y el empleado marcó 17:30). | Aumentar la tolerancia del turno o registrar manualmente en observaciones. |
| El botón "Sincronizar" está deshabilitado | No hay dispositivos activos. | Ir a Configuración → Dispositivos y registrar el K40. |
| Olvidé mi password de SUPERADMIN | — | Ver sección 14. |

---

## 17. Soporte

Si encontrás un problema que no podés resolver con este manual:

1. Sacá una captura de pantalla del error.
2. Adjuntá el archivo `data\logs\app.log` (los últimos 200 KB).
3. Mandalo al proveedor con una descripción de qué estabas haciendo cuando apareció el error.

---

*Fin del manual.*
