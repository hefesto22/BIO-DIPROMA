-- 003_empleados_turnos.sql — Esquema de Fase 2 (CRUD empleados, turnos y catálogos).
--
-- Crea:
--   - Catálogos `departamentos` y `cargos` (lookup tables con archivado).
--   - Tabla `turnos` (bloque único + bitmask de días + flag cross-midnight).
--   - Tabla `empleados` (13 campos: identidad, básicos, baja, control).
--   - Tabla de relación `empleado_turnos` con historial de fechas.
--   - Índices de lookup frecuente + índice parcial para "turno vigente".
--   - NO agrega permisos nuevos: todo Fase 2 usa `manage_employees` (ya seedeado
--     en 002_auth.sql para SUPERADMIN y ADMIN).
--
-- Decisiones referenciadas: ver docs/fase2-prd.md (D1–D8).
--
-- Todas las sentencias son idempotentes (CREATE ... IF NOT EXISTS) para
-- sobrevivir a corridas accidentales del runner.


-- ── Catálogos ──────────────────────────────────────────────────────────────
-- Departamentos y cargos son tablas gemelas: mismo esquema, distinto
-- significado. Aisladas para permitir CRUD independiente desde la UI.
-- Archivado vía `is_active` (D6). Nombre único para prevenir duplicados.

CREATE TABLE IF NOT EXISTS departamentos (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre    TEXT    NOT NULL UNIQUE,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS cargos (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre    TEXT    NOT NULL UNIQUE,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);


-- ── Turnos ─────────────────────────────────────────────────────────────────
-- Modelo de D4: bloque único continuo con descanso interno, bitmask de 7
-- días, flag cross-midnight derivado de la comparación de horas.
--
-- Convenciones de formato:
--   hora_entrada / hora_salida : "HH:MM" 24h (texto para simplicidad; el
--     servicio valida con datetime.strptime).
--   dias_semana                : entero 0..127 (bitmask 7 bits). Bit 6 = Lunes,
--     bit 0 = Domingo. Ver core/models/turno.py para las constantes.
--   cruza_medianoche           : 0/1. El servicio lo setea automáticamente al
--     guardar según `hora_salida < hora_entrada`. Se persiste explícitamente
--     para que los queries no tengan que recalcularlo.

CREATE TABLE IF NOT EXISTS turnos (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre           TEXT    NOT NULL UNIQUE,
    hora_entrada     TEXT    NOT NULL,
    hora_salida      TEXT    NOT NULL,
    minutos_descanso INTEGER NOT NULL DEFAULT 0 CHECK (minutos_descanso >= 0),
    dias_semana      INTEGER NOT NULL CHECK (dias_semana BETWEEN 0 AND 127),
    cruza_medianoche INTEGER NOT NULL DEFAULT 0 CHECK (cruza_medianoche IN (0, 1)),
    is_active        INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);


-- ── Empleados ──────────────────────────────────────────────────────────────
-- Modelo de D2: 13 campos (identidad + básicos + baja + control).
--
-- Notas de integridad:
--   - `dni` único a nivel BD (D7). La máscara + regex se aplica en el servicio.
--   - `zkteco_id` es UNIQUE con SQLite-semantics: múltiples NULLs están
--     permitidos (el estándar SQL considera NULL != NULL), entonces varios
--     empleados sin sincronizar al reloj coexisten sin colisión. Una vez
--     asignado, no se puede repetir.
--   - FK a departamentos/cargos con ON DELETE RESTRICT: como los catálogos
--     se archivan (nunca borran — D6), este RESTRICT es "cinturón +
--     tirantes" — si alguna vez alguien intenta un DELETE físico, la BD
--     lo rechaza.
--   - CHECK de coherencia entre `is_active` y los campos de baja: si el
--     empleado está activo, fecha_baja/motivo_baja/nota_baja deben ser NULL;
--     si está inactivo, fecha_baja y motivo_baja son obligatorios, y
--     nota_baja es obligatoria únicamente cuando motivo_baja = 'OTRO'
--     (validado en servicio — el CHECK solo exige los dos primeros para no
--     sobrecomplicar la condición SQL).

CREATE TABLE IF NOT EXISTS empleados (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    dni             TEXT    NOT NULL UNIQUE,
    nombres         TEXT    NOT NULL,
    apellidos       TEXT    NOT NULL,
    departamento_id INTEGER NOT NULL,
    cargo_id        INTEGER NOT NULL,
    fecha_ingreso   TEXT    NOT NULL,
    telefono        TEXT,
    email           TEXT,
    zkteco_id       INTEGER UNIQUE,
    is_active       INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    fecha_baja      TEXT,
    motivo_baja     TEXT
        CHECK (
            motivo_baja IS NULL
            OR motivo_baja IN (
                'DESPIDO', 'RENUNCIA', 'JUBILACION',
                'FIN_CONTRATO', 'FALLECIMIENTO', 'OTRO'
            )
        ),
    nota_baja       TEXT,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    FOREIGN KEY (departamento_id) REFERENCES departamentos(id) ON DELETE RESTRICT,
    FOREIGN KEY (cargo_id)        REFERENCES cargos(id)        ON DELETE RESTRICT,
    CHECK (
        (is_active = 1 AND fecha_baja IS NULL AND motivo_baja IS NULL AND nota_baja IS NULL)
        OR
        (is_active = 0 AND fecha_baja IS NOT NULL AND motivo_baja IS NOT NULL)
    )
);


-- ── Empleado ↔ Turno con historial ─────────────────────────────────────────
-- Modelo de D5: tabla intermedia con `fecha_inicio` y `fecha_fin`. NULL en
-- fecha_fin significa "turno vigente". La invariante "un solo vigente por
-- empleado" se valida en el servicio (no a nivel de esquema, porque SQLite
-- no soporta UNIQUE con condición WHERE directamente en CREATE TABLE — pero
-- SÍ lo soportamos a nivel de índice parcial más abajo).

