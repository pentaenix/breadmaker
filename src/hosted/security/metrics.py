from __future__ import annotations

"""M12B minimal, opt-in daily usage aggregates. No event stream or text fields.

This is validation-only; no automatic collection/sync or analytics SDK exists.
Daily records must be generated from the user's authorized project server-side
(or from client counts independently checked at a trust boundary).
"""

import re
from dataclasses import dataclass
from typing import Mapping
from types import MappingProxyType

class MetricsValidationError(ValueError):
    pass

_FIELDS = frozenset({"words", "characters", "estimated_pages", "projects", "active_minutes"})
MAX = {"words": 100_000_000, "characters": 1_000_000_000, "estimated_pages": 10_000_000,
       "projects": 100_000, "active_minutes": 1_440}

@dataclass(frozen=True)
class DailyUsageMetrics:
    user_id: str
    date_utc: str
    values: Mapping[str, int]

    @classmethod
    def from_counts(cls, *, user_id: str, date_utc: str, counts: Mapping[str, int]):
        if not isinstance(user_id, str) or not re.fullmatch(r"usr_[0-9a-f]{32}", user_id):
            raise MetricsValidationError("HOSTED_METRICS_USER_INVALID")
        if not isinstance(date_utc, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_utc):
            raise MetricsValidationError("HOSTED_METRICS_DATE_INVALID")
        try:
            from datetime import date
            if date.fromisoformat(date_utc).isoformat() != date_utc:
                raise ValueError()
        except ValueError:
            raise MetricsValidationError("HOSTED_METRICS_DATE_INVALID") from None
        if not isinstance(counts, Mapping) or set(counts) != _FIELDS:
            raise MetricsValidationError("HOSTED_METRICS_FIELDS_INVALID")
        for key, value in counts.items():
            if type(value) is not int or not 0 <= value <= MAX[key]:
                raise MetricsValidationError("HOSTED_METRICS_COUNT_INVALID")
        return cls(user_id=user_id, date_utc=date_utc, values=MappingProxyType(dict(counts)))

    def document_path(self) -> tuple[str, ...]:
        return ("users", self.user_id, "usage_daily", self.date_utc)

    def public(self) -> dict[str, object]:
        return {"schema": "bm-usage-daily-v1", "date_utc": self.date_utc, **self.values}
