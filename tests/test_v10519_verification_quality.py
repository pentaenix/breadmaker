"""M12J: adversarial verifier coverage and fail-closed encrypted authorization.

The human release operator runs these through ./verify-release.  No real GCP,
network, persistence, or Chromium is needed for the test bodies below.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_v10519_no_current_test_module_silently_excluded():
    """Future test_v11 or unclassified test_security files must fail preflight."""
    from scripts import verify_release as verifier

    actual = {p.name for p in (ROOT / "tests").glob("test_v10*.py")}
    selected = {p.name for p in verifier._current_core_paths()}
    selected.update(p.name for p in verifier._current_browser_paths())
    assert actual <= selected, sorted(actual - selected)
    assert "test_v10519_verification_quality.py" in selected
    assert "test_v10519_verification_quality.py" in verifier.SECURITY_QUICK_CORE_MODULES


def test_v10519_javascript_preflight_includes_feature_modules():
    """Syntax preflight cannot stop after checking only static/app.js."""
    from scripts import verify_release as verifier
    source = (ROOT / "scripts/verify_release.py").read_text(encoding="utf-8")
    assert 'scripts = sorted((ROOT / "static").rglob("*.js"))' in source
    assert 'for script in scripts:' in source
    assert '[node, "--check", relative]' in source
    assert '_javascript_syntax_check(node)' in source
    assert (ROOT / "static/js/features/tasks.js").is_file()
    assert (ROOT / "static/js/features/account/identity-platform.js").is_file()
    assert (ROOT / "static/js/features/tasks.js") in (ROOT / "static").rglob("*.js")


def test_v10519_encrypted_store_rejects_implicit_denial_before_touching_bucket():
    """No positive authorization means neither object access nor key operations."""
    from hosted.security.envelope import HostedEnvelopeCodec
    from hosted.storage.encrypted_objects import HostedEncryptedObjectStore
    from hosted.storage.memory import MemoryObjectStore

    class NoStorage(MemoryObjectStore):
        def stat(self, name):
            raise AssertionError("object storage accessed without authorization")

    class NoKms:
        key_resource = (
            'projects/breadmaker-test-123/locations/us-central1/'
            'keyRings/hosted/cryptoKeys/data'
        )

        def wrap(self, data):
            raise AssertionError("KMS accessed without authorization")

        def unwrap(self, data):
            raise AssertionError("KMS accessed without authorization")

    for denied in (None, False, 0, '', {}, []):
        service = HostedEncryptedObjectStore(
            NoStorage(), HostedEnvelopeCodec(NoKms()),
            authorize=lambda _pid, _uid, value=denied: value,
        )
        with pytest.raises(PermissionError, match='HOSTED_ENCRYPTED_NOT_AUTHORIZED'):
            service.publish(
                project_id='a' * 32, user_id='usr_' + 'b' * 32,
                entity='manuscript_block', record_id='block_1',
                operation_id='revision_1', plaintext=b'secret',
            )


def test_v10519_live_runtime_and_local_storage_never_enable_experimental_private_backends():
    """A test harness cannot accidentally treat opt-in encryption as deployed."""
    source = (ROOT / 'src/runtime/hosted.py').read_text(encoding='utf-8')
    local = (ROOT / 'src/runtime/local.py').read_text(encoding='utf-8')
    assert 'HostedEncryptedObjectStore(' not in source
    assert 'HostedPrivateFirestoreFields(' not in source
    assert 'HostedEncryptedRecoveryWriter(' not in source
    assert 'HostedEnvelopeCodec(' not in local
    assert 'google-cloud-kms' not in (ROOT / 'requirements.txt').read_text(encoding='utf-8')
