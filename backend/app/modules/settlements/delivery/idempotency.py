"""Idempotent creation of settlement rows (ADR 008 applied to R2).

A row created from a user action carries the action's `client_request_id` and the fingerprint of what
it asked. Repeating the id with the same content returns the original row; with different content it
is `409 idempotency_conflict`. The same contract as the ledger, so a network retry never creates a
second settlement, addition, transfer or expense.
"""

import hashlib
import json
from typing import Any

from app.core.errors import AppError


def fingerprint(actor_id: int, kind: str, payload: dict[str, Any]) -> str:
    """SHA-256 of who asked what; `payload` holds JSON-ready values (decimals as strings)."""
    canonical = json.dumps({"actor": actor_id, "kind": kind, **payload}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def check_replay(stored_fingerprint: str, fingerprint: str, entity: str, entity_id: int) -> None:
    if stored_fingerprint != fingerprint:
        raise AppError(
            409,
            "idempotency_conflict",
            "Ese identificador de solicitud ya se usó para una carga distinta.",
            {entity: entity_id},
        )
