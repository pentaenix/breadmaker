from __future__ import annotations

"""Cross-cutting storage synchronization boundaries."""

import threading
from contextlib import contextmanager

_LOCK = threading.RLock()


_RECOVERY_BACKUP_LOCK = threading.Lock()


@contextmanager
def project_switch_lock():
    """Serialize active-project changes against every database transaction."""
    with _LOCK:
        yield


@contextmanager
def recovery_privacy_barrier():
    """Serialize destructive privacy reset against built-in Recovery publication."""
    with _RECOVERY_BACKUP_LOCK:
        yield
