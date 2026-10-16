from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_v81823_unit_suite_is_a_first_class_release_gate():
    verifier = (ROOT / 'scripts' / 'verify_release.py').read_text(encoding='utf-8')
    assert 'UNIT_TESTS = TESTS / "unit"' in verifier
    assert 'collect_exact_unit_job()' in verifier
    assert 'run_stage("Unit", [unit_job], 1)' in verifier
    assert verifier.index('run_stage("Unit", [unit_job], 1)') < verifier.index('run_stage("Core / API"')
    assert 'Unit items:' in verifier
    assert 'Current browser items:' in verifier
    assert 'Durable E2E items:' in verifier


def test_v81823_unit_layer_stays_fast_and_does_not_eagerly_boot_app():
    hooks = (ROOT / 'tests' / 'conftest.py').read_text(encoding='utf-8')
    unit_files = sorted((ROOT / 'tests' / 'unit').glob('test_*.py'))
    assert len(unit_files) >= 6
    assert 'UNIT_TEST_TIMEOUT_SECONDS = 3.0' in hooks
    assert "if 'unit' in path.parts and 'tests' in path.parts:" in hooks
    assert 'return UNIT_TEST_TIMEOUT_SECONDS' in hooks
    assert 'def _load_app_module():' in hooks
    prefix = hooks.split('def _load_app_module():', 1)[0]
    assert 'import app as app_module' not in prefix
    for path in unit_files:
        source = path.read_text(encoding='utf-8')
        assert 'TestClient' not in source
        assert 'playwright' not in source.lower()
        assert 'import app' not in source


def test_v81823_testing_docs_define_the_four_layer_regression_model():
    testing = (ROOT / 'docs' / 'TESTING.md').read_text(encoding='utf-8')
    architecture = (ROOT / 'ARCHITECTURE.md').read_text(encoding='utf-8')
    for phrase in ('Unit', 'Core / API', 'Current browser', 'Durable E2E'):
        assert phrase in testing
    assert '3-second' in testing
    assert 'first executable verification gate' in testing
    assert 'Unit → Core/API → Current Browser → Durable E2E' in architecture
