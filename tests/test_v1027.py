from __future__ import annotations

from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_v1027_release_identity_and_removed_core_aliases_stay_removed(app_module_fixture):
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (10,2,7)
    assert not hasattr(app_module_fixture,'ReviewSelectionIn')
    assert not hasattr(app_module_fixture,'verified_full_backup')
    assert not hasattr(app_module_fixture,'_queue_destination_reconcile')


def test_v1027_historical_tests_target_canonical_owners():
    review=(ROOT/'tests/test_v8140.py').read_text(encoding='utf-8')
    history='\n'.join((ROOT/'tests'/name).read_text(encoding='utf-8') for name in (
        'test_v774.py','test_v775_scheduler_integration.py','test_v778.py',
    ))
    v1022=(ROOT/'tests/test_v1022.py').read_text(encoding='utf-8')
    assert 'from api.reviews.contracts import ReviewSelectionIn' in review
    assert 'app_module.ReviewSelectionIn' not in review
    assert 'backup_api.verified_full_backup' in history
    assert 'app_module.verified_full_backup' not in history
    assert 'backup_destinations_api._queue_destination_reconcile()' in history
    assert 'app_module._queue_destination_reconcile' not in history
    assert "monkeypatch.setattr(recovery_runtime,'verified_full_backup',fake_full)" in history
    assert "not hasattr(app_module_fixture,'verified_full_backup')" in v1022
