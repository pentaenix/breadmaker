"""CAS also preserves the existing encrypted member-state transaction boundary."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

import pytest

from tests.test_v10524_private_live_restore import OWNER, private_store
from hosted.firestore.contracts import DocumentConflict, HostedValidationError
from runtime.ui_state_revision import value_revision


def test_targets_private_state_conflict_rechecks_after_outside_transaction_sealing():
    documents,_,projects,_=private_store(OWNER)
    pid=projects.create('Disposable private Targets')['id']
    path=('projects',pid,'members',OWNER,'ui_state','targets-v1')
    revision=value_revision(None)
    barrier=Barrier(2);lock=Lock();reads=[0];get=documents.get
    def synchronized_get(candidate):
        row=get(candidate)
        if candidate==path:
            with lock:
                wait=reads[0]<2;reads[0]+=1
            if wait:barrier.wait(timeout=2)
        return row
    documents.get=synchronized_get
    def write(value):
        try:
            projects.put_member_state(pid,{'value':{'counter':value}},key='targets-v1',expected_value_revision=revision)
            return 'saved'
        except DocumentConflict as error:return str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(write,[2,3]))
    assert sorted(results)==['TARGETS_REVISION_CONFLICT','saved']
    documents.get=get
    saved=projects.member_state(pid,key='targets-v1')
    assert saved['value']['counter'] in [2,3]
    sealed=documents.get(path).data
    assert '_bm_private_fields' in sealed and 'value' not in sealed
    before=documents.get(path)
    with pytest.raises(DocumentConflict,match='TARGETS_REVISION_CONFLICT'):
        projects.put_member_state(pid,{'value':{'counter':99}},key='targets-v1',expected_value_revision=revision)
    assert documents.get(path)==before
    with pytest.raises(HostedValidationError,match='TARGETS_REVISION_REQUIRED'):
        projects.put_member_state(pid,{'value':{}},key='targets-v1')
