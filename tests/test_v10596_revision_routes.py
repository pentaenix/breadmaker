"""Reader-first runtime and gated request contracts on synthetic fenced stores."""
from copy import deepcopy

import pytest

from hosted.account_lifecycle import AccountLifecycleDocumentStore, AccountLifecycleObjectStore
from hosted.action_journal import ActionDocumentStore, _ACTION
from hosted.firestore import MemoryDocumentStore
from hosted.firestore.contracts import HostedValidationError
from hosted.manuscript_adoption import LegacyManuscriptAdoption
from hosted.manuscript_visibility import FORMAT_FIELD, ManuscriptDocumentStore
from hosted.runtime_services import _DEV_USER_ID
from hosted.storage.memory import MemoryObjectStore
from runtime.hosted import build_hosted_dev_runtime

FLAG = 'BREADMAKER_HOSTED_MANUSCRIPT_REVISIONS_ENABLED'


def make_runtime(monkeypatch, flag='', *, fenced=True, raw=None, objects=None):
    monkeypatch.setenv('BREADMAKER_AUTH_MODE', 'developer')
    monkeypatch.setenv('BREADMAKER_ENV', 'test')
    monkeypatch.setenv('BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED', '1')
    monkeypatch.setenv(FLAG, flag)
    raw = raw or MemoryDocumentStore()
    objects = objects or MemoryObjectStore()
    runtime = build_hosted_dev_runtime(
        documents=AccountLifecycleDocumentStore(raw) if fenced else raw, objects=objects)
    return runtime, raw, objects


def client_for(monkeypatch, runtime):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import hosted.http_api as http
    import hosted.http_compat as compat
    monkeypatch.setattr(http, 'runtime', runtime)
    monkeypatch.setattr(compat, 'runtime', runtime)
    app = FastAPI()
    http.install_request_boundary(app)
    app.include_router(http.platform_router)
    app.include_router(http.product_router)
    app.include_router(compat.router)
    return TestClient(app)


def adopt(runtime, pid):
    service = LegacyManuscriptAdoption(runtime.projects.documents.store)
    generation = runtime.projects.get(pid)['generation']
    progress = service.begin(pid, _DEV_USER_ID, generation=generation, operation='synthetic_adoption')
    while not progress['ready']:
        progress = service.step(pid, _DEV_USER_ID, progress['job_id'], progress['next_step'])
    service.finish(pid, _DEV_USER_ID, progress['job_id'])


@pytest.mark.parametrize('flag', ['', 'false', '0'])
def test_reader_stays_installed_when_writer_is_disabled(monkeypatch, flag):
    runtime, raw, _ = make_runtime(monkeypatch, flag)
    documents = runtime.projects.documents
    assert isinstance(documents, ActionDocumentStore)
    assert isinstance(documents.store, ManuscriptDocumentStore)
    assert isinstance(documents.store.store, AccountLifecycleDocumentStore)
    assert documents.store.store.store is raw
    assert isinstance(runtime.projects.objects, AccountLifecycleObjectStore)
    assert runtime.projects.objects.documents is documents.store.store
    assert not runtime.capabilities.supports('hosted_manuscript_revisions')


def test_explicit_writer_flag_requires_exact_value_and_lifecycle(monkeypatch):
    for invalid in ('1', 'yes', 'TRUE', ' false'):
        with pytest.raises(RuntimeError, match='CONFIGURATION_INVALID'):
            make_runtime(monkeypatch, invalid)
    with pytest.raises(RuntimeError, match='LIFECYCLE_REQUIRED'):
        make_runtime(monkeypatch, 'true', fenced=False)
    runtime, _, _ = make_runtime(monkeypatch, 'true')
    assert runtime.capabilities.supports('hosted_manuscript_revisions')
    plain, _, _ = make_runtime(monkeypatch, fenced=False)
    assert isinstance(plain.projects.documents.store, ManuscriptDocumentStore)
    assert not plain.capabilities.supports('hosted_manuscript_revisions')


