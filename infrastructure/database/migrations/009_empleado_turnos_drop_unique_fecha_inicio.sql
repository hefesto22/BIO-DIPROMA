-- 009_empleado_turnos_drop_unique_fecha_inicio.sql — Sub-3.3
--
-- Sub-3.3 cierra el flujo de "turnos múltiples paralelos por
-- empleado". El UNIQUE (empleado_id, fecha_inicio) creado en la
-- migración 003 impedía asignar DOS turnos paralelos que arranquen
-- el mismo día — caso real: un empleado al que hoy mismo le
-- asignamos su turno diurno (Lun-Vie 8-17) Y su turno sabatino
-- (Sáb 8-13), ambos vigentes desde la misma fecha.
--
-- SQLite no soporta DROP CONSTRAINT directo. Estrategia oficial:
--   1. PRAGMA foreign_keys=OFF para desconectar dependencias.
--   2. Crear tabla nueva sin el UNIQUE.
--   3. Copiar todas las filas (preservando ids).
--   4. DROP de la vieja + RENAME.
--   5. Recrear índices auxiliares (los UNIQUE parciales que sí
--      siguen aplicando).
--   6. PRAGMA foreign_keys=ON.
--
-- La invariante "los días no se solapan" sigue validándose en el
-- ``EmpleadoService`` (depende del bitmask del Turno, otra tabla,
-- por eso no se puede expresar como CHECK SQL).

PRAGMA foreign_keys=OFF;

CREATE TABLE empleado_turnos_new (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    empleado_id  INTEGER NOT NULL,
    turno_id     INTEGER NOT NULL,
    fecha_inicio TEXT    NOT NULL,
    fecha_fin    TEXT,
    FOREIGN KEY (empleado_id) REFERENCES empleados(id) ON DELETE RESTRICT,
    FOREIGN KEY (turno_id)    REFERENCES turnos(id)    ON DELETE RESTRICT,
    CHECK (fecha_fin IS NULL OR fecha_fin >= fecha_inicio)
);

INSERT INTO empleado_turnos_new (id, empleado_id, turno_id, fecha_inicio, fecha_fin)
SELECT id, empleado_id, turno_id, fecha_inicio, fecha_fin
FROM empleado_turnos;

DROP TABLE empleado_turnos;
ALTER TABLE empleado_turnos_new RENAME TO empleado_turnos;

CREATE INDEX IF NOT EXISTS idx_empleado_turnos_empleado_id
    ON empleado_turnos(empleado_id);

CREATE INDEX IF NOT EXISTS idx_empleado_turnos_turno_id
    ON empleado_turnos(turno_id);

-- Nota: NO recreamos ``idx_empleado_turnos_vigente_unico``. La
-- migración 008 ya lo había dropeado y Sub-3.3 mantiene esa decisión
-- (un empleado puede tener varias asignaciones vigentes con bitmasks
-- de días disjuntos).

PRAGMA foreign_keys=ON;
