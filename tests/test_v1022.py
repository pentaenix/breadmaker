from __future__ import annotations

from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def _version_tuple(value:str) -> tuple[int,int,int]:
    parts=value.strip().split('.')
    assert len(parts)==3 and all(part.isdigit() for part in parts)
    return tuple(int(part) for part in parts)


def test_v1022_release_identity_and_history_monkeypatch_uses_canonical_original():
    assert _version_tuple((ROOT/'VERSION').read_text(encoding='utf-8')) >= (10,2,2)
    source=(ROOT/'tests/test_v775_scheduler_integration.py').read_text(encoding='utf-8')
    assert 'original=backup_api.verified_full_backup' in source
    assert 'original=app_module.verified_full_backup' not in source
    assert "monkeypatch.setattr(backup_api,'verified_full_backup',backup_then_concurrent_edit)" in source


def test_v1022_history_backup_has_one_canonical_feature_owner(app_module_fixture):
    from api.durability import backups as backup_api
    assert callable(backup_api.verified_full_backup)
    assert not hasattr(app_module_fixture,'verified_full_backup')
