"""Servidor HTTP que recibe las marcas de los relojes en modo PUSH (ADMS).

Protocolo "PUSH" / ADMS de ZKTeco (el que usa ZKBioTime), versión de
asistencia. El reloj es el que se conecta:

    GET  /iclock/cdata?SN=<serie>&options=all…   al encender / reconectar.
         Se le contestan las opciones; la clave es ``ATTLOGStamp``: desde
         dónde debe enviar marcas (``None`` = todo lo que tenga guardado).
    POST /iclock/cdata?SN=<serie>&table=ATTLOG&Stamp=<n>
         Cuerpo: una marca por línea, campos separados por TAB:
         ``PIN  AAAA-MM-DD HH:MM:SS  estado  verificación  …``.
         Se contesta ``OK: <cantidad>``; con cualquier otra respuesta el
         reloj conserva las marcas y las reintenta más tarde.
    GET  /iclock/getrequest?SN=<serie>           latido cada pocos segundos
         (y pedido de comandos: no se le envía ninguno → ``OK``).
    POST /iclock/devicecmd, POST cdata con otras tablas (OPERLOG,
         ATTPHOTO, USERINFO…) → ``OK`` y se descartan.

Acepta también las rutas con ``.aspx``, que usan algunos firmwares.

En el reloj se configura en Menú → Comunicación → "Servidor en la nube" /
ADMS: dirección = IP de esta computadora, puerto = ``PUERTO_PUSH``.

El servidor no conoce la base de datos: le pasa todo a un ``ReceptorPush``
(en la app, ``RecepcionPushService``).
"""

from __future__ import annotations

import logging
import socket
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import List, Optional, Protocol, Sequence
from urllib.parse import parse_qs, urlsplit

from core.services.recepcion_push_service import MarcaPush, Saludo

_log = logging.getLogger(__name__)

# Segundos entre latidos que se le piden al reloj y espera tras un error.
_DELAY = 10
_ERROR_DELAY = 30


class ReceptorPush(Protocol):
    """Lo que el servidor necesita del dominio."""

    def saludo(self, numero_serie: str, ip: str) -> Saludo: ...

    def recibir_marcas(
        self,
        numero_serie: str,
        ip: str,
        marcas: Sequence[MarcaPush],
        stamp: Optional[str] = None,
    ) -> Optional[int]: ...


# ── Formato del protocolo (funciones puras, fáciles de probar) ────────────────


def opciones_para_reloj(numero_serie: str, stamp: Optional[str], zona_horaria: int) -> str:
    """Texto de opciones que se le devuelve al reloj al conectarse."""
    lineas = [
        f"GET OPTION FROM: {numero_serie}",
        f"ATTLOGStamp={stamp or 'None'}",
        # Las operaciones del reloj (altas, borrados…) y las fotos no se usan:
        # 9999 = "no envíes las viejas".
        "OPERLOGStamp=9999",
        "ATTPHOTOStamp=9999",
        f"ErrorDelay={_ERROR_DELAY}",
        f"Delay={_DELAY}",
        "TransTimes=00:00;14:05",
        "TransInterval=1",
        "TransFlag=TransData AttLog",
        f"TimeZone={zona_horaria}",
        "Realtime=1",
        "Encrypt=None",
    ]
    return "\n".join(lineas) + "\n"


