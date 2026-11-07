"""Resumable manuscript imports retain authority through production fences."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from io import BytesIO

import pytest
from docx import Document
from PIL import Image

from hosted.account_lifecycle import AccountLifecycleDocumentStore, AccountLifecycleObjectStore
from hosted.document_import import DocumentImport
from hosted.firestore import HostedProjectRepository, HostedStructuredRepository, HostedUserRepository, MemoryDocumentStore
from hosted.firestore.contracts import DocumentConflict, DocumentNotFound, ProjectUnavailable
from hosted.runtime_services import HostedCloudProjectRepository, ensure_hosted_user
from hosted.storage.memory import MemoryObjectStore
from hosted.idempotency import stable_project_id
from tests.test_v10353 import OWNER


ACTOR = ContextVar('synthetic_document_import_actor', default=None)


@contextmanager
def actor(uid=OWNER):
    token = ACTOR.set(uid)
    try:
        yield
    finally:
        ACTOR.reset(token)


def fenced_replicas():
    raw, objects = MemoryDocumentStore(), MemoryObjectStore()
    def make():
        documents = AccountLifecycleDocumentStore(raw, actor=ACTOR.get)
        guarded = AccountLifecycleObjectStore(objects, documents)
        users = HostedUserRepository(HostedStructuredRepository(documents))
        projects = HostedCloudProjectRepository(documents, guarded, HostedProjectRepository(documents), users, ACTOR.get)
        return DocumentImport(projects)
    with actor():
        service = make()
        ensure_hosted_user(service.documents, user_id=OWNER, display_name='Synthetic import owner')
        source = service.projects.create('Existing book', owner_user_id=OWNER)['id']
    return make, raw, objects, source


def illustrated_docx(count=130):
    document = Document()
    document.add_heading('First chapter', level=1)
    for index in range(count):
        document.add_paragraph(f'Synthetic manuscript paragraph {index}')
    images = []
    for color in ('red', 'blue'):
        image = BytesIO()
        Image.new('RGB', (8, 8), color).save(image, format='PNG')
        images.append(image.getvalue())
        document.add_picture(BytesIO(images[-1]))
    out = BytesIO()
    document.save(out)
    return out.getvalue(), images


def begin(service, payload=b'First paragraph\nSecond paragraph', operation='synthetic-document-import'):
    return service.begin(payload, 'Synthetic.txt', 'heading1', 'Imported manuscript', OWNER, operation)


def test_docx_import_preserves_request_authority_across_replicas_and_worker_threads():
    make, raw, objects, source = fenced_replicas()
    payload, images = illustrated_docx()
    before = deepcopy(raw.get(('projects', source)).data)
    with actor():
        progress = make().begin(payload, 'Synthetic.docx', 'heading1', 'Illustrated import', OWNER, 'synthetic-illustrated-import')
        job = progress['job_id']
        pid = make()._load(OWNER, job)['project_id']
        assert progress['images'] == 2 and progress['total_batches'] >= 3
        while not progress['ready']:
            assert raw.get(('projects', pid)) is None
            with pytest.raises(ProjectUnavailable):
                make().projects.get(pid)
            index = progress['next_batch']
            progress = make().step(OWNER, job, index)
            assert make().step(OWNER, job, index) == progress
        result = make().finish(OWNER, job)
        assert result['project']['id'] == pid
        assert make().finish(OWNER, job)['project']['id'] == pid
        blocks = make().domain.list('manuscript_block', {'project_id': pid}, user_id=OWNER)
        text = [block['text_content'] for block in blocks if block['block_type'] == 'paragraph']
        assert text[0] == 'Synthetic manuscript paragraph 0'
        assert text[-1] == 'Synthetic manuscript paragraph 129'
        assert len([block for block in blocks if block['block_type'] == 'image']) == 2
        assets = raw.query_collection(('projects', pid, 'assets'))
        assert len(assets) == len(images)
        for row in assets:
            blob = b''.join(objects.read_chunks(row.data['object_name'], max_bytes=4096))
            assert blob in images
        assert make().begin(payload, 'Synthetic.docx', 'heading1', 'Illustrated import', OWNER, 'synthetic-illustrated-import')['job_id'] == job
    assert raw.get(('projects', source)).data == before
    assert raw.query_collection(('projects', source, 'assets')) == []
    with pytest.raises(ProjectUnavailable):
        make().documents.run_transaction(lambda tx: tx.create(('projects', 'a'*32, 'chapters', 'outside-scope'), {}))


@pytest.mark.parametrize('phase', ['begin', 'step', 'finish'])
@pytest.mark.parametrize('freeze', ['pending', 'deleted', 'lock'])
def test_account_erasure_fences_still_block_each_import_phase(phase, freeze):
    make, raw, objects, _ = fenced_replicas()
    operation = 'synthetic-frozen-document'
    pid = stable_project_id(operation)
    with actor():
        if phase != 'begin':
            progress = begin(make(), operation=operation)
            if phase == 'finish':
                progress = make().step(OWNER, progress['job_id'], 0)
                assert progress['ready']
        def freeze_account(tx):
            path = ('users', OWNER)
            if freeze == 'pending':
                tx.set(path, {**tx.get(path).data, 'status': 'erasure_pending'})
            else:
                if freeze == 'deleted':tx.delete(path)
                tx.create(('account_erasure_locks', OWNER), {'operation': 'synthetic-erasure'})
        raw.run_transaction(freeze_account)
        before = list(objects.list_prefix(f'projects/{pid}/'))
        error, code = ((DocumentNotFound, 'HOSTED_USER_NOT_FOUND') if freeze == 'deleted' and phase == 'finish'
                       else (DocumentConflict, 'ERASURE_PENDING'))
        with pytest.raises(error, match=code):
            if phase == 'begin':begin(make(), operation=operation)
            elif phase == 'step':make().step(OWNER, progress['job_id'], 0)
            else:make().finish(OWNER, progress['job_id'])
        assert list(objects.list_prefix(f'projects/{pid}/')) == before
        assert raw.get(('projects', pid)) is None
        assert raw.query_collection(('projects', pid, 'members')) == []


def test_interrupted_document_upload_retries_without_duplicate_or_partial_publication(monkeypatch):
    make, raw, objects, source = fenced_replicas()
    operation = 'synthetic-interrupted-document'
    pid = stable_project_id(operation)
    write = objects.write_chunks
    with actor():
        with monkeypatch.context() as patch:
            def fail(*args, **kwargs):raise RuntimeError('synthetic upload interruption')
            patch.setattr(objects, 'write_chunks', fail)
            with pytest.raises(RuntimeError, match='upload interruption'):
                begin(make(), operation=operation)
        assert raw.get(('projects', pid)) is None
        assert raw.get(('hosted_import_operations', operation)) is not None
        progress = begin(make(), operation=operation)
        job = progress['job_id']
        with pytest.raises(DocumentConflict, match='REQUEST_CHANGED'):
            begin(make(), payload=b'Changed source', operation=operation)
        # In-flight bytes may complete, but a freeze must prevent their pointer.
        with monkeypatch.context() as patch:
            def write_then_freeze(*args, **kwargs):
                result = write(*args, **kwargs)
                raw.run_transaction(lambda tx: tx.create(('account_erasure_locks', OWNER), {'operation': 'synthetic-erasure'}))
                return result
            patch.setattr(objects, 'write_chunks', write_then_freeze)
            with pytest.raises(DocumentConflict, match='ERASURE_PENDING'):
                make().step(OWNER, job, 0)
        assert make().status(OWNER, job)['next_batch'] == 0
        assert raw.get(('projects', pid)) is None
        assert raw.query_collection(('projects', pid, 'blocks')) == []
        raw.run_transaction(lambda tx: tx.delete(('account_erasure_locks', OWNER)))
        assert make().step(OWNER, job, 0)['ready']
        assert make().finish(OWNER, job)['project']['id'] == pid
        assert len(raw.query_collection(('projects',))) == 2
        assert raw.get(('projects', source)) is not None


def test_job_owner_and_reservation_are_required_on_resumed_document_steps():
    make, raw, _, _ = fenced_replicas()
    operation = 'synthetic-reservation-check'
    with actor():
        progress = begin(make(), operation=operation)
        job = progress['job_id']
        stranger = 'usr_'+'f'*32
        for action in (lambda: make().status(stranger, job),
                       lambda: make().step(stranger, job, 0),
                       lambda: make().finish(stranger, job)):
            with pytest.raises(DocumentNotFound):action()
        with actor(stranger):
            with pytest.raises(DocumentConflict, match='ACTOR_MISMATCH'):
                make().step(OWNER, job, 0)
        assert make().status(OWNER, job)['next_batch'] == 0
        raw.run_transaction(lambda tx: tx.delete(('hosted_import_operations', operation)))
        with pytest.raises(DocumentConflict, match='RESERVATION_REQUIRED'):
            make().step(OWNER, job, 0)
        assert raw.get(('projects', stable_project_id(operation))) is None


def test_resumable_http_upload_step_and_finish_use_fenced_runtime(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import hosted.http_api as http
    import hosted.http_compat as compat
    from runtime.hosted import build_hosted_dev_runtime
    from hosted.runtime_services import _DEV_USER_ID
    monkeypatch.setenv('BREADMAKER_AUTH_MODE', 'developer')
    monkeypatch.setenv('BREADMAKER_ENV', 'test')
    monkeypatch.setenv('BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED', '1')
    raw, objects = MemoryDocumentStore(), MemoryObjectStore()
    runtime = build_hosted_dev_runtime(documents=AccountLifecycleDocumentStore(raw), objects=objects)
    monkeypatch.setattr(http, 'runtime', runtime)
    monkeypatch.setattr(compat, 'runtime', runtime)
    source = runtime.projects.create('Existing HTTP book')['id']
    original = deepcopy(raw.get(('projects', source)).data)
    app = FastAPI()
    http.install_request_boundary(app)
    app.include_router(compat.router)
    payload, _ = illustrated_docx()
    headers = {'X-Breadmaker-Project': source, 'Idempotency-Key': 'synthetic-http-import'}
    with TestClient(app) as client:
        reply = client.post('/api/import/new-project?resumable=true', headers=headers,
            files={'file': ('Synthetic.docx', payload, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')})
        assert reply.status_code == 200, reply.text
        progress = reply.json()
        job = progress['job_id']
        pid = DocumentImport(runtime.projects)._load(_DEV_USER_ID, job)['project_id']
        while not progress['ready']:
            assert raw.get(('projects', pid)) is None
            reply = client.post(f'/api/import/jobs/{job}/step?batch={progress["next_batch"]}', headers={'X-Breadmaker-Project': source})
            assert reply.status_code == 200, reply.text
            progress = reply.json()
        reply = client.post(f'/api/import/jobs/{job}/finish', headers={'X-Breadmaker-Project': source})
        assert reply.status_code == 200, reply.text
        assert reply.json()['project']['id'] == pid
        assert reply.json()['images'] == 2
        assert raw.get(('projects', source)).data == original


def test_old_job_without_reserved_operation_requires_safe_restart():
    make, raw, _, _ = fenced_replicas()
    with actor():
        progress = begin(make())
        job = progress['job_id']
        path = make()._path(OWNER, job)
        data = dict(raw.get(path).data)
        data.pop('operation')
        raw.run_transaction(lambda tx: tx.set(path, data))
        for action in (lambda: make().status(OWNER, job),
                       lambda: make().step(OWNER, job, 0),
                       lambda: make().finish(OWNER, job)):
            with pytest.raises(DocumentConflict, match='HOSTED_IMPORT_RESTART_REQUIRED'):action()
        assert raw.get(('projects', data['project_id'])) is None
