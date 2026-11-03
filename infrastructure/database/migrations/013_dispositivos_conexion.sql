-- 013_dispositivos_conexion.sql — Cómo se conecta cada reloj.
--
-- Para poder usar cualquier reloj ZKTeco (huella, rostro o ambos):
--
--   · `modo`: 'DIRECTO' = la app le pide las marcas al reloj (puerto 4370,
--     lo de siempre); 'PUSH' = el reloj envía las marcas solo a la app
--     (protocolo ADMS / "Servidor en la nube"), típico de los faciales
--     nuevos (SpeedFace, ProFace, SenseFace…).
--   · `clave_comunicacion`: "Comm Key" configurada en el reloj (0 = sin
--     clave). Solo para modo DIRECTO.
--   · `usar_udp`: algunos relojes antiguos solo responden por UDP.
--   · `numero_serie`: con él se reconoce a un reloj PUSH cuando se conecta.
--     Único entre los relojes que lo tienen.
--   · `push_stamp`: marca de agua que envía el reloj PUSH con cada lote de
--     marcas; se le devuelve al reconectarse para que no repita lo ya enviado.
--   · `ultimo_contacto`: ISO-8601 UTC del último contacto de un reloj PUSH.
--
-- Todas las columnas tienen DEFAULT o aceptan NULL: los relojes existentes
-- quedan como DIRECTO, sin clave y por TCP, igual que antes.

ALTER TABLE dispositivos ADD COLUMN modo TEXT NOT NULL DEFAULT 'DIRECTO'
    CHECK (modo IN ('DIRECTO', 'PUSH'));
ALTER TABLE dispositivos ADD COLUMN clave_comunicacion INTEGER NOT NULL DEFAULT 0
    CHECK (clave_comunicacion BETWEEN 0 AND 999999);
ALTER TABLE dispositivos ADD COLUMN usar_udp INTEGER NOT NULL DEFAULT 0
    CHECK (usar_udp IN (0, 1));
ALTER TABLE dispositivos ADD COLUMN numero_serie TEXT;
ALTER TABLE dispositivos ADD COLUMN push_stamp TEXT;
ALTER TABLE dispositivos ADD COLUMN ultimo_contacto TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS ux_dispositivos_numero_serie
    ON dispositivos(numero_serie) WHERE numero_serie IS NOT NULL;
