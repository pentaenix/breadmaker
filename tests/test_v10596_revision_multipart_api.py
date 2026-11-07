"""Actual multipart begin/adoption/image/content/publication protocol, synthetic data only."""
from copy import deepcopy

from hosted.manuscript_visibility import FORMAT_FIELD
from hosted.runtime_services import _DEV_USER_ID
from tests.test_v10594 import illustrated_docx
from tests.test_v10596_revision_routes import client_for, make_runtime


def book_rows(runtime, pid):
    return runtime.projects.domain.list('manuscript_block', {'project_id': pid}, user_id=_DEV_USER_ID)


def begin(client, pid, payload, *, append=False, operation='multipart', confirmed=False):
    path = ('/api/documents/import' if append else '/api/import') + '?resumable=true'
    if confirmed:
        path += '&confirmed=true'
    return client.post(path, headers={'X-Breadmaker-Project': pid, 'Idempotency-Key': operation},
        files={'file': ('Synthetic.docx' if payload[:2] == b'PK' else 'Synthetic.txt', payload)})


def advance(client, runtime, pid, progress, before):
    job, phases = progress['job_id'], set()
    while not progress['ready']:
        assert book_rows(runtime, pid) == before
        phases.add(progress['phase'])
        index = progress['next_batch']
        response = client.post(f'/api/import/revisions/{job}/step?batch={index}', headers={'X-Breadmaker-Project': pid})
        assert response.status_code == 200, response.text
        progress = response.json()
        replay = client.post(f'/api/import/revisions/{job}/step?batch={index}', headers={'X-Breadmaker-Project': pid})
        assert replay.status_code == 200 and replay.json() == progress
    assert book_rows(runtime, pid) == before
    return phases


def publish(client, pid, job):
    headers = {'X-Breadmaker-Project': pid}
    token = client.post('/api/undo/begin', headers=headers).json()['token']
    response = client.post(f'/api/import/revisions/{job}/finish', headers={**headers, 'X-Breadmaker-Action': token})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['undo_token'] == token and result['undo_available']
    assert client.post(f'/api/undo/finish/{token}', headers=headers).status_code == 200
    return token, result


def test_real_multipart_hundred_thousand_word_replace_stays_hidden_until_pointer_commit(monkeypatch):
    runtime, raw, _ = make_runtime(monkeypatch, 'true')
    pid = runtime.projects.create('Synthetic novel')['id']
    before = book_rows(runtime, pid)
    payload = '\n'.join(' '.join(['synthetic'] * 200) for _ in range(500)).encode()
    with client_for(monkeypatch, runtime) as client:
        response = begin(client, pid, payload)
        assert response.status_code == 200, response.text
        progress = response.json()
        assert progress['phase'] == 'adopting' and not progress['ready']
        assert FORMAT_FIELD not in raw.get(('projects', pid)).data
        assert begin(client, pid, payload).json() == progress
        assert advance(client, runtime, pid, progress, before) == {'adopting', 'content'}
        token, result = publish(client, pid, progress['job_id'])
        assert result['chapters'] == 1
        assert sum(len(row['text_content'].split()) for row in book_rows(runtime, pid)) == 100_000
        assert len(raw.get(('hosted_actions', _DEV_USER_ID, 'tokens', token)).data['records']) == 1
        assert client.post(f'/api/undo/apply/{token}', headers={'X-Breadmaker-Project': pid}).status_code == 200
        assert book_rows(runtime, pid) == before
        assert client.post(f'/api/undo/apply/{token}?direction=redo', headers={'X-Breadmaker-Project': pid}).status_code == 200
        assert sum(len(row['text_content'].split()) for row in book_rows(runtime, pid)) == 100_000
        unconfirmed = begin(client, pid, b'Replacement must require consent', operation='new-unconfirmed')
        assert unconfirmed.status_code == 409 and 'Confirm replacement' in unconfirmed.json()['detail']