def test_disabled_routes_do_not_stage_or_adopt_but_small_import_is_unchanged(monkeypatch):
    runtime, raw, _ = make_runtime(monkeypatch)
    pid = runtime.projects.create('Synthetic disabled project')['id']
    headers = {'X-Breadmaker-Project': pid, 'Idempotency-Key': 'synthetic-disabled'}
    before = deepcopy(raw._documents)
    with client_for(monkeypatch, runtime) as client:
        for path in ('/api/import?resumable=true', '/api/documents/import?resumable=true'):
            response = client.post(path, headers=headers, files={'file': ('Book.txt', b'new prose')})
            assert response.status_code == 409 and response.json()['detail'] == 'HOSTED_MANUSCRIPT_REVISIONS_DISABLED'
        for method, path in [('get', '/api/import/revisions/job'),
                ('post', '/api/import/revisions/job/step?batch=0'),
                ('post', '/api/import/revisions/job/finish'),
                ('post', '/api/import/revisions/job/cancel'),
                ('post', '/api/manuscript/adoptions'),
                ('get', '/api/manuscript/adoptions/job'),
                ('post', '/api/manuscript/adoptions/job/step?batch=0'),
                ('post', '/api/manuscript/adoptions/job/finish')]:
            response = getattr(client, method)(path, headers=headers)
            assert response.status_code == 409, response.text
        assert raw._documents == before
        response = client.post('/api/import', headers=headers, files={'file': ('Book.txt', b'new prose')})
        assert response.status_code == 200, response.text
        assert 'job_id' not in response.json()
        assert FORMAT_FIELD not in raw.get(('projects', pid)).data


def test_revision_routes_bind_actor_project_operation_and_publication_action(monkeypatch):
    import hosted.revision_staging as staging
    runtime, _, _ = make_runtime(monkeypatch, 'true')
    pid = runtime.projects.create('Synthetic route project')['id']
    seen = []
    class Service:
        def __init__(self, domain):
            assert domain is runtime.projects.domain
        def begin_document(self, project, actor, raw, filename, heading, **kwargs):
            seen.append(('begin', project, actor, raw, filename, heading, kwargs))
            return {'job_id': 'synthetic_job', 'ready': False}
        def status(self, *args):
            seen.append(('status', *args)); return {'ready': False}
        def step(self, *args):
            seen.append(('step', *args)); return {'ready': True}
        def finish(self, *args):
            seen.append(('finish', *args, _ACTION.get())); return {'ok': True}
        def cancel(self, *args):
            seen.append(('cancel', *args)); return {'cancelled': True}
    client = client_for(monkeypatch, runtime)
    monkeypatch.setattr(staging, 'ManuscriptRevisionImport', Service)
    headers = {'X-Breadmaker-Project': pid, 'Idempotency-Key': 'synthetic-route',
        'X-Breadmaker-User': 'usr_untrusted'}
    with client:
        for path in ('/api/import?resumable=true&confirmed=true',
                '/api/documents/import?resumable=true&after_document_id=tab_destination'):
            response = client.post(path, headers=headers, files={'file': ('Book.txt', b'synthetic prose')})
            assert response.status_code == 200, response.text
        assert client.get('/api/import/revisions/synthetic_job', headers=headers).status_code == 200
        assert client.post('/api/import/revisions/synthetic_job/step?batch=2', headers=headers).status_code == 200
        token = client.post('/api/undo/begin', headers=headers).json()['token']
        assert client.post('/api/import/revisions/synthetic_job/finish',
            headers={**headers, 'X-Breadmaker-Action': token}).status_code == 200
        assert client.post('/api/import/revisions/synthetic_job/cancel', headers=headers).status_code == 200
    assert all(row[1:3] == (pid, _DEV_USER_ID) for row in seen)
    assert seen[0][-1]['replace_main'] and seen[0][-1]['confirmed']
    assert not seen[1][-1]['replace_main'] and seen[1][-1]['after_document_id'] == 'tab_destination'
    assert seen[0][-1]['operation'].startswith('op_')
    assert seen[0][-1]['operation'] != seen[1][-1]['operation']
    assert seen[3][-1] == 2
    assert seen[4][-1] == (token, pid, _DEV_USER_ID)


