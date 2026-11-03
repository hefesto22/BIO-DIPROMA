# PRD — Fase 2: CRUD de Empleados, Turnos y Catálogos

**Proyecto:** BioMuni — Sistema de Asistencia Biométrica
**Cliente:** Municipalidad de Ocotepeque
**Aporte académico de:** Sammy Alberto Oliva Molina — UCENM
**Fecha de cierre del PRD:** 2026-04-22
**Estado:** Aprobado — listo para implementación
**Fase anterior:** Fase 1 (Autenticación y Control de Acceso) — cerrada en commit `b88b27e`

---

## 1. Objetivo de la fase

Construir el CRUD completo (backend + UI) para las cuatro entidades maestras del
sistema: **empleados**, **turnos**, **departamentos** y **cargos**. Al final de
Fase 2, un ADMIN debe poder dar de alta, editar, archivar y consultar cualquiera
de estas entidades desde la aplicación, con todos los cambios auditados y
persistidos en SQLite.

Fase 2 **no** incluye: sincronización con el reloj ZKTeco (Fase 4), cálculo de
asistencia (Fase 3) ni generación de reportes (Fase 5).

---

## 2. Alcance

### Entra en Fase 2

- Esquema SQL (migración `002_empleados_turnos.sql`) para las cuatro entidades
  y la tabla de relación `empleado_turnos`.
- Modelos de dominio (`@dataclass`) para cada entidad.
- Repositorios (interfaces + implementaciones SQLite) con separación
  `IReadRepository` / `IWriteRepository`.
- Servicios de negocio (`EmpleadoService`, `TurnoService`, `CatalogoService`)
  con todas las validaciones.
- Controllers con `@require_permission` aplicado.
- Vistas customtkinter: lista con búsqueda/filtros, formulario de
  alta/edición, confirmación de archivado.
- Registro en `audit_log` de toda alta, edición y archivado.
- Tests unitarios — cobertura mínima 70% en `core/services/` y
  `core/repositories/` de las entidades nuevas.

### Queda fuera de Fase 2 (fases posteriores)

- Importación masiva desde Excel (se evaluará en Fase 5 cuando estén los
  exportadores).
- Tolerancia de atraso/salida temprana por turno o global (se define al
  implementar el cálculo de asistencia — Fase 3).
- Política de asignación del `zkteco_id` al sincronizar con el reloj
  (Fase 4).
- Validación con dígito verificador del RNP (queda como mejora opcional
  si el nivel de regex + UNIQUE resulta insuficiente en producción).

---

## 3. Decisiones técnicas aprobadas

Todas las decisiones listadas a continuación fueron aprobadas explícitamente
por Mauricio durante la sesión de PRD del 2026-04-22. Cada una lista las
opciones consideradas y la razón por la que se eligió la implementada, para
servir de audit trail si en el futuro alguien (incluido un agente futuro)
necesita entender por qué el diseño es así.

### Decisión 1 — Identidad del empleado

**Adoptado:**

- **DNI hondureño** como identidad natural del empleado — obligatorio, único,
  formato `XXXX-XXXX-XXXXX`.
- **`empleado_id`** interno autogenerado (INTEGER PK AUTOINCREMENT) —
  invisible al usuario, usado en todas las FKs.
- **`zkteco_id`** — campo separado, único pero nullable (permite dar de alta
  al empleado antes de registrarlo en el reloj).

**Rechazado:** Usar el DNI como PK directa o usar un "código interno RRHH"
como identidad oficial. El DNI cambia de formato si RNP lo modifica y los
códigos internos varían por municipalidad; mantener el `empleado_id` como
PK estable protege las FKs.

### Decisión 2 — Atributos del empleado + tracking de baja

**Adoptado — 13 campos:**

| Campo | Tipo | Obligatorio | Nota |
|---|---|---|---|
| `id` | INTEGER PK | ✓ | Auto-incremento |
| `dni` | TEXT | ✓ | Único, formato `XXXX-XXXX-XXXXX` |
| `nombres` | TEXT | ✓ | Texto libre |
| `apellidos` | TEXT | ✓ | Texto libre |
| `departamento_id` | INTEGER FK | ✓ | FK a `departamentos(id)` |
| `cargo_id` | INTEGER FK | ✓ | FK a `cargos(id)` |
| `fecha_ingreso` | DATE | ✓ | ISO `YYYY-MM-DD` |
| `telefono` | TEXT | — | Opcional |
| `email` | TEXT | — | Opcional |
| `zkteco_id` | INTEGER | — | Único si no es NULL |
| `is_active` | BOOL | ✓ | Default TRUE |
| `fecha_baja` | DATE | — | NOT NULL si `is_active = FALSE` |
| `motivo_baja` | ENUM | — | NOT NULL si `is_active = FALSE` |
| `nota_baja` | TEXT | — | NOT NULL si `motivo_baja = OTRO` |

**Catálogo cerrado de `motivo_baja`:** `DESPIDO`, `RENUNCIA`, `JUBILACION`,
`FIN_CONTRATO`, `FALLECIMIENTO`, `OTRO`.

