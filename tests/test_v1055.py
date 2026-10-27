"""M11 Editor PR gate source contract. User-run verifier owns execution."""
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def test_v1055_editor_ci_is_credential_free_and_requires_full_verification():
    workflow=(ROOT/'.github/workflows/verify.yml').read_text(encoding='utf-8')
    assert 'editor-full-suite:' in workflow
    assert 'pull_request:' in workflow and 'branches: [main]' in workflow
    assert 'python-version: "3.14"' in workflow
    # OS dependencies and browser downloads are separate diagnostic steps;
    # neither may be omitted just to shorten setup.
    assert 'python -m playwright install-deps chromium' in workflow
    assert 'python -m playwright install chromium' in workflow
    early='python -m pytest -q tests/test_v1055.py tests/test_v965.py tests/test_v10560.py'
    assert workflow.index(early) < workflow.index('python -m playwright install-deps chromium')
    assert workflow.index('python -m playwright install chromium') < workflow.index('run: ./verify-release')
    assert './verify-release' in workflow
    assert 'id-token: write' not in workflow
    assert 'google-github-actions/auth' not in workflow
    assert 'gcloud' not in workflow
