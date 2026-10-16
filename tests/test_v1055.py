"""M11 Editor PR gate source contract. User-run verifier owns execution."""
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def test_v1055_editor_ci_is_credential_free_and_requires_full_verification():
    workflow=(ROOT/'.github/workflows/verify.yml').read_text(encoding='utf-8')
    assert 'editor-full-suite:' in workflow
    assert 'pull_request:' in workflow and 'branches: [main]' in workflow
    assert 'python-version: "3.14"' in workflow
    assert 'playwright install --with-deps chromium' in workflow
    assert './verify-release' in workflow
    assert 'id-token: write' not in workflow
    assert 'google-github-actions/auth' not in workflow
    assert 'gcloud' not in workflow
