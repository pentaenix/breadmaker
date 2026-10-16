from __future__ import annotations

import pytest

from revision_store.history_gc import conservative_retention, normalize_history_retention, retention_rank


@pytest.mark.parametrize("raw,expected", [
    (None, "90_days"),
    (" 30_DAYS ", "30_days"),
    ("90_days", "90_days"),
    ("1_YEAR", "1_year"),
    ("FOREVER", "forever"),
])
def test_history_retention_normalization(raw, expected):
    assert normalize_history_retention(raw) == expected


def test_history_retention_rejects_unknown_policy():
    with pytest.raises(ValueError, match="HISTORY_RETENTION_INVALID"):
        normalize_history_retention("2_years")


def test_history_retention_rank_is_monotonic():
    values = ["30_days", "90_days", "1_year", "forever"]
    assert [retention_rank(value) for value in values] == [0, 1, 2, 3]


def test_conservative_retention_chooses_longest_valid_policy():
    assert conservative_retention(["30_days", "garbage", "1_year", "90_days"]) == "1_year"


def test_conservative_retention_uses_default_when_no_explicit_valid_policy_exists():
    # None means "use the configured/default policy" rather than a separate
    # forever policy. Unknown strings are ignored conservatively.
    assert conservative_retention(["garbage", None], default="90_days") == "90_days"
    assert conservative_retention(["garbage"], default="90_days") == "90_days"
