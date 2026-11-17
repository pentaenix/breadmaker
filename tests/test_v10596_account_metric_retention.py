from copy import deepcopy
import pytest
from hosted.account_deletion import HostedAccountDeletionPlanner,AccountDescendantProof
from hosted.firestore.memory import MemoryDocumentStore
from hosted.storage.memory import MemoryObjectStore

UID='usr_'+'a'*32;BORN='2026-11-01T00:00:00Z'


def setup():
    raw=MemoryDocumentStore();path=('users',UID,'metrics','synthetic')
    raw.run_transaction(lambda tx:tx.create(('users',UID),dict(user_id=UID,status='active',created_at=BORN)))
    raw.run_transaction(lambda tx:tx.create(path,dict(synthetic=True)))
    return raw,path


def test_injected_classification_adds_explicit_review_blocks_and_never_authorizes_erasure():
    for category,blocker in [('metric_data','HOSTED_ACCOUNT_METRIC_DATA_ERASURE_REVIEW_REQUIRED'),
        ('metric_history','HOSTED_ACCOUNT_METRIC_HISTORY_RETENTION_REVIEW_REQUIRED')]:
        raw,path=setup();before=deepcopy(raw._documents)
        classify=lambda *a,**kw:AccountDescendantProof(UID,BORN,category)
        planner=HostedAccountDeletionPlanner(raw,MemoryObjectStore(),MemoryObjectStore(),lambda:UID,
            saved_word_retention_enabled=True,account_descendant_classifier=classify)
        preview=planner.preview(UID)
        assert blocker in preview.blockers and 'HOSTED_ACCOUNT_UNCLASSIFIED_USER_REFERENCES' not in preview.blockers
        assert path in preview.account_document_paths and not preview.physical_deletion_authorized
        assert 'user_id' not in preview.public() and raw._documents==before


@pytest.mark.parametrize('fault',['wrong_owner','wrong_birth','exception','not_a_proof'])
def test_unverified_or_cross_incarnation_classification_remains_unknown(fault):
    raw,path=setup()
    def classify(*a,**kw):
        if fault=='exception':raise ValueError('Synthetic schema failure')
        if fault=='not_a_proof':return True
        return AccountDescendantProof('usr_'+'b'*32 if fault=='wrong_owner' else UID,
            '2025-01-01T00:00:00Z' if fault=='wrong_birth' else BORN,'metric_data')
    preview=HostedAccountDeletionPlanner(raw,MemoryObjectStore(),MemoryObjectStore(),lambda:UID,
        saved_word_retention_enabled=True,account_descendant_classifier=classify).preview(UID)
    assert 'HOSTED_ACCOUNT_UNCLASSIFIED_USER_REFERENCES' in preview.blockers
    assert not preview.account_metric_data_paths and path in preview.account_document_paths


def test_classifier_receives_copies_and_default_public_shape_is_preserved():
    raw,path=setup();before=deepcopy(raw._documents)
    default=HostedAccountDeletionPlanner(raw,MemoryObjectStore(),MemoryObjectStore(),lambda:UID).preview(UID)
    assert 'account_metric_data_count' not in default.public()
    def classify(p,data,*,profile,lookup):
        data.clear();profile.clear();lookup(('users',UID)).clear()
        return AccountDescendantProof(UID,BORN,'metric_data')
    preview=HostedAccountDeletionPlanner(raw,MemoryObjectStore(),MemoryObjectStore(),lambda:UID,
        saved_word_retention_enabled=True,account_descendant_classifier=classify).preview(UID)
    assert raw._documents==before and preview.account_metric_data_paths==(path,)


def test_new_classification_cannot_omit_existing_shared_policy_and_ledger_review():
    raw,_=setup()
    with pytest.raises(ValueError,match='LEGACY_RETENTION_REVIEW_REQUIRED'):
        HostedAccountDeletionPlanner(raw,MemoryObjectStore(),MemoryObjectStore(),lambda:UID,
            account_descendant_classifier=lambda *a,**kw:AccountDescendantProof(UID,BORN,'metric_data'))
