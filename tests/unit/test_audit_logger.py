"""Tests del AuditLogger.

Usa un fake en memoria de ``IAuditLogWriteRepository`` para aislar el
servicio del SQLite real. Así validamos que el logger rellena
correctamente timestamp + machine_name sin depender del schema.
"""

from __future__ import annotations

from typing import List

from core.models.audit_entry import AuditEntry
from core.repositories.audit_log_repository import IAuditLogWriteRepository
from core.services.audit_logger import AuditLogger


class FakeAuditRepo(IAuditLogWriteRepository):
    """Colecciona las entradas insertadas en memoria."""

    def __init__(self) -> None:
        self.entradas: List[AuditEntry] = []
        self._seq = 0

    def insert(self, entry: AuditEntry) -> AuditEntry:
        self._seq += 1
        nuevo = AuditEntry(
            id=self._seq,
            user_id=entry.user_id,
            action=entry.action,
            machine_name=entry.machine_name,
            timestamp=entry.timestamp,
            details=entry.details,
        )
        self.entradas.append(nuevo)
        return nuevo


def test_log_rellena_timestamp_y_machine_name() -> None:
    repo = FakeAuditRepo()
    logger = AuditLogger(repo, machine_name="PC-TEST")
    entrada = logger.log("login_ok", user_id=7)

    assert entrada.id == 1
    assert entrada.action == "login_ok"
    assert entrada.user_id == 7
    assert entrada.machine_name == "PC-TEST"
    # Timestamp ISO-8601 UTC.
    assert "T" in entrada.timestamp
    assert entrada.timestamp.endswith("+00:00")


def test_log_user_id_null_permitido() -> None:
    repo = FakeAuditRepo()
    logger = AuditLogger(repo, machine_name="PC-TEST")
    entrada = logger.log("login_fail", user_id=None, details='{"x":"y"}')
    assert entrada.user_id is None
    assert entrada.details == '{"x":"y"}'


def test_machine_name_default_viene_de_socket() -> None:
    """Si no pasamos machine_name, el logger captura gethostname()."""
    repo = FakeAuditRepo()
    logger = AuditLogger(repo)  # sin override
    assert logger.machine_name  # no-empty string


def test_entradas_se_acumulan() -> None:
    repo = FakeAuditRepo()
    logger = AuditLogger(repo, machine_name="PC-TEST")
    logger.log("a", user_id=1)
    logger.log("b", user_id=2)
    logger.log("c", user_id=None)
    assert [e.action for e in repo.entradas] == ["a", "b", "c"]
    assert [e.id for e in repo.entradas] == [1, 2, 3]
