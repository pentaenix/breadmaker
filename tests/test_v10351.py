from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]




def test_v10351_repeated_timeout_modules_use_existing_no_contention_lane():
    source = (ROOT / "scripts/verify_release.py").read_text(encoding="utf-8")
    for name in ("test_v700.py", "test_v703.py", "test_v705.py", "test_v720.py", "test_v1024.py", "test_v1030.py"):
        assert f'"{name}"' in source
    assert 'run_stage("Core / API serial", serial_core_jobs, 1)' in source
    conftest = (ROOT / "tests/conftest.py").read_text(encoding="utf-8")
    assert "CORE_API_TEST_TIMEOUT_SECONDS = 10.0" in conftest


def test_v10351_find_reports_pending_instead_of_false_zero_results():
    source = (ROOT / "static/js/features/manuscript/find_replace.js").read_text(encoding="utf-8")
    assert "$('#findCounter').textContent='… / …'" in source
    assert "setAttribute('aria-busy','true')" in source
    assert "state.findRequestSeq++" in source
    html = (ROOT / "static/index.html").read_text(encoding="utf-8")
    assert 'id="findCounter" class="find-counter" role="status" aria-live="polite"' in html
