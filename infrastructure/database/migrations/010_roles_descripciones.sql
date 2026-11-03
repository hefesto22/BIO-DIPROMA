-- Migración 010_roles_descripciones
-- Actualiza las descripciones de los roles que se muestran en la pestaña
-- "Roles y permisos". La de ADMIN quedó desactualizada cuando la migración
-- 006 le dio el permiso manage_users ("No maneja usuarios ni roles" ya no
-- era cierto). Solo cambia textos: los permisos no se tocan.
--
-- Idempotente: UPDATE con valores fijos.

UPDATE roles
SET description = 'Acceso total al sistema. Es la única cuenta que administra roles.'
WHERE code = 'SUPERADMIN';

UPDATE roles
SET description = 'Gestiona empleados, turnos, configuración y usuarios (excepto al SUPERADMIN).'
WHERE code = 'ADMIN';

UPDATE roles
SET description = 'Descarga y consulta los reportes de asistencia en Excel.'
WHERE code = 'REPORTES';

UPDATE roles
SET description = 'Sincroniza el reloj biométrico y consulta la asistencia en pantalla.'
WHERE code = 'OPERADOR';
