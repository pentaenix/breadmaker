from __future__ import annotations

from backups.retention import (
    RECOVERY_RETENTION_POLICIES,
    normalize_recovery_retention_profile,
    recovery_retention_partition,
    retention_authority_key,
)
import pytest


@pytest.mark.parametrize("raw,expected", [
    (None, "standard"),
    (" STANDARD ", "standard"),
    ("compact", "compact"),
    ("EXTENDED", "extended"),
])
def test_recovery_profile_normalization(raw, expected):
    assert normalize_recovery_retention_profile(raw) == expected


def test_recovery_profile_rejects_unknown_value():
    with pytest.raises(ValueError, match="unsupported recovery retention profile"):
        normalize_recovery_retention_profile("infinite")


def test_retention_authority_is_generation_first_not_clock_first():
    newer_clock = {"project_generation": 4, "created_at": "2099-01-01T00:00:00Z", "file": "late.breadmaker"}
    newer_state = {"project_generation": 5, "created_at": "2000-01-01T00:00:00Z", "file": "early.breadmaker"}
    assert retention_authority_key(newer_state) > retention_authority_key(newer_clock)


def test_retention_authority_sanitizes_invalid_generation():
    key = retention_authority_key({"project_generation": "wat", "created_at": "", "file": "x"})
    assert key[0] == 0


def test_recovery_partition_keeps_newest_generations_for_current_workday():
    rows = [
        {"project_generation": g, "created_at": f"2026-09-20T0{g}:00:00Z", "workday": "2026-09-20", "file": f"g{g}"}
        for g in range(1, 7)
    ]
    keep, prune = recovery_retention_partition(rows, "compact")
    assert [row["project_generation"] for row in keep] == [6, 5]
    assert {row["project_generation"] for row in prune} == {1, 2, 3, 4}


def test_recovery_partition_uses_active_workdays_not_elapsed_calendar_days():
    rows = [
        {"project_generation": 4, "created_at": "2026-09-20T10:00:00Z", "workday": "2026-09-20", "file": "g4"},
        {"project_generation": 3, "created_at": "2026-09-10T10:00:00Z", "workday": "2026-09-10", "file": "g3"},
        {"project_generation": 2, "created_at": "2026-08-01T10:00:00Z", "workday": "2026-08-01", "file": "g2"},
        {"project_generation": 1, "created_at": "2025-12-01T10:00:00Z", "workday": "2025-12-01", "file": "g1"},
    ]
    keep, _ = recovery_retention_partition(rows, "compact")
    assert {row["project_generation"] for row in keep} >= {4, 3, 2, 1}


def test_all_retention_profiles_respect_their_hard_cap():
    rows = [
        {"project_generation": g, "created_at": f"2026-01-{(g % 28)+1:02d}T10:00:00Z", "workday": f"2026-01-{(g % 28)+1:02d}", "file": f"g{g}"}
        for g in range(1, 150)
    ]
    for name, policy in RECOVERY_RETENTION_POLICIES.items():
        keep, _ = recovery_retention_partition(rows, name)
        assert len(keep) <= policy.hard_cap