**Rechazado:** Guardar `fecha_nacimiento`, `sexo`, `estado_civil`, `dirección`,
`salario` — son datos de RRHH/nómina, fuera del alcance de un sistema de
asistencia. Si surge necesidad, se agregan con una migración futura.

### Decisión 3 — Departamento y cargo como catálogos

**Adoptado:** Tablas separadas `departamentos` y `cargos`, FK en
`empleados`. Ambos catálogos tienen esquema idéntico:

```sql
CREATE TABLE departamentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE,
    is_active INTEGER NOT NULL DEFAULT 1
);
-- idem para cargos
```

**Rechazado:** Guardar `departamento` y `cargo` como TEXT libre en
`empleados`. Aunque es más simple de implementar, cualquier typo rompe los
reportes agrupados ("Obras Públicas" ≠ "obras publicas" ≠ "O. Publicas").
Los catálogos garantizan consistencia al costo de dos CRUDs adicionales
muy simples.

### Decisión 4 — Modelo de Turno

**Adoptado — 7 campos por turno:**

| Campo | Tipo | Nota |
|---|---|---|
| `id` | INTEGER PK | Auto-incremento |
| `nombre` | TEXT | Único, ej. "Administrativo 8-5" |
| `hora_entrada` | TIME | ISO `HH:MM` |
| `hora_salida` | TIME | ISO `HH:MM` |
| `minutos_descanso` | INTEGER | Default 0, para almuerzo interno |
| `dias_semana` | INTEGER | Bitmask 7 bits (Lunes = bit 6, Domingo = bit 0) |
| `cruza_medianoche` | BOOL | Derivado: TRUE si `hora_salida < hora_entrada` |
| `is_active` | BOOL | Default TRUE |

**Restricciones implícitas:**

- Un turno es un **único bloque continuo** con descanso interno (no se
  modela jornada partida con dos bloques separados). La jornada estándar
  `08:00–17:00` con almuerzo `12:00–13:00` se representa como entrada `08:00`,
  salida `17:00`, descanso `60 minutos`.
- Soporte a turnos nocturnos cross-midnight vía el flag `cruza_medianoche`.
  Los registros que caen en ese rango se imputan al día en que **comenzó** el
  turno.

**Rechazado:** Modelo de "bloques múltiples" (permitir 8-12 + 14-18 como dos
entradas del mismo turno). La municipalidad no tiene jornadas partidas
reales — siempre hay almuerzo dentro de un bloque continuo.

### Decisión 5 — Relación Empleado ↔ Turno con historial

**Adoptado:** Tabla intermedia `empleado_turnos` con fechas:

```sql
CREATE TABLE empleado_turnos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    empleado_id INTEGER NOT NULL REFERENCES empleados(id),
    turno_id INTEGER NOT NULL REFERENCES turnos(id),
    fecha_inicio DATE NOT NULL,
    fecha_fin DATE NULL,   -- NULL = vigente
    UNIQUE(empleado_id, fecha_inicio)
);
```

**Invariante:** cada empleado tiene **a lo sumo una fila con `fecha_fin = NULL`**
a la vez (su turno vigente). Esta invariante se valida a nivel de servicio
(no de esquema, porque SQLite no soporta UNIQUE condicional sin índice parcial).

**Cambio de turno = transacción atómica:**

1. `UPDATE empleado_turnos SET fecha_fin = :ayer WHERE empleado_id = :id AND fecha_fin IS NULL`
2. `INSERT INTO empleado_turnos (empleado_id, turno_id, fecha_inicio) VALUES (:id, :nuevo_turno, :hoy)`

**Rechazado:**

- **1:1 directo** (`turno_id` como columna en `empleados`): se pierde el
  historial y los reportes históricos se ensucian en cuanto hay el primer
  cambio de turno.
- **N:N simultáneo**: sobre-diseño — la municipalidad no tiene empleados
  con dos turnos en paralelo y complicaría el cálculo de asistencia.

### Decisión 6 — Archivado universal (sin DELETE físico)

**Adoptado:** Las **cuatro** tablas maestras (`empleados`, `turnos`,
`departamentos`, `cargos`) usan `is_active = FALSE` como mecanismo de
"borrado lógico". Ninguna de estas entidades se elimina físicamente de la
BD.

**Implicaciones:**

- Entidades archivadas no aparecen en dropdowns de creación/edición de
  nuevos registros.
- Vistas de lista tienen un checkbox "Mostrar archivados" que por defecto
  está **apagado**.
- Reportes históricos siempre resuelven correctamente — aunque una entidad
  esté archivada hoy, los empleados que la tenían asignada en el pasado
  mantienen su historial intacto.
- Reactivar una entidad es reversible con un click.

**Rechazado:** DELETE físico con FK RESTRICT (Opción A). Aunque mantiene
la BD más "limpia", rompe los reportes históricos cuando alguien borra un
departamento que alguna vez existió.

