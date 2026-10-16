from __future__ import annotations

from pathlib import Path

from source_contracts import backend_source, reviews_source, durability_source

ROOT=Path(__file__).resolve().parents[1]


def _version_tuple(value:str) -> tuple[int,int,int]:
    parts=value.strip().split('.')
    assert len(parts)==3 and all(part.isdigit() for part in parts)
    return tuple(int(part) for part in parts)


def test_v1012_backend_source_boundary_is_real_string_not_generator_comment_regression():
    assert _version_tuple((ROOT/'VERSION').read_text(encoding='utf-8')) >= (10,1,2)
    source=backend_source(ROOT)
    assert isinstance(source,str) and len(source)>100_000
    assert 'def _current_recovery_mode' in source
    assert '@app.get("/api/book")' in source
    assert '@app.post("/api/backup-destinations")' in source


def test_v1012_reassignable_core_state_is_read_through_accessors():
    app=backend_source(ROOT)
    history=durability_source(ROOT)
    reviews=reviews_source(ROOT)
    assert 'def _current_recovery_mode()' in app
    assert 'def _current_portable_review_path()' in app
    assert '_current_recovery_mode()' in history
    assert '_RECOVERY_MODE' not in history
    assert 'current_portable_review_path()' in reviews
    assert '_PORTABLE_REVIEW_PATH' not in reviews


def test_v1012_refactor_sensitive_tests_patch_canonical_feature_owner():
    history_tests='\n'.join((ROOT/'tests'/name).read_text(encoding='utf-8') for name in (
        'test_v731.py','test_v741.py','test_v775_scheduler_integration.py','test_v776.py',
    ))
    assert 'from api.durability import backup_destinations as backup_destinations_api' in history_tests
    assert 'from api.durability import history as history_api' in history_tests
    assert "monkeypatch.setattr(backup_destinations_api,'_choose_local_directory'" in history_tests
    assert "monkeypatch.setattr(backup_destinations_api,'_queue_destination_reconcile'" in history_tests
    assert "monkeypatch.setattr(history_api,'_history_v2_snapshot'" in history_tests
    assert "monkeypatch.setattr(history_api,'_queue_remote_history'" in history_tests
    security=(ROOT/'tests/test_v9613.py').read_text(encoding='utf-8')
    assert 'from api.reviews import portable as portable_reviews' in security
    assert "monkeypatch.setattr(portable_reviews,'runtime',fake)" in security
