-- 005_reportes.sql — Sub-3.5a — Historial de descargas de reportes Excel.
--
-- Crea:
--   - Tabla `descargas_reportes`: una fila por archivo .xlsx generado.
--   - Índices de lookup frecuente (orden por fecha desc + filtro por user).
--
-- Permisos: ya existen `export_reports` y `view_export_history` en
-- 002_auth.sql. Esta migración NO añade permisos nuevos.
--
-- Política de idempotencia: CREATE TABLE / INDEX usan IF NOT EXISTS —
-- re-corrida accidental es safe.


-- ── Descargas de reportes ──────────────────────────────────────────────────
-- Registro inmutable (append-only desde el código) de cada export que
-- terminó OK. Si la generación falla NO se inserta — la tabla refleja
-- archivos que efectivamente existieron en disco al momento de generar.
--
-- `user_id` usa ON DELETE SET NULL para preservar el historial aunque el
-- usuario que descargó el reporte se elimine (política análoga a
-- audit_log y sincronizaciones.iniciada_por_user_id).
--
-- `empleado_id_filtro` es NULL cuando el reporte cubrió a todos los
-- empleados activos. ON DELETE SET NULL para que borrar un empleado no
-- destruya el historial — solo pierde el detalle del filtro aplicado.
--
-- `tipo_reporte` queda como CHECK con un solo valor por ahora; futuras
-- migraciones añadirán otros tipos (ej. 'INDIVIDUAL', 'POR_DEPARTAMENTO').
-- El enum vive también en core/models/descarga_reporte.py — si se agrega
-- un valor allá, debe agregarse acá y viceversa.

CREATE TABLE IF NOT EXISTS descargas_reportes (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id            INTEGER,
    fecha_hora_utc     TEXT    NOT NULL,
    tipo_reporte       TEXT    NOT NULL
        CHECK (tipo_reporte IN ('ASISTENCIA')),
    rango_desde        TEXT    NOT NULL,
    rango_hasta        TEXT    NOT NULL,
    empleado_id_filtro INTEGER,
    ruta_archivo       TEXT    NOT NULL,
    filas_exportadas   INTEGER NOT NULL DEFAULT 0
        CHECK (filas_exportadas >= 0),
    FOREIGN KEY (user_id)            REFERENCES usuarios(id)  ON DELETE SET NULL,
    FOREIGN KEY (empleado_id_filtro) REFERENCES empleados(id) ON DELETE SET NULL,
    CHECK (rango_hasta >= rango_desde)
);


-- ── Índices ────────────────────────────────────────────────────────────────
-- Lookups previstos:
--   - Listado de historial reciente (por fecha desc).
--   - Filtro por usuario que generó (auditoría: "qué descargó X").

CREATE INDEX IF NOT EXISTS idx_descargas_reportes_fecha_hora
    ON descargas_reportes(fecha_hora_utc DESC);

CREATE INDEX IF NOT EXISTS idx_descargas_reportes_user_id
    ON descargas_reportes(user_id);
