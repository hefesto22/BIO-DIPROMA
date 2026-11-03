"""Tests del helper ui/async_util.run_async_ui.

Tk no está disponible en el entorno de tests (no hay display), pero
``run_async_ui`` solo usa el método ``.after`` del widget. Lo simulamos
con un fake que ejecuta el callback inmediatamente (síncrono), lo cual
es suficiente para validar el protocolo:
    - work() se ejecuta en un thread daemon
    - on_success recibe el resultado
    - on_error recibe la excepción si work() lanza
"""

from __future__ import annotations

from typing import Any, Callable, List

from ui.async_util import run_async_ui


class FakeWidget:
    """Widget mínimo: .after() ejecuta el callback inmediatamente."""

    def __init__(self) -> None:
        self.calls: List[Callable[[], Any]] = []

    def after(self, ms: int, func: Callable[[], Any]) -> str:
        # Guardamos y ejecutamos para simular el "vuelta al UI thread".
        self.calls.append(func)
        func()
        return "fake-id"


def test_work_exitoso_despacha_on_success() -> None:
    widget = FakeWidget()
    recibidos: List[int] = []
    thread = run_async_ui(
        widget,
        work=lambda: 42,
        on_success=recibidos.append,
        on_error=lambda e: None,
    )
    thread.join(timeout=2)
    assert recibidos == [42]


def test_work_que_lanza_excepcion_despacha_on_error() -> None:
    widget = FakeWidget()
    errores: List[BaseException] = []
    thread = run_async_ui(
        widget,
        work=_work_que_falla,
        on_success=lambda x: None,
        on_error=errores.append,
    )
    thread.join(timeout=2)
    assert len(errores) == 1
    assert isinstance(errores[0], RuntimeError)


def test_thread_es_daemon() -> None:
    widget = FakeWidget()
    thread = run_async_ui(
        widget,
        work=lambda: None,
        on_success=lambda x: None,
        on_error=lambda e: None,
    )
    thread.join(timeout=2)
    assert thread.daemon is True


def _work_que_falla() -> None:
    raise RuntimeError("boom")
