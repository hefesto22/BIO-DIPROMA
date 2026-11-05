-- 014_almuerzo_y_admin_total.sql — Hora de almuerzo en los turnos y
-- Administrador con acceso total.
--
-- 1) Almuerzo con hora (de 12:00 a 13:00) en vez de solo minutos.
--
--   · `almuerzo_inicio` / `almuerzo_fin` ("HH:MM") en `turnos` (horario
--     principal) y en `turno_horarios` (horarios alternos). NULL = sin
--     almuerzo. `minutos_descanso` se sigue guardando: la app lo calcula
--     desde las dos horas (fin − inicio) y lo usan las vistas y los totales.
--   · Turnos que ya tenían descanso: se les pone almuerzo desde las 12:00
--     por los mismos minutos, si cabe dentro del horario (p. ej. 09:00–17:00
--     con 60 min → 12:00–13:00). Los que no caben (turnos de noche o que
--     terminan antes de la 1) quedan con sus minutos y sin hora; al
--     editarlos el formulario sugiere una.
--
-- 2) El rol ADMIN pasa a tener los mismos permisos que SUPERADMIN
--    (también `manage_roles`). La app sigue sin dejar que un ADMIN cree o
--    modifique la cuenta del SUPERADMIN, que es única.

ALTER TABLE turnos ADD COLUMN almuerzo_inicio TEXT;
ALTER TABLE turnos ADD COLUMN almuerzo_fin TEXT;
ALTER TABLE turno_horarios ADD COLUMN almuerzo_inicio TEXT;
ALTER TABLE turno_horarios ADD COLUMN almuerzo_fin TEXT;

UPDATE turnos
SET almuerzo_inicio = '12:00',
    almuerzo_fin = substr(time('12:00', '+' || minutos_descanso || ' minutes'), 1, 5)
WHERE minutos_descanso > 0
  AND minutos_descanso < 720
  AND cruza_medianoche = 0
  AND hora_entrada <= '12:00'
  AND substr(time('12:00', '+' || minutos_descanso || ' minutes'), 1, 5) <= hora_salida;

UPDATE turno_horarios
SET almuerzo_inicio = '12:00',
    almuerzo_fin = substr(time('12:00', '+' || minutos_descanso || ' minutes'), 1, 5)
WHERE minutos_descanso > 0
  AND minutos_descanso < 720
  AND cruza_medianoche = 0
  AND hora_entrada <= '12:00'
  AND substr(time('12:00', '+' || minutos_descanso || ' minutes'), 1, 5) <= hora_salida;

UPDATE roles
SET permissions_json = (SELECT permissions_json FROM roles WHERE code = 'SUPERADMIN')
WHERE code = 'ADMIN';

UPDATE roles
SET description = 'Acceso total al sistema. Es la cuenta de quien instala y da soporte.'
WHERE code = 'SUPERADMIN';

UPDATE roles
SET description = 'Acceso total al sistema: empleados, turnos, relojes, reportes, usuarios y roles.'
WHERE code = 'ADMIN';
