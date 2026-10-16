from __future__ import annotations

import json
import os
import threading
import traceback
import uuid
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _log_path() -> Path:
    # Import lazily to avoid turning diagnostics into part of the storage dependency graph.
    try:
        from storage import context as storage_context
        if storage_context.current_project_root():
            root = Path(storage_context.current_project_root()) / "logs"
            root.mkdir(parents=True, exist_ok=True)
            return root / "breadmaker.jsonl"
    except Exception:
        pass
    root = Path(os.environ.get("BREADMAKER_HOME", Path.home() / ".breadmaker-editor")).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root / "breadmaker-app.jsonl"


def log_event(event_id: str, message: str, *, level: str = "INFO", context: dict[str, Any] | None = None,
              error: BaseException | None = None, data_safe: bool = True) -> str:
    """Append a structured diagnostic event and return its unique occurrence id.

    Hosted diagnostics emit only structural event identifiers and exception types.
    Local owner-controlled diagnostics retain details for offline troubleshooting.
    """
    occurrence = uuid.uuid4().hex[:12]
    if str(os.environ.get("BREADMAKER_RUNTIME") or "local").strip().lower() != "local":
        # Security/M12: exception messages, tracebacks and caller context are not
        # safe for cloud logging. A storage provider or parser can interpolate
        # document contents, tokens or paths into all three. Never stringify them.
        safe_event = str(event_id or "")
        if not safe_event.isascii() or not safe_event.replace("_", "").isalnum() or len(safe_event) > 80:
            safe_event = "DIAGNOSTIC_EVENT_REDACTED"
        safe_level = str(level or "INFO").upper()
        if safe_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            safe_level = "ERROR"
        public_row = {
            "time": _now(), "level": safe_level,
            "event_id": safe_event, "occurrence": occurrence,
            "privacy": "STRUCTURAL_ONLY",
        }
        if error is not None:
            public_row["exception"] = type(error).__name__[:100]
        with _LOCK:
            sys.stderr.write(json.dumps(public_row, ensure_ascii=True, separators=(",", ":")) + "\n")
            sys.stderr.flush()
        return occurrence
    # Local diagnostics stay under the user's own project/home directory and
    # preserve detailed tracebacks for offline recovery and debugging.
    row: dict[str, Any] = {
        "time": _now(), "level": level, "event_id": event_id,
        "occurrence": occurrence, "message": message,
        "user_data_safe": bool(data_safe), "context": context or {},
    }
    if error is not None:
        row["exception"] = type(error).__name__
        row["exception_message"] = str(error)[:1000]
        row["traceback"] = "".join(traceback.format_exception(type(error), error, error.__traceback__))[-20000:]
    encoded = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
    path = _log_path()
    with _LOCK:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(encoded)
            fh.flush()
            os.fsync(fh.fileno())
    return occurrence


_STORAGE_CONTEXT_KEYS = frozenset({
    'project_id', 'workspace_project_id', 'generation', 'history_revision',
    'revision_id', 'destination_id', 'provider', 'state_transition',
    'previous_head', 'new_head', 'verification_outcome', 'retry_attempt',
    'queue_depth', 'gc_epoch', 'operation', 'reason', 'scope', 'result',
})
_STORAGE_FORBIDDEN_FRAGMENTS = ('token', 'secret', 'password', 'manuscript', 'content', 'prose', 'asset_bytes')


def log_storage_event(event_id: str, message: str, *, level: str = 'INFO', **context: Any) -> str:
    """Write a privacy-bounded storage/reliability diagnostic event.

    7.7.8 deliberately exposes only an allowlist of identifiers and state-machine
    metadata. Manuscript text, credentials, provider tokens, passwords, and asset
    contents cannot be added accidentally through this API. Unknown keys fail closed
    so new diagnostic fields receive an explicit privacy review before shipping.
    """
    clean: dict[str, Any] = {}
    for raw_key, value in context.items():
        key = str(raw_key)
        lowered = key.lower()
        if any(fragment in lowered for fragment in _STORAGE_FORBIDDEN_FRAGMENTS):
            raise ValueError(f'BREADMAKER_STORAGE_DIAGNOSTIC_FORBIDDEN_FIELD:{key}')
        if key not in _STORAGE_CONTEXT_KEYS:
            raise ValueError(f'BREADMAKER_STORAGE_DIAGNOSTIC_UNKNOWN_FIELD:{key}')
        if value is None:
            clean[key] = None
        elif isinstance(value, (bool, int, float, str)):
            text = value if not isinstance(value, str) else value[:240]
            clean[key] = text
        else:
            clean[key] = str(value)[:240]
    return log_event(event_id, message, level=level, context=clean, data_safe=True)
