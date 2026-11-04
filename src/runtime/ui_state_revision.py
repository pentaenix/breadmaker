"""Value-only CAS tokens shared by local and hosted personal UI-state stores."""
from __future__ import annotations

import hashlib
import json
import re


def is_targets_key(key: str | None) -> bool:
    return key == 'targets-v1' or str(key or '').startswith('targets-v1-')


def value_revision(value: object) -> str:
    raw=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def valid_revision(value: object) -> bool:
    return isinstance(value,str) and re.fullmatch(r'[a-f0-9]{64}',value) is not None


def targets_value_size(value: object) -> int:
    return len(json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8'))
