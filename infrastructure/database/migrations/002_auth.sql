-- 002_auth.sql — Esquema de autenticación y auditoría (Fase 1, sub-entregable 1.1).
--
-- Crea:
--   - Tabla `roles` con permisos como JSON (decisión técnica aprobada: Opción A).
--   - Tabla `usuarios` con bloqueo por intentos fallidos.
--   - Tabla `audit_log` append-only (FK SET NULL para preservar historial).
--   - Índices en columnas de lookup frecuente.
--   - Triggers de "SUPERADMIN único" en INSERT y UPDATE (Opción C: defensa en profundidad).
--   - Seed de los 4 roles canónicos con su matriz de permisos.
--
-- Todas las sentencias son idempotentes (IF NOT EXISTS / INSERT OR IGNORE) para
-- sobrevivir a corridas accidentales del runner.


-- ── Tablas ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS roles (
    id               INTEGER PRIMARY KEY,
    code             TEXT    NOT NULL UNIQUE,
    name             TEXT    NOT NULL,
    description      TEXT    NOT NULL DEFAULT '',
    permissions_json TEXT    NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS usuarios (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT    NOT NULL UNIQUE,
    password_hash   TEXT    NOT NULL,
    full_name       TEXT    NOT NULL,
    role_id         INTEGER NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    failed_attempts INTEGER NOT NULL DEFAULT 0 CHECK (failed_attempts >= 0),
    locked_until    TEXT,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER,
    action       TEXT    NOT NULL,
    machine_name TEXT    NOT NULL,
    timestamp    TEXT    NOT NULL,
    details      TEXT,
    FOREIGN KEY (user_id) REFERENCES usuarios(id) ON DELETE SET NULL
);


-- ── Índices ────────────────────────────────────────────────────────────────
-- usuarios(username) ya es UNIQUE por la columna (índice automático).
-- Agregamos índices para lookups frecuentes:

CREATE INDEX IF NOT EXISTS idx_usuarios_role_id     ON usuarios(role_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_user_id    ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp  ON audit_log(timestamp);


-- ── Triggers: SUPERADMIN único ─────────────────────────────────────────────
-- Garantiza que nunca exista más de un usuario con rol SUPERADMIN.
-- Se cubre INSERT y UPDATE (alguien podría intentar promover un ADMIN a SUPERADMIN).
-- El WHEN usa una subquery para encontrar el id del rol SUPERADMIN, lo cual mantiene
-- el trigger válido aunque los IDs del seed cambiaran en el futuro.

CREATE TRIGGER IF NOT EXISTS trg_enforce_single_superadmin_insert
BEFORE INSERT ON usuarios
WHEN NEW.role_id = (SELECT id FROM roles WHERE code = 'SUPERADMIN')
BEGIN
    SELECT CASE
        WHEN (SELECT COUNT(*) FROM usuarios WHERE role_id = NEW.role_id) > 0
        THEN RAISE(ABORT, 'Solo puede existir un SUPERADMIN en el sistema')
    END;
END;

CREATE TRIGGER IF NOT EXISTS trg_enforce_single_superadmin_update
BEFORE UPDATE ON usuarios
WHEN NEW.role_id = (SELECT id FROM roles WHERE code = 'SUPERADMIN')
  AND (OLD.role_id IS NULL OR OLD.role_id != NEW.role_id)
BEGIN
    SELECT CASE
        WHEN (SELECT COUNT(*) FROM usuarios WHERE role_id = NEW.role_id) > 0
        THEN RAISE(ABORT, 'Solo puede existir un SUPERADMIN en el sistema')
    END;
END;


-- ── Seed: 4 roles canónicos ────────────────────────────────────────────────
-- Orden y códigos de permiso deben coincidir con core/models/permissions.py.
-- Matriz según PRD:
--   SUPERADMIN → todos (7/7)
--   ADMIN      → todos menos manage_users (6/7)
--   REPORTES   → solo reportes (2/7)
--   OPERADOR   → sincronización + ver asistencia (2/7)

INSERT OR IGNORE INTO roles (id, code, name, description, permissions_json) VALUES
    (1, 'SUPERADMIN', 'Super Administrador',
     'Acceso total. Gestiona usuarios y roles. Único en el sistema.',
     '["manage_users","manage_settings","manage_employees","run_zkteco_sync","view_attendance","export_reports","view_export_history"]'),
    (2, 'ADMIN', 'Administrador',
     'Gestiona empleados, turnos y configuración. No maneja usuarios ni roles.',
     '["manage_settings","manage_employees","run_zkteco_sync","view_attendance","export_reports","view_export_history"]'),
    (3, 'REPORTES', 'Reportes',
     'Solo descarga y visualiza reportes Excel.',
     '["export_reports","view_export_history"]'),
    (4, 'OPERADOR', 'Operador',
     'Ejecuta sincronización ZKTeco y ve asistencia en pantalla.',
     '["run_zkteco_sync","view_attendance"]');
