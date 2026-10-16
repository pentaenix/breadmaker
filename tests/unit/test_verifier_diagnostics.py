"""Pure verifier accounting regressions; no subprocess, browser or application startup."""
from __future__ import annotations

import json
from pathlib import Path

from scripts import verify_release as verifier


def _records(path: Path, rows: list[dict]) -> None:
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')


def test_result_accounting_rejects_setup_only_item(tmp_path):
    file = tmp_path / 'results.jsonl'
    _records(file, [{'nodeid': 'tests/test_x.py::test_x', 'phase': 'setup', 'outcome': 'passed'}])
    executed, passed, failed, skipped, failures = verifier._parse_result_jsonl(file)
    assert (executed, passed, failed, skipped) == (1, 0, 1, 0)
    assert failures[0][2] == 'accounting'


def test_result_accounting_preserves_failed_call_even_if_teardown_passes(tmp_path):
    file = tmp_path / 'results.jsonl'
    node = 'tests/test_x.py::test_x'
    _records(file, [
        {'nodeid': node, 'phase': 'setup', 'outcome': 'passed'},
        {'nodeid': node, 'phase': 'call', 'outcome': 'failed', 'reason': 'a private test error'},
        {'nodeid': node, 'phase': 'teardown', 'outcome': 'passed'},
    ])
    executed, passed, failed, skipped, failures = verifier._parse_result_jsonl(file)
    assert (executed, passed, failed, skipped) == (1, 0, 1, 0)
    assert failures == [(node, 'a private test error', 'call')]


def test_result_accounting_accepts_all_three_passed_phases(tmp_path):
    file = tmp_path / 'results.jsonl'
    node = 'tests/test_x.py::test_x'
    _records(file, [
        {'nodeid': node, 'phase': phase, 'outcome': 'passed'}
        for phase in ('setup', 'call', 'teardown')
    ])
    assert verifier._parse_result_jsonl(file) == (1, 1, 0, 0, [])


def test_durable_resume_does_not_mark_setup_only_success_complete(tmp_path):
    file = tmp_path / 'results.jsonl'
    node_a = 'tests/test_e2e.py::test_a'
    node_b = 'tests/test_e2e.py::test_b'
    _records(file, [
        {'nodeid': node_a, 'phase': 'setup', 'outcome': 'passed'},
        {'nodeid': node_b, 'phase': 'setup', 'outcome': 'failed'},
    ])
    assert verifier._durable_recorded_names(file) == {'test_b'}


def test_new_test_module_cannot_be_silently_omitted(monkeypatch, tmp_path):
    test_dir = tmp_path / 'tests'
    test_dir.mkdir()
    (test_dir / 'test_v500_migration.py').write_text('# intentionally archived\n')
    (test_dir / 'test_v110.py').write_text('def test_future(): pass\n')
    (test_dir / 'test_v6100.py').write_text('def test_future(): pass\n')
    (test_dir / 'test_v500.py').write_text('# archived historical test\n')
    (test_dir / 'test_security_new.py').write_text('def test_new(): pass\n')
    monkeypatch.setattr(verifier, 'TESTS', test_dir)
    monkeypatch.setattr(verifier, '_unit_paths', lambda: [])
    monkeypatch.setattr(verifier, '_current_core_paths', lambda: [])
    monkeypatch.setattr(verifier, '_current_browser_paths', lambda: [])
    # Synthetic discovery must be independent of the real release's historical
    # manifest. The separate inventory contract audits the actual frozen list.
    monkeypatch.setattr(verifier, '_historical_test_modules', lambda: frozenset({'test_v500.py', 'test_v500_migration.py'}))
    assert verifier._untracked_test_modules() == ['test_security_new.py', 'test_v110.py', 'test_v6100.py']


def test_historical_test_manifest_is_explicit_and_frozen():
    """A prefix pattern cannot silently exempt a newly introduced test file."""
    excluded = verifier._historical_test_modules()
    assert 'test_v500.py' in excluded
    assert 'test_v500_migration.py' in excluded
    assert 'test_v110.py' not in excluded
    assert 'test_v6100.py' not in excluded
    assert 'test_v640.py' not in excluded  # active boundary, never archived
    assert all(name.endswith('.py') for name in excluded)


def test_verification_report_ends_with_exact_editor_version(monkeypatch, tmp_path):
    report = tmp_path / 'verification-report.txt'
    monkeypatch.setattr(verifier, 'REPORT', report)
    version = (verifier.ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    verifier._write_report(version, 'FULL', [], True)
    assert report.read_text(encoding='utf-8').splitlines()[-1] == (
        f'Verification target: Breadmaker Editor v{version}')


def test_release_identity_is_a_single_shared_contract(monkeypatch, tmp_path):
    """Synthetic source tree validates sync and verifier using the same policy."""
    from scripts import sync_editor_release_metadata as release

    for relative, pattern, label, count in release.FIELDS:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        # Generate the expected number of tokens using realistic paths, without
        # requiring a network server or booting the Editor.
        token = 'Implemented through **10.5.99**:' if 'IMPLEMENTATION_PLAN' in relative else (
            'breadmaker_logo.svg?v=10.5.99' if label == 'logo cache keys' else (
                "frontend:'10.5.99'" if label == 'bootstrap identity' else (
                    'identity-platform-client.js?v=10.5.99' if 'import' in label else '/static/app.js?v=10.5.99')))
        path.write_text('\n'.join([token] * (2 if label == "logo cache keys" else count)), encoding='utf-8')
    (tmp_path / 'VERSION').write_text('10.5.99\n', encoding='utf-8')
    monkeypatch.setattr(verifier, 'ROOT', tmp_path)
    assert release.validate(tmp_path) == '10.5.99'
    verifier._assert_release_identity()

    css = tmp_path / 'static/css/reviews/workspace.css'
    css.write_text(css.read_text().replace('10.5.99', '10.5.98', 1), encoding='utf-8')
    try:
        verifier._assert_release_identity()
    except AssertionError as exc:
        assert 'static/css/reviews/workspace.css' in str(exc)
    else:
        raise AssertionError('Stale nested version passed the release preflight')

    # Setting a version updates every dependency with no changes to test code.
    assert release.synchronize(tmp_path, new_version='10.5.100') == '10.5.100'
    assert release.validate(tmp_path) == '10.5.100'
    assert css.read_text().count('10.5.100') == 2

    # Invalid bump attempts are transactional: leave previous version intact.
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    try:
        release.synchronize(tmp_path, new_version='garbage')
    except ValueError:
        pass
    else:
        raise AssertionError('An invalid release version was accepted')
    assert before == {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
