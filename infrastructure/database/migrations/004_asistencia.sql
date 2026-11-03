-- 004_asistencia.sql — Esquema de Fase 3 (sincronización ZKTeco + asistencia).
--
-- Crea / altera:
--   - ALTER `turnos` ADD COLUMN minutos_tolerancia_entrada / minutos_tolerancia_salida (D1).
--   - Tabla `dispositivos` (multi-device desde día 1 — D2).
--   - Tabla `feriados` (catálogo plano de días no laborables — D3).
--   - Tabla `sincronizaciones` (cabecera de cada pull, auditable).
--   - Tabla `registros_raw` (lo que vino del device, 1 fila por marcada).
--   - Tabla `asistencias` (consolidado diario por empleado — D4 materializada).
--   - Índices de lookup frecuente + UNIQUE de dedupe defensivo.
--   - NO agrega permisos nuevos: run_zkteco_sync, view_attendance,
--     export_reports y view_export_history ya están seedeados desde
--     002_auth.sql. Feriados y dispositivos caen bajo manage_settings.
--
-- Decisiones referenciadas: D1 (tolerancia por turno), D2 (multi-dispositivo
-- desde día 1), D3 (feriados inline en Sub-3.1), D4 (asistencia materializada).
--
-- Política de idempotencia:
--   - CREATE TABLE / INDEX usan IF NOT EXISTS — re-corrida accidental es safe.
--   - ALTER TABLE NO soporta IF NOT EXISTS en SQLite. Confiamos en el runner
--     (schema_migrations) para que esta migración no se re-ejecute. Si alguien
--     borra manualmente el registro en schema_migrations, el ALTER fallará
--     — lo cual es el comportamiento correcto (contrato roto).


-- ── ALTER turnos: tolerancias ──────────────────────────────────────────────
-- Default 10 min entrada (llegar hasta 10 min tarde es PRESENTE) y 0 salida
-- (ninguna salida temprana tolerada). Valores editables desde la UI de Turnos.
-- Los turnos existentes heredan los defaults al correr esta migración.

ALTER TABLE turnos ADD COLUMN minutos_tolerancia_entrada INTEGER NOT NULL DEFAULT 10
    CHECK (minutos_tolerancia_entrada >= 0);

ALTER TABLE turnos ADD COLUMN minutos_tolerancia_salida INTEGER NOT NULL DEFAULT 0
    CHECK (minutos_tolerancia_salida >= 0);


-- ── Dispositivos ───────────────────────────────────────────────────────────
-- Reloj físico configurado en el sistema. Multi-device desde día 1 (D2).
-- `nombre` y la tupla `(ip, puerto)` son únicos para evitar duplicados.

CREATE TABLE IF NOT EXISTS dispositivos (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre    TEXT    NOT NULL UNIQUE,
    ip        TEXT    NOT NULL,
    puerto    INTEGER NOT NULL DEFAULT 4370
        CHECK (puerto > 0 AND puerto < 65536),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    UNIQUE (ip, puerto)
);


-- ── Feriados ───────────────────────────────────────────────────────────────
-- Catálogo plano de días no laborables. Una fecha = un feriado. Aplican a
-- toda la municipalidad (no hay segmentación por depto/rol — D3).

CREATE TABLE IF NOT EXISTS feriados (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha       TEXT    NOT NULL UNIQUE,
    descripcion TEXT    NOT NULL
);


-- ── Sincronizaciones ───────────────────────────────────────────────────────
-- Cabecera de cada pull desde un dispositivo. Permite auditar quién disparó
-- qué sync, cuándo, con qué rango y resultado. Los registros_raw referencian
-- su sync con ON DELETE CASCADE para que borrar una sync fallida limpie
-- también sus datos crudos.
--
-- `iniciada_por_user_id` usa ON DELETE SET NULL para preservar el historial
-- aunque el usuario que la disparó se elimine (política análoga a audit_log).

CREATE TABLE IF NOT EXISTS sincronizaciones (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    dispositivo_id       INTEGER NOT NULL,
    iniciada_por_user_id INTEGER,
    inicio               TEXT    NOT NULL,
    fin                  TEXT,
    rango_desde          TEXT    NOT NULL,
    rango_hasta          TEXT    NOT NULL,
    registros_recibidos  INTEGER NOT NULL DEFAULT 0
        CHECK (registros_recibidos >= 0),
    estado               TEXT    NOT NULL DEFAULT 'EN_CURSO'
        CHECK (estado IN ('EN_CURSO', 'OK', 'FALLIDA')),
    error_mensaje        TEXT,
    FOREIGN KEY (dispositivo_id)       REFERENCES dispositivos(id) ON DELETE RESTRICT,
    FOREIGN KEY (iniciada_por_user_id) REFERENCES usuarios(id)     ON DELETE SET NULL,
    CHECK (rango_hasta >= rango_desde)
);


