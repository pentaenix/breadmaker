from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]




def test_v10345_durable_e2e_has_a_no_contention_lane_and_scale_runs_first():
    source = (ROOT / 'scripts/verify_release.py').read_text(encoding='utf-8')
    assert "durable_browser_jobs=[job for job in browser_jobs if job.category=='durable E2E']" in source
    assert "current_browser_jobs=[job for job in browser_jobs if job.category=='current browser']" in source
    assert 'run_stage("Durable E2E", durable_browser_jobs, 1)' in source
    assert 'run_stage("Current browser", current_browser_jobs, max(1, args.browser_workers))' in source
    assert source.index('run_stage("Core / API scale", scale_jobs, 1)') < source.index('run_stage("Core / API", ordinary_core_jobs')
    # Preserve the user's hard per-item limit; reliability is achieved by removing
    # verifier contention, never by silently stretching the core/API budget.
    conftest = (ROOT / 'tests/conftest.py').read_text(encoding='utf-8')
    assert 'CORE_API_TEST_TIMEOUT_SECONDS = 10.0' in conftest


def test_v10345_orphan_publication_failure_is_named_explicitly():
    model = (ROOT / 'docs/HOSTED_DATA_MODEL_V1.json').read_text(encoding='utf-8').lower()
    assert 'orphan gcs immutable object' in model
