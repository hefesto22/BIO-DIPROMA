-- 007_cargos_por_departamento.sql — Sub-3.2.A
--
-- Permite asociar opcionalmente un cargo a un departamento. La regla
-- de negocio (Decisión Sub-3.2.A — Opción A):
--
--   · cargo.departamento_id IS NULL  →  cargo "global": aparece en
--     cualquier departamento al editar empleados (ej. "Auxiliar").
--
--   · cargo.departamento_id = X      →  cargo específico de X: solo
--     aparece cuando el departamento elegido en el form de empleado
--     es X (ej. "Tesorero" solo en "Tesorería").
--
-- Los cargos existentes preservan su semántica actual (nada cambia
-- para ellos) porque la columna nueva es nullable y default NULL.
--
-- ON DELETE: SET NULL — si se borra el departamento (cosa que el
-- sistema no hace; usa archivado), el cargo queda como global en vez
-- de desaparecer. Defensa en profundidad.

ALTER TABLE cargos ADD COLUMN departamento_id INTEGER NULL
    REFERENCES departamentos(id) ON DELETE SET NULL;

-- Índice para resolver rápido "dame los cargos de este departamento"
-- (consulta del dropdown del formulario de empleado).
CREATE INDEX IF NOT EXISTS idx_cargos_departamento_id
    ON cargos(departamento_id);