### Decisión 7 — Validación del DNI

**Adoptado — Nivel 2: formato estricto + máscara + UNIQUE.**

- Regex: `^\d{4}-\d{4}-\d{5}$`.
- En la UI, el campo de DNI aplica una **máscara** que auto-inserta los
  guiones (`0801` → `0801-` → `0801-1990-` → `0801-1990-12345`). El usuario
  no puede meter un formato inválido — lo fuerza el widget.
- A nivel BD: constraint `UNIQUE` en `empleados.dni`.
- Error genérico al usuario si intenta guardar un DNI ya existente: "Ya
  existe un empleado con este DNI." (no revela quién).

**Rechazado:**

- **Nivel 1** (solo no-vacío): permite basura.
- **Nivel 3** (dígito verificador RNP): requiere mantener el algoritmo
  oficial del RNP; puede rechazar DNIs antiguos válidos. Se deja como
  mejora opcional si el Nivel 2 resulta insuficiente.

**Escape hatch documentado:** si en el futuro aparece un empleado
extranjero, se agregaría un campo `tipo_identidad` con enum `DNI_HN`,
`PASAPORTE`, `RESIDENCIA`. Por ahora todos los empleados son hondureños
con DNI local.

### Decisión 8 — Organización del menú lateral

**Adoptado:** Empleados y Turnos como botones principales; Departamentos
y Cargos agrupados bajo "Configuración maestra" con tabs.

Sidebar final de Fase 2:

```
├── Usuarios              [Fase 1 — SUPERADMIN]
├── Empleados             [Fase 2 — SUPERADMIN, ADMIN]
├── Turnos                [Fase 2 — SUPERADMIN, ADMIN]
├── Configuración maestra [Fase 2 — SUPERADMIN, ADMIN]
│     ├── Tab "Departamentos"
│     └── Tab "Cargos"
├── (botones futuros de Fase 3/4/5 deshabilitados)
└── Cerrar sesión
```

**Rechazado:**

- **Opción A** (un botón por entidad): 4 botones nuevos sobrecargan el
  sidebar, y Departamentos/Cargos son pantallas de uso esporádico que no
  merecen el mismo peso visual que Empleados.
- **Opción C** (todo bajo un único "Gestión" con 4 tabs): esconde
  Empleados — la vista más usada — tras un click extra.

---

## 4. Permisos (reafirmación desde el PRD general)

La matriz de permisos ya definida en el prompt del proyecto aplica
íntegramente. Resumen para Fase 2:

| Acción | SUPERADMIN | ADMIN | REPORTES | OPERADOR |
|---|---|---|---|---|
| Ver lista de empleados | ✓ | ✓ | — | — |
| Crear/editar/archivar empleado | ✓ | ✓ | — | — |
| Ver lista de turnos | ✓ | ✓ | — | — |
| Crear/editar/archivar turno | ✓ | ✓ | — | — |
| Gestionar departamentos/cargos | ✓ | ✓ | — | — |
| Asignar turno a empleado | ✓ | ✓ | — | — |

Toda la funcionalidad de Fase 2 queda gobernada por el permiso existente
**`manage_employees`** (definido en `core/models/permissions.py` y ya
asignado a SUPERADMIN y ADMIN en la migración `002_auth.sql`). No se
agregan permisos nuevos en Fase 2 — el permiso paraguas cubre empleados,
turnos y catálogos maestros. OPERADOR no ve ninguna de las pantallas de
gestión; su acceso se limita a sincronización ZKTeco y vista de
asistencia (Fases 3 y 4).

---

## 5. Plan de Sub-entregables

Propuesto (pendiente de aprobación antes de arrancar Sub-2.1):

1. **Sub-2.1 — Esquema + modelos de dominio.** Migración
   `002_empleados_turnos.sql`, `@dataclass`es en `core/models/`.
2. **Sub-2.2 — Repositorios.** Interfaces (ISP) +
   implementaciones SQLite + tests unitarios (DB en memoria).
3. **Sub-2.3 — Servicios.** `CatalogoService`, `TurnoService`,
   `EmpleadoService` con validaciones + tests.
4. **Sub-2.4 — UI Configuración maestra.** Vista con tabs de departamentos
   y cargos (es la más simple — punto de entrada suave a la UI de Fase 2).
5. **Sub-2.5 — UI Turnos.** Lista + formulario + archivado.
6. **Sub-2.6 — UI Empleados.** Lista con búsqueda + formulario (incluye
   máscara de DNI, dropdowns de departamento/cargo, asignación inicial de
   turno, flujo de archivado con motivo).

Cada sub-entregable cierra con los 4 gates verdes (black, flake8,
mypy --strict, pytest), commit convencional en `develop`, y breve reporte
de qué se hizo / qué tests pasan / deuda técnica.

---

## 6. Historial del documento

| Fecha | Evento |
|---|---|
| 2026-04-22 | Q&A dirigido con Mauricio, 8 decisiones aprobadas, PRD consolidado. |