def test_real_docx_append_publishes_images_and_large_chapter_delete_undo(monkeypatch):
    runtime, raw, objects = make_runtime(monkeypatch, 'true')
    pid = runtime.projects.create('Synthetic image append')['id']
    before = book_rows(runtime, pid)
    payload, expected_images = illustrated_docx(130)
    with client_for(monkeypatch, runtime) as client:
        response = begin(client, pid, payload, append=True)
        assert response.status_code == 200, response.text
        progress = response.json()
        assert progress['images'] == 2
        phases = advance(client, runtime, pid, progress, before)
        assert phases == {'adopting', 'images', 'content'}
        assert runtime.projects.documents.query_collection(('projects', pid, 'assets')) == []
        token, result = publish(client, pid, progress['job_id'])
        imported = [row for row in book_rows(runtime, pid) if row['tab_id'] == result['first_document_id']]
        assert len(imported) == 132 and sum(row['block_type'] == 'image' for row in imported) == 2
        assets = runtime.projects.documents.query_collection(('projects', pid, 'assets'))
        assert len(assets) == 2
        assert {b''.join(objects.read_chunks(row.data['object_name'], max_bytes=4096)) for row in assets} == set(expected_images)
        headers = {'X-Breadmaker-Project': pid}
        delete_token = client.post('/api/undo/begin', headers=headers).json()['token']
        cid = imported[0]['chapter_id']
        response = client.delete(f'/api/chapters/{cid}/permanent', headers={**headers, 'X-Breadmaker-Action': delete_token})
        assert response.status_code == 200, response.text
        assert client.post(f'/api/undo/finish/{delete_token}', headers=headers).status_code == 200
        assert book_rows(runtime, pid) == before
        assert len(raw.get(('hosted_actions', _DEV_USER_ID, 'tokens', delete_token)).data['records']) == 1
        assert client.post(f'/api/undo/apply/{delete_token}', headers=headers).status_code == 200
        assert len(book_rows(runtime, pid)) == len(before) + 132
        assert client.post(f'/api/undo/apply/{token}', headers=headers).status_code == 200
        assert book_rows(runtime, pid) == before
        # Published image URLs remain valid for immutable History/Review captures.
        assert len(runtime.projects.documents.query_collection(('projects', pid, 'assets'))) == 2


def test_multipart_image_source_interruption_retries_without_adopting_or_publishing(monkeypatch):
    from hosted.revision_images import RevisionImageStager
    runtime, raw, _ = make_runtime(monkeypatch, 'true')
    pid = runtime.projects.create('Synthetic interrupted source')['id']
    before = book_rows(runtime, pid)
    payload, _ = illustrated_docx(2)
    original = RevisionImageStager.publish_sources
    def interrupted(self, *args):
        original(self, *args)
        raise RuntimeError('Synthetic interruption after durable source receipts')
    with client_for(monkeypatch, runtime) as client:
        with monkeypatch.context() as patch:
            patch.setattr(RevisionImageStager, 'publish_sources', interrupted)
            response = begin(client, pid, payload)
            assert response.status_code == 500
        assert FORMAT_FIELD not in raw.get(('projects', pid)).data
        jobs = raw.query_collection(('projects', pid, 'manuscript_import_jobs'))
        assert len(jobs) == 1 and jobs[0].data['state'] == 'preparing'
        assert not jobs[0].data['source_ready'] and len(jobs[0].data['asset_source_receipts']) == 2
        assert book_rows(runtime, pid) == before
        assert runtime.projects.documents.query_collection(('projects', pid, 'assets')) == []
        response = begin(client, pid, payload)
        assert response.status_code == 200, response.text
        progress = response.json()
        assert progress['job_id'] == jobs[0].path[-1]
        assert len(raw.query_collection(('projects', pid, 'manuscript_import_jobs'))) == 1
        advance(client, runtime, pid, progress, before)
        _, result = publish(client, pid, progress['job_id'])
        assert result['images'] == 2 and len(book_rows(runtime, pid)) == 4


