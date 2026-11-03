-- 012_ausencias.sql — Vacaciones, días libres, permisos e incapacidades.
--
-- 1. Tabla `ausencias`: un rango de fechas (inclusivo) por registro.
--    · `tipo` cerrado: VACACIONES, DIA_LIBRE, PERMISO, INCAPACIDAD.
--    · Que dos ausencias del mismo empleado no se crucen lo valida
--      `AusenciaService` (comparación de rangos, no expresable como CHECK).
--    · `creado_por_user_id` usa ON DELETE SET NULL para conservar el
--      historial aunque se elimine el usuario (igual que audit_log).
--
-- 2. `asistencias.estado` acepta los cuatro estados nuevos. SQLite no
--    permite alterar un CHECK: se reconstruye la tabla con la estrategia
--    oficial (igual que la migración 009):
--      a. PRAGMA foreign_keys=OFF.
--      b. Tabla nueva con el CHECK ampliado.
--      c. Copia de todas las filas preservando ids.
--      d. DROP de la vieja + RENAME.
--      e. Recrear índices.
--      f. PRAGMA foreign_keys=ON.

CREATE TABLE IF NOT EXISTS ausencias (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    empleado_id        INTEGER NOT NULL,
    tipo               TEXT    NOT NULL
        CHECK (tipo IN ('VACACIONES', 'DIA_LIBRE', 'PERMISO', 'INCAPACIDAD')),
    fecha_desde        TEXT    NOT NULL,
    fecha_hasta        TEXT    NOT NULL,
    nota               TEXT,
    creado_por_user_id INTEGER,
    creado_en          TEXT    NOT NULL,
    FOREIGN KEY (empleado_id)        REFERENCES empleados(id) ON DELETE RESTRICT,
    FOREIGN KEY (creado_por_user_id) REFERENCES usuarios(id)  ON DELETE SET NULL,
    CHECK (fecha_hasta >= fecha_desde)
);

CREATE INDEX IF NOT EXISTS idx_ausencias_empleado_id
    ON ausencias(empleado_id);

CREATE INDEX IF NOT EXISTS idx_ausencias_rango
    ON ausencias(fecha_desde, fecha_hasta);


PRAGMA foreign_keys=OFF;

CREATE TABLE asistencias_new (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    empleado_id             INTEGER NOT NULL,
    fecha                   TEXT    NOT NULL,
    turno_id_aplicado       INTEGER,
    hora_entrada_real       TEXT,
    hora_salida_real        TEXT,
    estado                  TEXT    NOT NULL
        CHECK (estado IN (
            'PRESENTE', 'TARDE', 'SALIDA_TEMPRANA', 'TARDE_Y_SALIDA_TEMPRANA',
            'AUSENTE', 'SIN_TURNO', 'FERIADO', 'INCOMPLETO',
            'VACACIONES', 'DIA_LIBRE', 'PERMISO', 'INCAPACIDAD'
        )),
    minutos_tarde           INTEGER NOT NULL DEFAULT 0
        CHECK (minutos_tarde >= 0),
    minutos_salida_temprana INTEGER NOT NULL DEFAULT 0
        CHECK (minutos_salida_temprana >= 0),
    observaciones           TEXT,
    consolidada_en          TEXT,
    FOREIGN KEY (empleado_id)       REFERENCES empleados(id) ON DELETE RESTRICT,
    FOREIGN KEY (turno_id_aplicado) REFERENCES turnos(id)    ON DELETE SET NULL,
    UNIQUE (empleado_id, fecha)
);

INSERT INTO asistencias_new (
    id, empleado_id, fecha, turno_id_aplicado, hora_entrada_real, hora_salida_real,
    estado, minutos_tarde, minutos_salida_temprana, observaciones, consolidada_en
)
SELECT
    id, empleado_id, fecha, turno_id_aplicado, hora_entrada_real, hora_salida_real,
    estado, minutos_tarde, minutos_salida_temprana, observaciones, consolidada_en
FROM asistencias;

DROP TABLE asistencias;
ALTER TABLE asistencias_new RENAME TO asistencias;

CREATE INDEX IF NOT EXISTS idx_asistencias_empleado_fecha
    ON asistencias(empleado_id, fecha);

CREATE INDEX IF NOT EXISTS idx_asistencias_fecha
    ON asistencias(fecha);

CREATE INDEX IF NOT EXISTS idx_asistencias_estado
    ON asistencias(estado);

PRAGMA foreign_keys=ON;