def leer_marcas(texto: str) -> List[MarcaPush]:
    """Interpreta el cuerpo de un ``table=ATTLOG``. Ignora las líneas que no entiende."""
    marcas: List[MarcaPush] = []
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea:
            continue
        if "\t" in linea:
            campos = [c.strip() for c in linea.split("\t")]
            pin, fecha_hora, resto = campos[0], campos[1] if len(campos) > 1 else "", campos[2:]
        else:
            # Algunos firmwares separan con espacios: PIN FECHA HORA estado verif…
            partes = linea.split()
            if len(partes) < 3:
                continue
            pin, fecha_hora, resto = partes[0], f"{partes[1]} {partes[2]}", partes[3:]
        try:
            momento = datetime.strptime(fecha_hora, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            _log.debug("Línea de ATTLOG ignorada: %r", linea)
            continue
        estado = int(resto[0]) if len(resto) > 0 and resto[0].isdigit() else 0
        verificacion = int(resto[1]) if len(resto) > 1 and resto[1].isdigit() else 0
        marcas.append(MarcaPush(pin, momento, estado, verificacion))
    return marcas


def zona_horaria_local() -> int:
    """Diferencia en horas de esta computadora con UTC (Honduras: -6)."""
    offset = datetime.now().astimezone().utcoffset()
    return round(offset.total_seconds() / 3600) if offset is not None else 0


def direcciones_de_esta_pc() -> List[str]:
    """IPs (v4) de esta computadora en la red local, la principal primero."""
    ips: List[str] = []
    try:
        # No envía nada: solo pregunta qué interfaz se usaría para salir.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    return [ip for ip in ips if not ip.startswith("127.")]


# ── Servidor ──────────────────────────────────────────────────────────────────


class _Manejador(BaseHTTPRequestHandler):
    receptor: ReceptorPush  # se asigna en una subclase creada por ServidorPush
    server_version = "BIO-DIPROMA"
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 — nombre fijado por http.server
        accion, serie, _qs = self._ruta()
        if accion == "cdata" and serie:
            saludo = self.receptor.saludo(serie, self.client_address[0])
            self._responder(opciones_para_reloj(serie, saludo.stamp, zona_horaria_local()))
        elif accion == "getrequest" and serie:
            self.receptor.saludo(serie, self.client_address[0])
            self._responder("OK")
        else:
            self._responder("OK")

    def do_POST(self) -> None:  # noqa: N802
        accion, serie, qs = self._ruta()
        cuerpo = self._leer_cuerpo()
        tabla = (qs.get("table") or [""])[0].upper()
        if accion != "cdata" or tabla != "ATTLOG" or not serie:
            self._responder("OK")  # fotos, operaciones, respuestas a comandos…
            return
        marcas = leer_marcas(cuerpo)
        stamps = qs.get("Stamp") or qs.get("stamp") or []
        stamp: Optional[str] = stamps[0] if stamps else None
        guardadas = self.receptor.recibir_marcas(serie, self.client_address[0], marcas, stamp)
        if guardadas is None:
            # No registrado: el reloj se queda con las marcas y reintenta.
            self._responder("ERROR: reloj no registrado en BIO-DIPROMA", codigo=403)
        else:
            self._responder(f"OK: {len(marcas)}")

    # ── Utilidades ────────────────────────────────────────────────────────

    def _ruta(self) -> tuple[str, str, dict[str, List[str]]]:
        partes = urlsplit(self.path)
        nombre = partes.path.rstrip("/").rsplit("/", 1)[-1]
        accion = nombre.split(".", 1)[0].lower()  # "cdata.aspx" → "cdata"
        qs = parse_qs(partes.query)
        serie = (qs.get("SN") or qs.get("sn") or [""])[0].strip()
        return accion, serie, qs

    def _leer_cuerpo(self) -> str:
        largo = int(self.headers.get("Content-Length") or 0)
        datos = self.rfile.read(largo) if largo > 0 else b""
        return datos.decode("utf-8", errors="replace")

    def _responder(self, texto: str, codigo: int = 200) -> None:
        datos = texto.encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(datos)))
        self.send_header("Date", self.date_time_string())
        self.end_headers()
        self.wfile.write(datos)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        _log.debug("push %s - %s", self.client_address[0], format % args)


class ServidorPush:
    """Servidor HTTP en un hilo aparte; se enciende cuando hay relojes PUSH."""

    def __init__(self, receptor: ReceptorPush, puerto: int, host: str = "0.0.0.0") -> None:
        self._receptor = receptor
        self.puerto = puerto
        self._host = host
        self._servidor: Optional[ThreadingHTTPServer] = None
        self._hilo: Optional[threading.Thread] = None
        self.error: Optional[str] = None

    @property
    def activo(self) -> bool:
        return self._servidor is not None

    def iniciar(self) -> bool:
        """Empieza a escuchar. Devuelve ``False`` (y deja ``error``) si no pudo."""
        if self._servidor is not None:
            return True
        manejador = type("ManejadorPush", (_Manejador,), {"receptor": self._receptor})
        try:
            servidor = ThreadingHTTPServer((self._host, self.puerto), manejador)
        except OSError as exc:
            self.error = (
                f"No se pudo abrir el puerto {self.puerto} para recibir marcas: {exc}. "
                "¿Hay otro programa (por ejemplo ZKBioTime) usándolo?"
            )
            _log.error(self.error)
            return False
        servidor.daemon_threads = True
        self.puerto = servidor.server_address[1]  # por si se pidió el puerto 0 (tests)
        self._servidor = servidor
        self.error = None
        self._hilo = threading.Thread(
            target=servidor.serve_forever, name="servidor-push", daemon=True
        )
        self._hilo.start()
        _log.info("Recibiendo marcas por push en el puerto %s.", self.puerto)
        return True

    def detener(self) -> None:
        servidor, self._servidor = self._servidor, None
        if servidor is not None:
            servidor.shutdown()
            servidor.server_close()
