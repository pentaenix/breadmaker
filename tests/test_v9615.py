from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]




def test_v9615_scheduler_reconcile_test_resets_process_global_coordinator_state():
    test=(ROOT/'tests/test_v775_scheduler_integration.py').read_text(encoding='utf-8')
    marker='def test_v775_destination_reconcile_failure_never_marks_local_recovery_failed'
    start=test.index(marker)
    end=test.find('\ndef ',start+len(marker))
    body=test[start:end if end!=-1 else len(test)]
    assert 'recovery_runtime._reset_recovery_runtime_state()' in body