def test_adoption_http_is_bounded_and_rechecks_current_project(monkeypatch):
    runtime, raw, _ = make_runtime(monkeypatch, 'true')
    pid = runtime.projects.create('Synthetic adoption project')['id']
    other = runtime.projects.create('Other project')['id']
    headers = {'X-Breadmaker-Project': pid, 'Idempotency-Key': 'synthetic-adoption'}
    generation = runtime.projects.get(pid)['generation']
    with client_for(monkeypatch, runtime) as client:
        response = client.post('/api/manuscript/adoptions', headers=headers, json={'generation': generation})
        assert response.status_code == 200, response.text
        progress = response.json()
        job = progress['job_id']
        assert FORMAT_FIELD not in raw.get(('projects', pid)).data
        wrong = client.get(f'/api/manuscript/adoptions/{job}', headers={'X-Breadmaker-Project': other})
        assert wrong.status_code == 404
        assert client.post(f'/api/manuscript/adoptions/{job}/finish', headers=headers).status_code == 409
        while not progress['ready']:
            response = client.post(f'/api/manuscript/adoptions/{job}/step?batch={progress["next_step"]}', headers=headers)
            assert response.status_code == 200, response.text
            progress = response.json()
            assert FORMAT_FIELD not in raw.get(('projects', pid)).data
        assert client.get(f'/api/manuscript/adoptions/{job}', headers=headers).json() == progress
        response = client.post(f'/api/manuscript/adoptions/{job}/finish', headers=headers)
        assert response.status_code == 200 and response.json()['activated']
        assert raw.get(('projects', pid)).data[FORMAT_FIELD] == 1


def test_writer_rollback_preserves_encoded_reads_trash_small_delete_and_undo(monkeypatch):
    runtime, raw, objects = make_runtime(monkeypatch, 'true')
    pid = runtime.projects.create('Synthetic encoded project')['id']
    adopt(runtime, pid)
    # Model a reader-compatible binary restarted with writers switched off.
    runtime, _, _ = make_runtime(monkeypatch, '', raw=raw, objects=objects)
    assert runtime.projects.get(pid)['status'] == 'active'
    runtime.projects.delete(pid)
    assert raw.get(('projects', pid)).data['status'] == 'deleted_manuscript_v1'
    assert any(row['id'] == pid for row in runtime.projects.list_trash())
    runtime.projects.restore_from_trash(pid)
    assert raw.get(('projects', pid)).data['status'] == 'active_manuscript_v1'
    domain = runtime.projects.domain
    chapters = domain.list('chapter', {'project_id': pid}, user_id=_DEV_USER_ID)
    cid = chapters[0]['id']
    headers = {'X-Breadmaker-Project': pid}
    with client_for(monkeypatch, runtime) as client:
        token = client.post('/api/undo/begin', headers=headers).json()['token']
        response = client.delete(f'/api/chapters/{cid}/permanent', headers={**headers, 'X-Breadmaker-Action': token})
        assert response.status_code == 200, response.text
        assert client.post(f'/api/undo/finish/{token}', headers=headers).status_code == 200
        assert not domain.list('chapter', {'project_id': pid}, user_id=_DEV_USER_ID)
        assert client.post(f'/api/undo/apply/{token}', headers=headers).status_code == 200
    restored = domain.list('chapter', {'project_id': pid}, user_id=_DEV_USER_ID)
    assert [{key: value for key, value in row.items() if key != 'generation'} for row in restored] == [
        {key: value for key, value in row.items() if key != 'generation'} for row in chapters]
    assert restored[0]['generation'] > chapters[0]['generation']
    assert raw.get(('projects', pid)).data[FORMAT_FIELD] == 1


@pytest.mark.parametrize('field', ['encrypted_fields', 'encrypted_objects'])
def test_private_adoption_and_revision_import_fail_closed(monkeypatch, field):
    runtime, raw, _ = make_runtime(monkeypatch, 'true')
    pid = runtime.projects.create('Synthetic private policy')['id']
    domain = runtime.projects.domain
    monkeypatch.setattr(domain, field, object())
    from hosted.revision_staging import ManuscriptRevisionImport
    with pytest.raises(HostedValidationError, match='PRIVATE_BATCH_NOT_READY'):
        ManuscriptRevisionImport(domain)
    with client_for(monkeypatch, runtime) as client:
        response = client.post('/api/manuscript/adoptions', headers={'X-Breadmaker-Project': pid},
            json={'generation': 1})
        assert response.status_code == 400 and response.json()['detail'] == 'HOSTED_PRIVATE_BATCH_NOT_READY'
    assert FORMAT_FIELD not in raw.get(('projects', pid)).data


