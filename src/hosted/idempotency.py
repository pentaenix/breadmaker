from __future__ import annotations

"""Request-scoped *identity*, never request-scoped authority.

A browser may retry a request after Cloud Run disappears. When it supplies an
Idempotency-Key, the canonical resource/operation IDs must be reproducible on a
new instance. Firestore's existing per-resource create fingerprint and CAS are
still the authority: no in-memory response cache is consulted for correctness.
"""

import hashlib
import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator

_KEY = re.compile(r"^[\x21-\x7e]{1,128}$")


@dataclass
class RequestOperations:
    scope: str
    ordinal: int = 0


_ACTIVE: ContextVar[RequestOperations | None] = ContextVar("breadmaker_hosted_request_operations", default=None)


def request_scope_key(raw: str, *, actor: str, method: str, path: str) -> str:
    """Reject ambiguous keys; bind reuse to one actor, method and target URL."""
    if not _KEY.fullmatch(raw) or any(c in raw for c in (",", "\\", '"')):
        raise ValueError("HOSTED_IDEMPOTENCY_KEY_INVALID")
    if not actor or not method or not path:
        raise ValueError("HOSTED_IDEMPOTENCY_SCOPE_INVALID")
    material = json.dumps([actor, method.upper(), path, raw], separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@contextmanager
def bind_operations(scope: str | None) -> Iterator[None]:
    token = _ACTIVE.set(RequestOperations(scope) if scope else None)
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def operation_for(kind: str) -> str | None:
    """Assign a deterministic operation per logical step; no process-global counter."""
    current = _ACTIVE.get()
    if current is None:
        return None
    ordinal = current.ordinal
    current.ordinal += 1
    value = json.dumps([current.scope, kind, ordinal], separators=(",", ":"), ensure_ascii=True)
    return "op_" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_entity_id(prefix: str, operation: str) -> str:
    return str(prefix) + hashlib.sha256(operation.encode("ascii")).hexdigest()[:24]


def stable_project_id(operation: str) -> str:
    return hashlib.sha256(operation.encode("ascii")).hexdigest()[:32]
