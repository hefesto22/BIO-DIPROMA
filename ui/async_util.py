"""Helper para correr operaciones I/O sin bloquear la UI de customtkinter.

customtkinter hereda el modelo de threading de Tk: los widgets solo
pueden tocarse desde el main thread. Para una operación lenta (bcrypt
verify, apertura de SQLite, futuras llamadas ZKTeco) el patrón es:

    1. Ejecutar la operación en un thread daemon.
    2. Al terminar, volver al main thread vía ``widget.after(0, cb)``
       para entregarle el resultado al callback que actualiza la UI.

Este módulo encapsula ese patrón en una única función para que los
controllers/views no repitan la plomería ni la olviden.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Protocol, TypeVar

_log = logging.getLogger(__name__)

T = TypeVar("T")


class _WidgetWithAfter(Protocol):
    """Contrato estructural mínimo: solo exigimos ``after``.

    Tk y customtkinter exponen ``after(ms, callback)``; no queremos
    importar tkinter aquí para mantener este módulo usable desde
    tests sin display.
    """

    def after(self, ms: int, func: Callable[[], Any]) -> str:
        """Programa ``func`` para ejecutarse tras ``ms`` milisegundos."""
        ...


def run_async_ui(
    widget: _WidgetWithAfter,
    work: Callable[[], T],
    on_success: Callable[[T], None],
    on_error: Callable[[BaseException], None],
) -> threading.Thread:
    """Ejecuta ``work`` en un thread daemon y despacha el resultado al UI thread.

    Args:
        widget: Cualquier widget Tk — se usa solo su ``after`` para volver
            al main thread. Típicamente la ventana raíz o el frame actual.
        work: Callable sin argumentos que hace el trabajo pesado.
        on_success: Callback invocado en el UI thread con el resultado.
        on_error: Callback invocado en el UI thread con la excepción.
            Se captura ``BaseException`` porque las views NO deben dejar
            que un error tumbe el hilo silenciosamente; el caller decide
            qué hacer (mostrar en UI, cerrar app, etc.).

    Returns:
        El thread lanzado. Se expone para tests que necesiten ``join()``.
    """

    def _target() -> None:
        try:
            resultado = work()
        except BaseException as exc:  # noqa: BLE001 — se re-despacha al UI
            # Python limpia ``exc`` al salir del except — bind aparte.
            error_capturado: BaseException = exc
            _log.debug("run_async_ui: work() lanzó %s", type(error_capturado).__name__)
            widget.after(0, lambda: on_error(error_capturado))
            return
        widget.after(0, lambda: on_success(resultado))

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    return thread