-- ── Registros raw ──────────────────────────────────────────────────────────
-- Una fila por marcada recibida del device. `zkteco_user_id` es el ID en el
-- reloj (no el empleado_id local); el mapeo lo hace el servicio de
-- consolidación en Fase 3.3.
--
-- UNIQUE (dispositivo_id, zkteco_user_id, timestamp) = dedupe defensivo: si
-- se re-sincroniza el mismo rango, no se duplican registros. El insert del
-- adapter usa INSERT OR IGNORE.

CREATE TABLE IF NOT EXISTS registros_raw (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    dispositivo_id    INTEGER NOT NULL,
    sincronizacion_id INTEGER NOT NULL,
    zkteco_user_id    INTEGER NOT NULL,
    timestamp         TEXT    NOT NULL,
    tipo_marcada      TEXT    NOT NULL DEFAULT 'UNKNOWN'
        CHECK (tipo_marcada IN (
            'CHECK_IN', 'CHECK_OUT', 'OVERTIME_IN', 'OVERTIME_OUT', 'UNKNOWN'
        )),
    FOREIGN KEY (dispositivo_id)    REFERENCES dispositivos(id)     ON DELETE RESTRICT,
    FOREIGN KEY (sincronizacion_id) REFERENCES sincronizaciones(id) ON DELETE CASCADE,
    UNIQUE (dispositivo_id, zkteco_user_id, timestamp)
);


-- ── Asistencias ────────────────────────────────────────────────────────────
-- Consolidado diario por empleado. Output de la consolidación (Fase 3.3).
-- UNIQUE (empleado_id, fecha) = una sola fila por día — re-consolidaciones
-- deben hacer UPSERT. `turno_id_aplicado` se SET NULL si el turno se borra
-- físicamente alguna vez (no debería, pero cinturón + tirantes).
--
-- Convención de turno nocturno: la fecha es la del día de ENTRADA aunque el
-- turno termine al día siguiente. Ver docstring de core/models/asistencia.py.

CREATE TABLE IF NOT EXISTS asistencias (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    empleado_id             INTEGER NOT NULL,
    fecha                   TEXT    NOT NULL,
    turno_id_aplicado       INTEGER,
    hora_entrada_real       TEXT,
    hora_salida_real        TEXT,
    estado                  TEXT    NOT NULL
        CHECK (estado IN (
            'PRESENTE', 'TARDE', 'SALIDA_TEMPRANA', 'TARDE_Y_SALIDA_TEMPRANA',
            'AUSENTE', 'SIN_TURNO', 'FERIADO', 'INCOMPLETO'
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


-- ── Índices ────────────────────────────────────────────────────────────────
-- Lookups frecuentes previstos en Fase 3:
--   - registros_raw(zkteco_user_id) + (timestamp): query de consolidación
--     escanea por usuario y rango temporal.
--   - registros_raw(sincronizacion_id): rollback/diagnóstico de una sync.
--   - asistencias(empleado_id, fecha): reportes por empleado.
--   - asistencias(fecha): reportes por día/rango.
--   - asistencias(estado): filtros "solo ausentes", "solo tardes".
--   - sincronizaciones(dispositivo_id) + (inicio): historial por reloj.
--   - feriados(fecha): lookup rápido al consolidar.

CREATE INDEX IF NOT EXISTS idx_registros_raw_zkteco_user_id
    ON registros_raw(zkteco_user_id);

CREATE INDEX IF NOT EXISTS idx_registros_raw_timestamp
    ON registros_raw(timestamp);

CREATE INDEX IF NOT EXISTS idx_registros_raw_sincronizacion_id
    ON registros_raw(sincronizacion_id);

CREATE INDEX IF NOT EXISTS idx_asistencias_empleado_fecha
    ON asistencias(empleado_id, fecha);

CREATE INDEX IF NOT EXISTS idx_asistencias_fecha
    ON asistencias(fecha);

CREATE INDEX IF NOT EXISTS idx_asistencias_estado
    ON asistencias(estado);

CREATE INDEX IF NOT EXISTS idx_sincronizaciones_dispositivo_id
    ON sincronizaciones(dispositivo_id);

CREATE INDEX IF NOT EXISTS idx_sincronizaciones_inicio
    ON sincronizaciones(inicio);

CREATE INDEX IF NOT EXISTS idx_feriados_fecha
    ON feriados(fecha);
