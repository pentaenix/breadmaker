"""Actual identity-deletion protocol with isolated providers and failure injection."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from auth.identity_platform import IdentityPlatformAdmissionStore
from hosted.account_closure import StagedAccountClosure
from hosted.account_closure_cli import FirebaseClosureIdentity, required_environment, require_deployed_fence
from hosted.account_lifecycle import AccountLifecycleDocumentStore, AccountLifecycleObjectStore
from hosted.firestore.contracts import DocumentConflict, ProjectUnavailable
from hosted.firestore.memory import MemoryDocumentStore
from hosted.firestore.repositories import HostedProjectRepository
from hosted.runtime_services import HostedCloudAccountIdentityService
from hosted.storage.memory import MemoryObjectStore

PROJECT = 'synthetic-project'
OPERATOR = f'bm-production-erasure@{PROJECT}.iam.gserviceaccount.com'
EMAIL = 'synthetic-writer@gmail.com'
EXTERNAL = 'firebase-disposable-old'
ISSUER = 'https://securetoken.google.com/'+PROJECT
OPERATION = 'synthetic-delete-1'


class Identities:
    def __init__(self):
        self.users = {EXTERNAL: NS(uid=EXTERNAL, disabled=False, email=EMAIL, email_verified=True, provider_data=[])}
        self.events = []
        self.failure = None
        self.refuse_delete = False

    def get(self, uid):
        if self.failure: raise self.failure
        return self.users.get(uid)

    def disable(self, uid):
        self.events.append(('disable', uid))
        if uid in self.users: self.users[uid].disabled = True

    def revoke(self, uid): self.events.append(('revoke', uid))

    def delete(self, uid):
        self.events.append(('delete', uid))
        if not self.refuse_delete: self.users.pop(uid, None)


def fixture(*, projects=2, checkpoint=None):
    docs, live, audit, identities = MemoryDocumentStore(), MemoryObjectStore(), MemoryObjectStore(), Identities()
    accounts = HostedCloudAccountIdentityService(docs)
    identity = accounts.resolve_or_create_external_identity(ISSUER, EXTERNAL, provider='google',
        display_name='Synthetic writer', email=EMAIL, email_verified=True)
    uid = identity.user_id
    grant_path = IdentityPlatformAdmissionStore.greenlist_path(EMAIL)
    docs.run_transaction(lambda tx: tx.create(grant_path, {
        'email':EMAIL, 'status':'active', 'allowed_providers':['google'], 'operator_setting':'preserve-me'}))
    admission = IdentityPlatformAdmissionStore(docs)
    admission.require_or_consume(EXTERNAL, None, email=EMAIL, email_verified=True, provider='google')
    authority = HostedProjectRepository(docs)
    pids = []
    for index in range(projects):
        pid = ('a' if index == 0 else 'b')*32
        authority.create('Synthetic book', owner_user_id=uid, operation_id='create'+str(index), project_id=pid)
        docs.run_transaction(lambda tx: tx.create(('projects',pid,'chapters','chapter1'), {'text':'KEEP THIS SYNTHETIC TEXT'}))
        live.write_chunks(f'projects/{pid}/body', [b'kept'], max_bytes=20, content_type='text/plain', if_generation_match=0)
        pids.append(pid)
    engine = StagedAccountClosure(docs, identities, audit, project=PROJECT,
        now=lambda:datetime(2026,10,28,12,tzinfo=timezone.utc), checkpoint=checkpoint)
    return NS(docs=docs, live=live, audit=audit, identities=identities, accounts=accounts,
              uid=uid, pids=pids, engine=engine, admission=admission, grant_path=grant_path)


def close(world, confirmation=None):
    confirmation = confirmation or world.engine.plan(EMAIL, operation=OPERATION).confirmation
    return world.engine.close(EMAIL, operation=OPERATION, confirmation=confirmation)


def test_v10563_close_deletes_only_exact_identity_keeps_allowlist_and_retained_data():
    w = fixture()
    original = {pid:dict(w.docs.get(('projects',pid,'chapters','chapter1')).data) for pid in w.pids}
    before_objects = tuple((meta.name,meta.generation,meta.sha256) for meta in w.live.list_prefix('projects/'))
    result = close(w)
    assert result['account_closed'] is True and result['data_erasure_complete'] is False
    assert result['physical_purge_authorized'] is False and result['retained_project_count'] == 2
    assert result['retain_until'] == '2026-11-27T12:00:00+00:00'
    assert w.identities.events == [('disable',EXTERNAL),('revoke',EXTERNAL),('delete',EXTERNAL)]
    assert w.identities.get(EXTERNAL) is None
    grant = w.docs.get(w.grant_path).data
    assert grant == {'email':EMAIL, 'status':'active', 'allowed_providers':['google'],
                     'operator_setting':'preserve-me', 'bound_uid':'', 'admitted_at_epoch':grant['admitted_at_epoch']}
    assert not w.admission.is_admitted(EXTERNAL)
    assert w.docs.get(('users',w.uid)).data['status'] == 'erasure_pending'
    for pid in w.pids:
        assert w.docs.get(('projects',pid)).data['status'] == 'erasure_pending'
        assert dict(w.docs.get(('projects',pid,'chapters','chapter1')).data) == original[pid]
    assert tuple((meta.name,meta.generation,meta.sha256) for meta in w.live.list_prefix('projects/')) == before_objects
    assert len(w.audit.list_prefix('erasure-audit/v1/closures/')) == 2


def test_v10563_same_email_fresh_signup_has_new_account_without_old_projects():
    w = fixture()
    confirmation = w.engine.plan(EMAIL, operation=OPERATION).confirmation
    result = close(w, confirmation)
    new_external = 'firebase-new-signup'
    w.identities.users[new_external] = NS(uid=new_external, disabled=False)
    w.admission.require_or_consume(new_external, None, email=EMAIL, email_verified=True, provider='google')
    identity = w.accounts.resolve_or_create_external_identity(ISSUER,new_external,provider='google',
        display_name='Fresh signup',email=EMAIL,email_verified=True)
    assert identity.user_id != w.uid and w.admission.is_admitted(new_external)
    for pid in w.pids:
        with pytest.raises(ProjectUnavailable): HostedProjectRepository(w.docs).get_for_user(pid,identity.user_id)
    previous_calls = list(w.identities.events)
    assert close(w, confirmation) == result
    assert w.identities.events == previous_calls and w.identities.get(new_external) is not None
    assert w.docs.get(w.grant_path).data['bound_uid'] == new_external


@pytest.mark.parametrize('point',['account_fenced','project_fenced','provider_delete_returned','identity_deleted','audit_written'])
def test_v10563_closure_restarts_safely_after_interruption(point):
    fired = []
    def crash(state):
        if state == point and not fired:
            fired.append(state)
            raise RuntimeError('synthetic crash')
    w = fixture(checkpoint=crash)
    confirmation = w.engine.plan(EMAIL, operation=OPERATION).confirmation
    with pytest.raises(RuntimeError,match='synthetic crash'): close(w,confirmation)
    assert w.docs.get(w.grant_path).data['bound_uid'] == EXTERNAL
    restarted = StagedAccountClosure(w.docs,w.identities,w.audit,project=PROJECT,
        now=lambda:datetime(2026,10,28,12,tzinfo=timezone.utc))
    result = restarted.close(EMAIL,operation=OPERATION,confirmation=confirmation)
    assert result['account_closed'] and not result['data_erasure_complete']
    assert w.docs.get(w.grant_path).data['bound_uid'] == ''
    for pid in w.pids:
        assert w.docs.get(('projects',pid,'chapters','chapter1')).data['text']=='KEEP THIS SYNTHETIC TEXT'


def test_v10563_wrong_confirmation_has_no_mutations():
    w = fixture()
    before = deepcopy(w.docs._documents)
    with pytest.raises(DocumentConflict,match='CONFIRMATION_MISMATCH'): close(w,'wrong')
    assert w.docs._documents == before and not w.identities.events
    assert not w.audit.list_prefix('erasure-audit/')


def test_v10563_unconfirmed_provider_deletion_never_releases_email_grant():
    w = fixture()
    w.identities.refuse_delete = True
    with pytest.raises(DocumentConflict,match='DELETE_UNCONFIRMED'): close(w)
    assert w.docs.get(w.grant_path).data['bound_uid'] == EXTERNAL
    assert not any(meta.name.endswith('/identity-deleted.json') for meta in w.audit.list_prefix('erasure-audit/'))


def test_v10563_provider_lookup_outage_after_delete_is_not_absence():
    w = fixture()
    def fail(state):
        if state == 'provider_delete_returned': w.identities.failure = RuntimeError('synthetic provider outage')
    w.engine.checkpoint = fail
    with pytest.raises(RuntimeError,match='provider outage'): close(w)
    assert w.docs.get(w.grant_path).data['bound_uid'] == EXTERNAL
    assert w.identities.users == {}


def test_v10563_forged_runtime_control_row_without_external_intent_is_not_authority():
    w = fixture()
    plan = w.engine.plan(EMAIL,operation=OPERATION)
    w.docs.run_transaction(lambda tx:tx.create(('account_erasures',plan.request_id),{
        'request_id':plan.request_id,'confirmation':plan.confirmation,'project':PROJECT,
        'greenlist_digest':plan.greenlist_digest,'user_id':w.uid,'external_uid':EXTERNAL,
        'state':'closed_retention_pending'}))
    with pytest.raises(DocumentConflict,match='AUDIT_MISMATCH'): close(w,plan.confirmation)
    assert not w.identities.events and w.docs.get(w.grant_path).data['bound_uid'] == EXTERNAL


def test_v10563_shared_projects_and_foreign_provider_bindings_fail_before_closure():
    w = fixture()
    w.docs.run_transaction(lambda tx:tx.create(('projects',w.pids[0],'members','usr_'+'d'*32),
        {'user_id':'usr_'+'d'*32,'role':'editor'}))
    with pytest.raises(DocumentConflict,match='SHARED_PROJECT'): close(w)
    assert w.docs.get(('users',w.uid)).data['status']=='active' and not w.identities.events
    w = fixture()
    w.identities.users[EXTERNAL].email = 'someone-else@gmail.com'
    with pytest.raises(DocumentConflict,match='EMAIL_UNCONFIRMED'): close(w)
    assert not w.identities.events


def test_v10563_changed_grant_or_fence_prevents_privileged_identity_side_effects():
    w = fixture()
    confirmation = w.engine.plan(EMAIL,operation=OPERATION).confirmation
    def change(state):
        if state == 'account_fenced':
            grant = dict(w.docs.get(w.grant_path).data); grant['bound_uid']='a-different-uid'
            w.docs.run_transaction(lambda tx:tx.set(w.grant_path,grant))
    w.engine.checkpoint = change
    with pytest.raises(DocumentConflict,match='FENCE_CHANGED'): close(w,confirmation)
    assert not w.identities.events and w.docs.get(w.grant_path).data['bound_uid']=='a-different-uid'


def test_v10563_firestore_fence_blocks_inflight_writes_project_creation_and_resurrection():
    w = fixture()
    fenced = AccountLifecycleDocumentStore(w.docs,actor=lambda:w.uid)
    close(w)
    path = ('projects',w.pids[0],'chapters','chapter1')
    with pytest.raises(DocumentConflict,match='ERASURE_PENDING'):
        fenced.run_transaction(lambda tx:tx.set(path,{'text':'MUST NOT COMMIT'}))
    with pytest.raises(DocumentConflict,match='ERASURE_PENDING'):
        HostedProjectRepository(fenced).create('Must not create',owner_user_id=w.uid,operation_id='late')
    with pytest.raises(DocumentConflict,match='ERASURE_PENDING'):
        fenced.run_transaction(lambda tx:tx.set(('users',w.uid),{'user_id':w.uid,'status':'active'}))
    assert w.docs.get(path).data['text']=='KEEP THIS SYNTHETIC TEXT'


def test_v10563_new_upload_is_blocked_but_prefreeze_upload_can_finish_without_publication():
    w = fixture()
    fenced = AccountLifecycleDocumentStore(w.docs,actor=lambda:w.uid)
    uploads = AccountLifecycleObjectStore(w.live,fenced)
    original = w.live.write_chunks
    def finish_after_closure(name,chunks,**kwargs):
        close(w)
        return original(name,chunks,**kwargs)
    w.live.write_chunks = finish_after_closure
    name=f'projects/{w.pids[0]}/inflight'
    uploads.write_chunks(name,[b'late upload'],max_bytes=20,content_type='text/plain',if_generation_match=0)
    assert w.live.stat(name) is not None
    with pytest.raises(DocumentConflict,match='ERASURE_PENDING'):
        fenced.run_transaction(lambda tx:tx.set(('projects',w.pids[0],'chapters','chapter1'),{'object':name}))
    with pytest.raises((DocumentConflict,ProjectUnavailable)):
        uploads.write_chunks(name+'2',[b'deny'],max_bytes=20,content_type='text/plain',if_generation_match=0)
    assert w.live.stat(name+'2') is None
    assert w.docs.get(('projects',w.pids[0],'chapters','chapter1')).data['text']=='KEEP THIS SYNTHETIC TEXT'


def test_v10563_lifecycle_transaction_keeps_query_predicates_counts_and_read_your_writes():
    w = fixture()
    fenced = AccountLifecycleDocumentStore(w.docs,actor=lambda:w.uid)
    prefix=('projects',w.pids[0],'chapters')
    def write(tx):
        tx.set(prefix+('second',),{'text':'second'})
        tx.set(prefix+('chapter1',),{'text':'second'})
        assert len(tx.query_collection(prefix,field='text',value='second'))==2
        assert tx.count_collection(prefix)==2
        tx.delete(prefix+('second',))
        assert tx.count_collection(prefix)==1
        assert tx.get_many([prefix+('chapter1',),prefix+('second',)])[1] is None
    fenced.run_transaction(write)
    assert len(w.docs.query_collection(prefix))==1
    with pytest.raises(DocumentConflict,match='OPERATOR_ONLY'):
        fenced.run_transaction(lambda tx:tx.create(('account_erasures','forged'),{'state':'fake'}))


def test_v10563_real_runtime_composes_fence_with_journal_and_preserves_ordinary_saves(monkeypatch):
    from runtime.hosted import build_hosted_dev_runtime
    w = fixture()
    monkeypatch.setenv('BREADMAKER_AUTH_MODE','developer')
    monkeypatch.setenv('BREADMAKER_ENV','development')
    monkeypatch.setenv('BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED','1')
    monkeypatch.setenv('BREADMAKER_HOSTED_DEV_USER_ID',w.uid)
    fenced=AccountLifecycleDocumentStore(w.docs)
    runtime=build_hosted_dev_runtime(documents=fenced,objects=w.live)
    assert runtime.capabilities.public()['account_erasure_fence'] is True
    path=('projects',w.pids[0],'chapters','chapter1')
    runtime.projects.documents.run_transaction(lambda tx:tx.set(path,{'text':'ACKNOWLEDGED SAVE'}))
    assert w.docs.get(path).data['text']=='ACKNOWLEDGED SAVE'
    close(w)
    with pytest.raises((DocumentConflict,ProjectUnavailable)):
        runtime.projects.documents.run_transaction(lambda tx:tx.set(path,{'text':'REJECT'}))
    assert w.docs.get(path).data['text']=='ACKNOWLEDGED SAVE'


def test_v10563_application_fence_reads_all_preconditions_before_google_style_writes():
    w=fixture()
    class StrictTransaction:
        def __init__(self,tx): self.tx,self.writing=tx,False
        def get(self,path):
            assert not self.writing, 'Firestore forbids reads after writes'
            return self.tx.get(path)
        def get_many(self,paths):
            assert not self.writing
            return self.tx.get_many(paths)
        def set(self,path,data): self.writing=True; self.tx.set(path,data)
        def create(self,path,data): self.writing=True; self.tx.create(path,data)
        def delete(self,path): self.writing=True; self.tx.delete(path)
    class StrictStore:
        def run_transaction(self,fn,**kwargs):
            return w.docs.run_transaction(lambda tx:fn(StrictTransaction(tx)),**kwargs)
    fenced=AccountLifecycleDocumentStore(StrictStore(),actor=lambda:w.uid)
    def write(tx):
        tx.set(('projects',w.pids[0],'chapters','chapter1'),{'text':'first'})
        tx.set(('projects',w.pids[1],'chapters','chapter1'),{'text':'second'})
    fenced.run_transaction(write)
    assert w.docs.get(('projects',w.pids[1],'chapters','chapter1')).data['text']=='second'


def test_v10563_tampered_closed_status_cannot_claim_completion_without_audit():
    w=fixture()
    confirmation=w.engine.plan(EMAIL,operation=OPERATION).confirmation
    result=close(w,confirmation)
    assert w.engine.status(EMAIL,operation=OPERATION)==result
    receipt=next(meta for meta in w.audit.list_prefix('erasure-audit/') if meta.name.endswith('/identity-deleted.json'))
    w.audit.delete(receipt.name,if_generation_match=receipt.generation)
    with pytest.raises(DocumentConflict,match='AUDIT_MISMATCH'):
        w.engine.status(EMAIL,operation=OPERATION)


def test_v10563_firebase_adapter_distinguishes_only_explicit_not_found():
    class Missing(Exception): pass
    class Auth:
        UserNotFoundError = Missing
        @staticmethod
        def get_user(uid,**kwargs): raise Missing()
    adapter = FirebaseClosureIdentity(Auth(),object())
    assert adapter.get(EXTERNAL) is None
    def outage(*args,**kwargs): raise RuntimeError('PRIVATE_PROVIDER_ERROR')
    Auth.get_user = outage
    with pytest.raises(RuntimeError): adapter.get(EXTERNAL)


def test_v10563_cli_requires_dedicated_identity_and_deployed_fence():
    env={'BREADMAKER_ENV':'production','BREADMAKER_OPERATOR_MODE':'staged_account_closure',
         'BREADMAKER_GCP_PROJECT_ID':PROJECT,'BREADMAKER_FIRESTORE_DATABASE':'(default)',
         'BREADMAKER_RECOVERY_BUCKET':PROJECT+'-breadmaker-recovery'}
    required_environment(env,read_identity=lambda:OPERATOR)
    with pytest.raises(ValueError,match='DEDICATED_IDENTITY'):
        required_environment(env,read_identity=lambda:'breadmaker-production-runtime@'+PROJECT+'.iam.gserviceaccount.com')
    with pytest.raises(ValueError,match='CONFIGURATION'): required_environment({},read_identity=lambda:OPERATOR)
    require_deployed_fence('https://synthetic.invalid',read=lambda _:b'{"mode":"hosted","metadata":{"environment":"production"},"capabilities":{"account_erasure_fence":true}}')
    with pytest.raises(ValueError,match='DEPLOYED_FENCE'):
        require_deployed_fence('https://synthetic.invalid',read=lambda _:b'{"mode":"hosted","capabilities":{"account_erasure_fence":false}}')


def test_v10563_cli_help_is_safe_and_failures_do_not_echo_account_identifiers(monkeypatch,capsys):
    from hosted.account_closure_cli import main
    with pytest.raises(SystemExit) as stopped:
        main(['--help'])
    assert stopped.value.code==0
    capsys.readouterr()
    monkeypatch.delenv('BREADMAKER_OPERATOR_MODE',raising=False)
    assert main(['close','--email',EMAIL,'--operation',OPERATION,'--confirm','synthetic-confirm'])==1
    captured=capsys.readouterr()
    assert EMAIL not in captured.err and EXTERNAL not in captured.err
    assert 'completion not confirmed' in captured.err