def test_revision_steps_publish_only_with_current_structural_action_and_undo(monkeypatch):
    from hosted.document_io import parse_document_bytes
    from hosted.revision_staging import ManuscriptRevisionImport
    runtime, raw, _ = make_runtime(monkeypatch, 'true')
    pid = runtime.projects.create('Synthetic staged publication')['id']
    adopt(runtime, pid)
    domain = runtime.projects.domain
    before = domain.list('manuscript_block', {'project_id': pid}, user_id=_DEV_USER_ID)
    tabs, _ = parse_document_bytes('\n'.join(f'New paragraph {i}' for i in range(130)).encode(), 'Novel.txt')
    service = ManuscriptRevisionImport(domain)
    progress = service.begin(pid, _DEV_USER_ID, tabs, operation='synthetic_revision', replace_main=True)
    job = progress['job_id']
    headers = {'X-Breadmaker-Project': pid}
    with client_for(monkeypatch, runtime) as client:
        while not progress['ready']:
            assert domain.list('manuscript_block', {'project_id': pid}, user_id=_DEV_USER_ID) == before
            response = client.post(f'/api/import/revisions/{job}/step?batch={progress["next_batch"]}', headers=headers)
            assert response.status_code == 200, response.text
            progress = response.json()
        assert client.get(f'/api/import/revisions/{job}', headers=headers).json()['ready']
        response = client.post(f'/api/import/revisions/{job}/finish', headers=headers)
        assert response.status_code == 400 and response.json()['detail'] == 'HOSTED_MANUSCRIPT_UNDO_ACTION_REQUIRED'
        token = client.post('/api/undo/begin', headers=headers).json()['token']
        response = client.post(f'/api/import/revisions/{job}/finish', headers={**headers, 'X-Breadmaker-Action': token})
        assert response.status_code == 200, response.text
        assert response.json()['undo_token'] == token
        assert client.post(f'/api/undo/finish/{token}', headers=headers).status_code == 200
        assert len(domain.list('manuscript_block', {'project_id': pid}, user_id=_DEV_USER_ID)) == 130
        assert len(raw.get(('hosted_actions', _DEV_USER_ID, 'tokens', token)).data['records']) == 1
        assert client.post(f'/api/import/revisions/{job}/cancel', headers=headers).status_code == 409
        assert client.post(f'/api/undo/apply/{token}', headers=headers).status_code == 200
        assert domain.list('manuscript_block', {'project_id': pid}, user_id=_DEV_USER_ID) == before
        second = service.begin(pid, _DEV_USER_ID, tabs, operation='synthetic_cancel', replace_main=True)
        job = second['job_id']
        assert client.post(f'/api/import/revisions/{job}/cancel', headers=headers).json()['cancelled']
        assert client.post(f'/api/import/revisions/{job}/step?batch=0', headers=headers).status_code == 409


def test_enabled_chapter_delete_dispatches_only_revision_service(monkeypatch):
    import hosted.chapter_deletion as legacy
    import hosted.revision_staging as revisions
    runtime, _, _ = make_runtime(monkeypatch, 'true')
    pid = runtime.projects.create('Synthetic delete dispatch')['id']
    calls = []
    def forbidden(*args):
        raise AssertionError('Enabled revision deletion must never fall back to inline legacy deletion/adoption')
    def revision(domain, project, chapter, actor):
        calls.append((domain, project, chapter, actor, _ACTION.get()))
        return {'ok': True}
    monkeypatch.setattr(legacy, 'delete_chapter_atomic', forbidden)
    monkeypatch.setattr(revisions, 'delete_chapter_revision', revision)
    headers = {'X-Breadmaker-Project': pid}
    with client_for(monkeypatch, runtime) as client:
        token = client.post('/api/undo/begin', headers=headers).json()['token']
        response = client.delete('/api/chapters/chapter_1/permanent', headers={**headers, 'X-Breadmaker-Action': token})
        assert response.status_code == 200, response.text
    assert calls == [(runtime.projects.domain, pid, 'chapter_1', _DEV_USER_ID, (token, pid, _DEV_USER_ID))]