CREATE TABLE IF NOT EXISTS empleado_turnos (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    empleado_id  INTEGER NOT NULL,
    turno_id     INTEGER NOT NULL,
    fecha_inicio TEXT    NOT NULL,
    fecha_fin    TEXT,
    FOREIGN KEY (empleado_id) REFERENCES empleados(id) ON DELETE RESTRICT,
    FOREIGN KEY (turno_id)    REFERENCES turnos(id)    ON DELETE RESTRICT,
    UNIQUE (empleado_id, fecha_inicio),
    CHECK (fecha_fin IS NULL OR fecha_fin >= fecha_inicio)
);


-- ── Índices ────────────────────────────────────────────────────────────────
-- Lookups frecuentes que se ejecutan desde la UI:
--   - Buscar empleados por departamento o cargo (listas filtradas).
--   - Filtrar por is_active para la vista "mostrar archivados".
--   - Obtener el turno vigente de un empleado (índice parcial).
--   - Obtener todos los empleados que tienen cierto turno asignado.

CREATE INDEX IF NOT EXISTS idx_empleados_departamento_id
    ON empleados(departamento_id);

CREATE INDEX IF NOT EXISTS idx_empleados_cargo_id
    ON empleados(cargo_id);

CREATE INDEX IF NOT EXISTS idx_empleados_is_active
    ON empleados(is_active);

CREATE INDEX IF NOT EXISTS idx_empleado_turnos_empleado_id
    ON empleado_turnos(empleado_id);

CREATE INDEX IF NOT EXISTS idx_empleado_turnos_turno_id
    ON empleado_turnos(turno_id);

-- Índice parcial único: garantiza a nivel BD que cada empleado tenga a lo
-- sumo UN turno vigente (fecha_fin IS NULL). Defensa en profundidad frente
-- a la validación del servicio — si alguien bypasseara el servicio e
-- insertara directo, la BD rechaza. SQLite ≥ 3.8.0 soporta WHERE en índice.
CREATE UNIQUE INDEX IF NOT EXISTS idx_empleado_turnos_vigente_unico
    ON empleado_turnos(empleado_id) WHERE fecha_fin IS NULL;
