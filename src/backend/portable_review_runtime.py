from __future__ import annotations

"""Process-local state for standalone and temporary Portable Review sessions."""

import os
import threading
from dataclasses import dataclass
from pathlib import Path

from backend.environment import ROOT
from storage import values as storage_values

_PORTABLE_REVIEW_PATH = Path(os.environ['BREADMAKER_PORTABLE_REVIEW']).expanduser().resolve() if os.environ.get('BREADMAKER_PORTABLE_REVIEW') else None
_PORTABLE_REVIEW_PASSPHRASE: str | None = None

@dataclass
class _TemporaryPortableReviewState:
    path: Path
    root: Path
    filename: str
    passphrase: str | None = None
    dirty: bool = False

_TEMP_PORTABLE_REVIEWS: dict[str,_TemporaryPortableReviewState] = {}
_TEMP_PORTABLE_REVIEW_LOCK = threading.RLock()
_PORTABLE_INSTANCE_ID = os.environ.get('BREADMAKER_INSTANCE_ID') or storage_values.sha(str(ROOT.resolve()))[:16]


def _current_portable_review_path() -> Path | None:
    return _PORTABLE_REVIEW_PATH

current_portable_review_path=_current_portable_review_path


def current_portable_review_passphrase() -> str | None:
    return _PORTABLE_REVIEW_PASSPHRASE


def set_portable_review_passphrase(value:str|None) -> None:
    global _PORTABLE_REVIEW_PASSPHRASE
    _PORTABLE_REVIEW_PASSPHRASE=value
