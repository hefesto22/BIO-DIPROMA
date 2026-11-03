"""Tests de los helpers de presentación de Usuarios y roles (sin Tk)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.models import permissions as perms
from core.models.usuario import Usuario
from ui.components import usuarios_format as fmt

AHORA = datetime(2026, 10, 31, 22, 0, tzinfo=timezone.utc)


def _usuario(**kw: object) -> Usuario:
    base: dict[str, object] = dict(
        id=1, username="ana", password_hash="h", full_name="Ana Lara", role_id=2
    )
    base.update(kw)
    return Usuario(**base)  # type: ignore[arg-type]


# ── Estado ────────────────────────────────────────────────────────────────────


def test_estado_activo() -> None:
    assert fmt.estado_usuario(_usuario(), AHORA) == fmt.ESTADO_ACTIVO


def test_estado_bloqueado_mientras_no_vence() -> None:
    hasta = (AHORA + timedelta(minutes=10)).isoformat()
    u = _usuario(locked_until=hasta)
    assert fmt.estado_usuario(u, AHORA) == fmt.ESTADO_BLOQUEADO
    assert fmt.bloqueado_hasta(u, AHORA) == AHORA + timedelta(minutes=10)


def test_bloqueo_vencido_se_muestra_activo() -> None:
    u = _usuario(locked_until=(AHORA - timedelta(minutes=1)).isoformat())
    assert fmt.estado_usuario(u, AHORA) == fmt.ESTADO_ACTIVO
    assert fmt.bloqueado_hasta(u, AHORA) is None


def test_inactivo_tiene_prioridad_sobre_bloqueado() -> None:
    u = _usuario(is_active=False, locked_until=(AHORA + timedelta(hours=1)).isoformat())
    assert fmt.estado_usuario(u, AHORA) == fmt.ESTADO_INACTIVO


def test_locked_until_invalido_no_rompe() -> None:
    assert fmt.estado_usuario(_usuario(locked_until="basura"), AHORA) == fmt.ESTADO_ACTIVO


# ── Fechas ────────────────────────────────────────────────────────────────────


def test_hace_sin_dato() -> None:
    assert fmt.hace(None, AHORA) == "Nunca"


def test_hace_minutos() -> None:
    assert fmt.hace((AHORA - timedelta(minutes=5)).isoformat(), AHORA) == "Hace 5 min"
    assert fmt.hace((AHORA - timedelta(seconds=20)).isoformat(), AHORA) == "Hace un momento"


def test_hace_dias_viejos_muestra_fecha() -> None:
    texto = fmt.hace("2026-09-01T12:00:00+00:00", AHORA)
    assert texto.count("/") == 2 and texto.endswith("2026")


def test_fecha_hora_local_formato() -> None:
    texto = fmt.fecha_hora_local("2026-10-31T16:13:00+00:00")
    assert len(texto) == len("31/10/2026 16:13")
    assert fmt.fecha_hora_local(None) == "—"


# ── Iniciales ─────────────────────────────────────────────────────────────────


def test_iniciales() -> None:
    assert fmt.iniciales("Ana Gabriela Lara") == "AG"
    assert fmt.iniciales("admin") == "AD"
    assert fmt.iniciales("   ") == "?"


# ── Permisos y acciones ───────────────────────────────────────────────────────


def test_la_matriz_cubre_todos_los_permisos_del_sistema() -> None:
    assert {p.code for p in fmt.PERMISOS_UI} == set(perms.ALL_PERMISSIONS)


def test_etiqueta_accion_conocida_y_respaldo() -> None:
    assert fmt.etiqueta_accion("login_ok") == "Inició sesión"
    assert fmt.etiqueta_accion("algo_nuevo.raro") == "Algo nuevo raro"


# ── Contraseña ────────────────────────────────────────────────────────────────


def test_evaluar_password_refleja_la_politica() -> None:
    reglas = fmt.evaluar_password("abc", 8, require_digit=True, require_letter=True)
    assert [ok for _, ok in reglas] == [False, True, False]

    reglas = fmt.evaluar_password("abcd1234", 8, require_digit=True, require_letter=True)
    assert all(ok for _, ok in reglas)


def test_evaluar_password_letras_unicode_no_cuentan() -> None:
    reglas = fmt.evaluar_password("ñññññ123", 8, require_digit=True, require_letter=True)
    assert dict(reglas)["Al menos una letra (A-Z)"] is False


def test_evaluar_password_sin_reglas_extra() -> None:
    assert len(fmt.evaluar_password("x", 4, require_digit=False, require_letter=False)) == 1
