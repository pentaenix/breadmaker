from __future__ import annotations

from storage import schema as storage_schema

from pathlib import Path

import scripts.verify_release as verify_release
from workspace import APP_SCHEMA_VERSION

ROOT=Path(__file__).resolve().parents[1]


def test_v8185_verifier_keeps_schema_and_runs_durable_e2e_sequentially():
    version=tuple(map(int,ROOT.joinpath('VERSION').read_text().strip().split('.')))
    assert version >= (8,18,5)
    assert APP_SCHEMA_VERSION==storage_schema.SCHEMA_VERSION and APP_SCHEMA_VERSION>=39
    durable=[job for job in verify_release.discover_browser_jobs() if job.category=='durable E2E']
    assert len(durable)==1
    assert durable[0].target.endswith('tests/test_e2e.py')
    assert '::' not in durable[0].target


def test_v8185_console_reporter_is_compact_by_default_and_verbose_is_opt_in():
    source=ROOT.joinpath('scripts','verify_release.py').read_text()
    assert 'FAILURE DIGEST' in source
    assert '--verbose-failures' in source
    assert '_print_failure_digest(all_results)' in source
    assert 'Copy the FAILURE DIGEST above' in source
    assert 'Full tracebacks are stored in verification-report.txt.' in source


def test_v8185_historical_e2e_uses_current_project_menu_vocabulary():
    e2e=ROOT.joinpath('tests','test_e2e.py').read_text()
    assert "#projectModal:not(.hidden)" not in e2e
    assert '#projectMenuSettings' in e2e
    assert '#settingsVerify' in e2e
    assert 'Card · Reference Node' in e2e
    assert "['Bookmark','Split View','Add Chapter Below','Details','Cut','Delete']" in e2e
