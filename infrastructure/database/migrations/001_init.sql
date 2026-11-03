-- Migración 001_init
-- Instala la infraestructura de tracking de migraciones.
-- Ninguna tabla de negocio se crea aquí — esas llegan en Fase 1 (auth).
--
-- La tabla schema_migrations registra qué versiones se han aplicado.
-- El runner (infrastructure/database/migrations_runner.py) consulta esta
-- tabla antes de correr cada archivo .sql para evitar re-aplicaciones.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,   -- "001", "002", ... (coincide con prefijo del archivo)
    name       TEXT NOT NULL,      -- nombre descriptivo ("init", "auth", ...)
    applied_at TEXT NOT NULL       -- timestamp ISO 8601 UTC
);
