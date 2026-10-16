"""M12 adversarial contracts. These run only in the human-owned verifier."""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stderr
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def test_m12_hosted_diagnostics_never_log_exception_values_or_user_context(monkeypatch):
    from backend.diagnostics import log_event
    sentinel = "MY_PRIVATE_MANUSCRIPT_TOKEN_993344"
    monkeypatch.setenv("BREADMAKER_RUNTIME", "hosted")
    out=io.StringIO()
    try:
        raise RuntimeError(sentinel)
    except RuntimeError as error:
        with redirect_stderr(out):
            receipt=log_event("HOSTED_FAILURE_001", "secret " + sentinel,
                              error=error, context={"manuscript":sentinel}, data_safe=True)
    log=out.getvalue()
    assert sentinel not in log and "traceback" not in log and "context" not in log
    record=json.loads(log)
    assert record["occurrence"]==receipt
    assert record["event_id"]=="HOSTED_FAILURE_001"
    assert record["exception"]=="RuntimeError"
    assert record["privacy"]=="STRUCTURAL_ONLY"
    assert "exception_message" not in record and "message" not in record


def test_m12_hosted_diagnostics_do_not_stringify_exceptions(monkeypatch):
    from backend.diagnostics import log_event
    class Dangerous(Exception):
        def __str__(self):
            raise AssertionError("Never stringify hosted exceptions")
    monkeypatch.setenv("BREADMAKER_RUNTIME", "hosted")
    out=io.StringIO()
    with redirect_stderr(out):
        log_event("BLOB_READ_001", "constant event", error=Dangerous())
    assert json.loads(out.getvalue())["exception"]=="Dangerous"


def test_m12_browser_mfa_labels_and_secret_are_text_only():
    source=(ROOT/'static/js/features/account/identity-platform.js').read_text(encoding='utf-8')
    assert "$('#identityTotpDisplayName').textContent" in source
    assert "$('#identityEnrollmentSecret').textContent" in source
    assert "${displayName" not in source and "${enrollment.secretKey}" not in source
    assert 'target.origin!==location.origin' in source
    assert "value.startsWith('//')" in source
    assert "value.startsWith('/')" in source


def test_m12_hosted_validation_does_not_echo_input():
    source=(ROOT/'src/backend/http_boundary.py').read_text(encoding='utf-8')
    assert "if runtime.mode != 'local':" in source
    assert '"type":str(item.get("type")' in source
    assert '"loc":[str(part)' in source
    assert '"params":{"errors":errors}' in source


def test_m12_editor_ci_audits_hosted_dependency_graph():
    workflow=(ROOT/'.github/workflows/verify.yml').read_text(encoding='utf-8')
    assert 'pip_audit --strict' in workflow
    assert '-r requirements-hosted.txt' in workflow
    required=(ROOT/'requirements-hosted.txt').read_text(encoding='utf-8')
    assert '-r requirements.txt' in required
    for name in ('google-cloud-firestore','google-cloud-storage','firebase-admin'):
        assert name+'==' in required


def test_m12_cross_tenant_domain_read_write_fail_closed():
    from hosted.firestore import HostedProjectRepository, HostedStructuredRepository, HostedUserRepository
    from hosted.firestore.contracts import ProjectUnavailable, HostedAuthorizationError
    from hosted.firestore.memory import MemoryDocumentStore
    from hosted.storage.memory import MemoryObjectStore
    from hosted.runtime_services import HostedCloudProjectRepository, ensure_hosted_user
    from hosted.domain_store import HostedDomainStore
    from hosted.firestore.ids import require_user_id
    owner=require_user_id('usr_'+'a'*32)
    outsider=require_user_id('usr_'+'b'*32)
    docs=MemoryDocumentStore();objects=MemoryObjectStore()
    ensure_hosted_user(docs,user_id=owner,display_name='A')
    ensure_hosted_user(docs,user_id=outsider,display_name='B')
    authority=HostedProjectRepository(docs)
    users=HostedUserRepository(HostedStructuredRepository(docs))
    first=HostedCloudProjectRepository(docs,objects,authority,users,lambda:owner)
    pid=first.create('Hidden writing')['id']
    dom=HostedDomainStore(docs,objects)
    task=dom.create('task',{'project_id':pid},{'title':'Private'},user_id=owner)
    for action in (
        lambda: authority.get_for_user(pid,outsider),
        lambda: dom.list('task',{'project_id':pid},user_id=outsider),
        lambda: dom.get('task',{'project_id':pid,'task_id':task['id']},user_id=outsider),
        lambda: dom.update('task',{'project_id':pid,'task_id':task['id']},{'title':'Stolen'},user_id=outsider),
        lambda: dom.delete('task',{'project_id':pid,'task_id':task['id']},user_id=outsider),
    ):
        with pytest.raises((ProjectUnavailable,HostedAuthorizationError)):
            action()
    assert dom.get('task',{'project_id':pid,'task_id':task['id']},user_id=owner)['title']=='Private'


def test_m12_unreviewed_deletion_and_external_identity_revocation_stay_off():
    from hosted.purge_production_gate import HostedProductionPurgeGate, REQUIRED_PROOFS
    assert not HostedProductionPurgeGate.assess(recorded_proofs=frozenset(REQUIRED_PROOFS)).production_deletion_authorized
    from hosted.account_sandbox import HostedSandboxAccountClosure
    assert not hasattr(HostedSandboxAccountClosure,'revoke_external_identity')


def test_m12_operator_can_audit_hosted_transitives_without_running_suite():
    script=(ROOT/'audit-hosted-dependencies').read_text(encoding='utf-8')
    assert 'set -euo pipefail' in script
    assert 'pip_audit --strict' in script
    assert '-r requirements-hosted.txt' in script
    assert 'pytest' not in script and './verify-release' not in script.split('exec .venv/bin/python')[1]
