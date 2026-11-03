-- 006_manage_roles.sql — Sub-2.7a / módulo Usuarios y Roles.
--
-- Cambios sobre el seed de roles de 002_auth.sql:
--
--   1. Se introduce el permiso `manage_roles` (NUEVO) — habilita editar
--      la matriz de permisos de un rol existente. Solo SUPERADMIN.
--
--   2. Se separa `manage_users` de `manage_roles`:
--        · SUPERADMIN: gana manage_roles (ya tenía manage_users).
--        · ADMIN:      gana manage_users (puede crear/editar usuarios,
--                       pero NO tocar roles ni asignar SUPERADMIN — la
--                       app valida esa última regla en el service).
--        · REPORTES y OPERADOR: sin cambios.
--
-- La migración usa UPDATE directo del JSON en vez de manipulación
-- nativa: SQLite no garantiza el operador json_*; mejor reemplazar el
-- arreglo completo y ser explícito sobre el resultado final.
-- El runner de migraciones aplica este archivo una sola vez (registrado
-- en la tabla schema_migrations).


-- SUPERADMIN: 7 → 8 permisos. Todos los actuales + manage_roles.
UPDATE roles
SET permissions_json = '["manage_users","manage_settings","manage_employees","run_zkteco_sync","view_attendance","export_reports","view_export_history","manage_roles"]'
WHERE code = 'SUPERADMIN';


-- ADMIN: 6 → 7 permisos. Todos los actuales + manage_users.
-- Esto representa una divergencia DELIBERADA del PRD original (que
-- daba "Gestionar usuarios/roles" solo a SUPERADMIN). Decisión
-- aprobada por Mauricio: el ADMIN debe poder gestionar usuarios pero
-- NO roles. La app aplica además R1 (no asignar SUPERADMIN) como
-- defensa adicional en el service.
UPDATE roles
SET permissions_json = '["manage_settings","manage_employees","run_zkteco_sync","view_attendance","export_reports","view_export_history","manage_users"]'
WHERE code = 'ADMIN';


-- REPORTES y OPERADOR no se modifican.
