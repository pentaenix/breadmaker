"""M12B supply-chain preflight: both dependency graphs must be audited before tests.

These are source-contract tests. Only the human operator runs the release verifier.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_v1058_hosted_dependencies_audited_before_any_release_tests():
    script = (ROOT / "verify-release").read_text(encoding="utf-8")
    runner = 'exec "$TEST_PYTHON" scripts/verify_release.py "$@"'
    core = '"$TEST_PYTHON" -m pip_audit --strict --progress-spinner=off --desc=off -r requirements.txt'
    hosted = '"$TEST_PYTHON" -m pip_audit --strict --progress-spinner=off --desc=off -r requirements-hosted.txt'
    assert 0 <= script.index(core) < script.index(hosted) < script.index(runner)
    assert 'if ! ' + core in script
    assert 'if ! ' + hosted in script
    assert 'Breadmaker HOSTED dependency security audit FAILED.' in script
    assert "cat \"$AUDIT_OUTPUT\" >&2" in script


def test_v1058_hosted_audit_requires_no_cloud_login_or_installation():
    script = (ROOT / "verify-release").read_text(encoding="utf-8")
    segment = script.split('# Hosted requirements include', 1)[1].split(
        'exec "$TEST_PYTHON" scripts/verify_release.py', 1
    )[0]
    assert 'requirements-hosted.txt' in segment
    assert 'pip install -r requirements-hosted.txt' not in segment
    assert 'gcloud ' not in segment and 'google.auth' not in segment
    assert '--strict' in segment


def test_v1058_local_runtime_not_changed_to_require_hosted_dependencies():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    hosted_requirements = (ROOT / "requirements-hosted.txt").read_text(encoding="utf-8")
    assert 'google-cloud-kms' not in requirements
    assert '-r requirements.txt' in hosted_requirements
    assert 'google-cloud-kms' in hosted_requirements
    assert 'pip_audit' not in (ROOT / "src/runtime/local.py").read_text(encoding="utf-8")
