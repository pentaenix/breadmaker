from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]




def test_v10352_v8173_prewarms_app_without_relaxing_item_watchdog():
    verifier = (ROOT / "scripts/verify_release.py").read_text(encoding="utf-8")
    conftest = (ROOT / "tests/conftest.py").read_text(encoding="utf-8")
    assert 'PREWARM_APP_CORE_MODULES = {' in verifier
    assert '"test_v8173.py"' in verifier
    assert 'env["BREADMAKER_VERIFY_PRELOAD_APP"]="1"' in verifier
    assert 'os.environ.get("BREADMAKER_VERIFY_PRELOAD_APP") == "1"' in conftest
    assert 'CORE_API_TEST_TIMEOUT_SECONDS = 10.0' in conftest


def test_v10352_single_job_progress_is_start_and_finish_only_when_captured():
    verifier = (ROOT / "scripts/verify_release.py").read_text(encoding="utf-8")
    assert 'single_job_log = (not tty and len(jobs) == 1)' in verifier
    assert 'if single_job_log:' in verifier
    assert 'not single_job_log and now - last_heartbeat >= HEARTBEAT_SECONDS' in verifier
    assert "execution job{'s' if len(jobs) != 1 else ''} covering" in verifier