def test_revision_job_rejects_other_project_and_different_authenticated_editor(monkeypatch):
    runtime, raw, objects = make_runtime(monkeypatch, 'true')
    pid = runtime.projects.create('Synthetic owner job')['id']
    other = runtime.projects.create('Synthetic other project')['id']
    with client_for(monkeypatch, runtime) as client:
        response = begin(client, pid, b'Private synthetic job')
        assert response.status_code == 200
        job = response.json()['job_id']
        for method, path in [('get', ''), ('post', '/step?batch=0'), ('post', '/cancel')]:
            response = getattr(client, method)(f'/api/import/revisions/{job}{path}', headers={'X-Breadmaker-Project': other})
            assert response.status_code == 404, response.text
    foreign = 'usr_' + 'e' * 32
    monkeypatch.setenv('BREADMAKER_HOSTED_DEV_USER_ID', foreign)
    foreign_runtime, _, _ = make_runtime(monkeypatch, 'true', raw=raw, objects=objects)
    raw.run_transaction(lambda tx: tx.create(('projects', pid, 'members', foreign), {'user_id': foreign, 'role': 'editor'}))
    before = deepcopy(raw._documents)
    with client_for(monkeypatch, foreign_runtime) as client:
        for method, path in [('get', ''), ('post', '/step?batch=0'), ('post', '/cancel')]:
            response = getattr(client, method)(f'/api/import/revisions/{job}{path}', headers={
                'X-Breadmaker-Project': pid, 'X-Breadmaker-User': _DEV_USER_ID})
            assert response.status_code == 404, response.text
        # A current, valid foreign action still cannot publish the owner's job.
        token = client.post('/api/undo/begin', headers={'X-Breadmaker-Project': pid}).json()['token']
        response = client.post(f'/api/import/revisions/{job}/finish', headers={
            'X-Breadmaker-Project': pid, 'X-Breadmaker-Action': token})
        assert response.status_code == 404, response.text
    assert raw.get(('projects', pid, 'manuscript_import_jobs', job)).data == before[('projects', pid, 'manuscript_import_jobs', job)][0]


def test_adoption_optional_generation_terminal_replay_and_delete_precondition(monkeypatch):
    runtime, raw, _ = make_runtime(monkeypatch, 'true')
    pid = runtime.projects.create('Synthetic first delete')['id']
    headers = {'X-Breadmaker-Project': pid, 'Idempotency-Key': 'prepare-delete'}
    with client_for(monkeypatch, runtime) as client:
        token = client.post('/api/undo/begin', headers=headers).json()['token']
        response = client.delete('/api/chapters/chapter_1/permanent', headers={**headers, 'X-Breadmaker-Action': token})
        assert response.status_code == 400 and response.json()['detail'] == 'HOSTED_MANUSCRIPT_ADOPTION_REQUIRED'
        assert FORMAT_FIELD not in raw.get(('projects', pid)).data
        assert client.post(f'/api/undo/rollback/{token}', headers=headers).status_code == 200
        response = client.post('/api/manuscript/adoptions', headers=headers, json={})
        assert response.status_code == 200, response.text
        progress = response.json()
        while not progress['ready']:
            response = client.post(f'/api/manuscript/adoptions/{progress["job_id"]}/step?batch={progress["next_step"]}', headers=headers)
            assert response.status_code == 200, response.text
            progress = response.json()
        response = client.post(f'/api/manuscript/adoptions/{progress["job_id"]}/finish', headers=headers)
        assert response.status_code == 200 and response.json()['activated']
        response = client.post('/api/manuscript/adoptions', headers=headers, json={})
        assert response.status_code == 200, response.text
        assert response.json()['activated'] and response.json()['ready'] and response.json()['job_id'] is None
        response = client.post('/api/manuscript/adoptions', headers=headers, json={'generation': 999})
        assert response.status_code == 409 and response.json()['detail'] == 'HOSTED_GENERATION_CONFLICT'
