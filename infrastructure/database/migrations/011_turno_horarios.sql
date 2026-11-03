-- 011_turno_horarios.sql — Horarios distintos por día dentro de un turno.
--
-- Caso real: el turno "Administrativo" trabaja de lunes a viernes de
-- 09:00 a 17:00 y el sábado de 09:00 a 12:00. Antes había que crear dos
-- turnos y asignar ambos a cada empleado; ahora es UN turno con dos
-- horarios.
--
-- Modelo:
--   · Las columnas de `turnos` (hora_entrada, hora_salida,
--     minutos_descanso, dias_semana, cruza_medianoche) siguen siendo el
--     horario principal. Los turnos existentes no cambian.
--   · `turno_horarios` guarda los horarios alternos, cada uno con sus
--     propios días. Los días de todos los horarios de un turno son
--     disjuntos; esa regla la valida `TurnoService` (cruza filas de dos
--     tablas, no se puede expresar como CHECK).
--   · ON DELETE CASCADE: los horarios pertenecen al turno.

CREATE TABLE IF NOT EXISTS turno_horarios (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    turno_id         INTEGER NOT NULL,
    dias_semana      INTEGER NOT NULL CHECK (dias_semana BETWEEN 1 AND 127),
    hora_entrada     TEXT    NOT NULL,
    hora_salida      TEXT    NOT NULL,
    minutos_descanso INTEGER NOT NULL DEFAULT 0 CHECK (minutos_descanso >= 0),
    cruza_medianoche INTEGER NOT NULL DEFAULT 0 CHECK (cruza_medianoche IN (0, 1)),
    FOREIGN KEY (turno_id) REFERENCES turnos(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_turno_horarios_turno_id
    ON turno_horarios(turno_id);
