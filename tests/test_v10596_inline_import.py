"""Opt-in experiment only; normal HTTP/runtime composition remains unchanged."""
import pytest
from hosted.package_import import PackageImport
from hosted.firestore.contracts import HostedValidationError
from hosted.idempotency import stable_project_id
from hosted.runtime_services import _DEV_USER_ID as OWNER
from tests.test_v10596_package_import import setup, finish_steps


def test_opt_in_inline_is_unpublished_until_finish_and_reads_after_restart(setup):
    one, make, docs, _ = setup
    service = PackageImport(one.projects, inline_blocks=True)
    progress = service.begin(b'small', OWNER, operation='inline_experiment')
    pid = stable_project_id('inline_experiment')
    finish_steps(service, progress)
    assert docs.get(('projects', pid)) is None
    rows = docs.query_collection(('projects', pid, 'blocks'))
    assert len(rows) == 3
    assert all('payload_object' not in r.data and r.data['text_content'].startswith('Synthetic') for r in rows)
    service.finish(OWNER, progress['job_id'])
    restored = make().projects.domain.list('manuscript_block', {'project_id': pid}, user_id=OWNER)
    assert len(restored) == 3
    assert sorted(r['text_content'] for r in restored) == [f'Synthetic paragraph {i}' for i in range(3)]


def test_large_or_non_manuscript_payload_keeps_object(setup):
    one, _, _, pid = setup
    from hosted.packages import HostedProjectPackageService
    packages = HostedProjectPackageService(one.projects)
    _, row = packages._prepare_import_entity('manuscript_block', {'project_id': pid},
        {'chapter_id':'chapter_import', 'text_content':'é' * 5000, 'content_html':'<p>large</p>'},
        entity_id='b_large', inline_block=True)
    assert 'payload_object' in row and 'text_content' not in row


def test_inline_removes_small_block_publications_without_removing_guards(setup, monkeypatch):
    from hosted.package_import import _GuardedObjects
    import sys
    from types import SimpleNamespace
    from tests.test_v10596_package_import import source_plan
    monkeypatch.setitem(sys.modules, 'hosted.native_package', SimpleNamespace(
        prepare_project_package=lambda raw, pid, **kwargs: source_plan(pid, count=130)))
    one, _, _, _ = setup
    original_write = _GuardedObjects.write_chunks
    names = []
    def counted(self, name, *args, **kwargs):
        names.append(name)
        return original_write(self, name, *args, **kwargs)
    monkeypatch.setattr(_GuardedObjects, 'write_chunks', counted)
    totals = []
    for inline in (False, True):
        names.clear()
        service = PackageImport(one.projects, inline_blocks=inline)
        progress = service.begin(b'small', OWNER, operation='publication_count_' + str(inline))
        finish_steps(service, progress)
        totals.append(len([name for name in names if '/block-payloads/' in name]))
    # 130 paragraphs plus three batch accelerators versus three accelerators only.
    assert totals == [133, 3]


def imported_inline(setup):
    one, make, docs, _ = setup
    service = PackageImport(one.projects, inline_blocks=True)
    progress = service.begin(b'small', OWNER, operation='inline_lifecycle')
    finish_steps(service, progress)
    result = service.finish(OWNER, progress['job_id'])
    return one, make, docs, result['project']['id']


def test_inline_edit_externalizes_without_retaining_stale_prose(setup):
    one, make, docs, pid = imported_inline(setup)
    domain = one.projects.domain
    ids = {'project_id': pid, 'block_id': 'b_import_0'}
    result = domain.update('manuscript_block', ids,
        {'text_content':'Edited exact text', 'content_html':'<p>Edited exact text</p>'},
        user_id=OWNER, expected_generation=1, operation='edit_inline')
    assert result['text_content'] == 'Edited exact text'
    raw = docs.get(('projects', pid, 'blocks', 'b_import_0')).data
    assert 'payload_object' in raw
    assert 'text_content' not in raw and 'content_html' not in raw
    assert make().projects.domain.get('manuscript_block', ids, user_id=OWNER)['text_content'] == 'Edited exact text'


def test_inline_export_and_recovery_preserve_bodies(setup):
    import io, json, zipfile
    from hosted.packages import HostedProjectPackageService
    from hosted.recovery_archives import HostedRecoveryArchiveService
    from hosted.storage.memory import MemoryObjectStore
    one, make, _, pid = imported_inline(setup)
    packages = HostedProjectPackageService(one.projects)
    raw = packages.export_bytes(pid, OWNER)
    recovery = HostedRecoveryArchiveService(make().projects, MemoryObjectStore())
    recovery.create(pid, OWNER, operation='inline_snapshot')
    archived = recovery.read(pid, OWNER, operation='inline_snapshot')
    for payload in (raw, archived):
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            entities=json.loads(z.read('project.json'))['entities']
        assert [r['data']['text_content'] for r in entities['manuscript_block']] == [f'Synthetic paragraph {i}' for i in range(3)]


def test_supported_10596_rollback_reader_accepts_inline_and_edited_pointer(setup):
    import runpy
    from pathlib import Path
    import hosted.domain_store as module
    old_hydrate = runpy.run_path(str(Path(__file__).parent / 'fixtures' / 'rollback_10596_hydrate.py'),
                                init_globals=vars(module))['_hydrate']
    one, _, docs, pid = imported_inline(setup)
    ids = {'project_id':pid, 'block_id':'b_import_0'}
    raw = docs.get(('projects',pid,'blocks','b_import_0')).data
    assert old_hydrate(one.projects.domain, 'manuscript_block', raw, ids=ids, user_id=OWNER)['text_content'] == 'Synthetic paragraph 0'
    one.projects.domain.update('manuscript_block', ids, {'text_content':'Rollback-visible edit','content_html':'<p>Rollback-visible edit</p>'}, user_id=OWNER, expected_generation=1)
    raw = docs.get(('projects',pid,'blocks','b_import_0')).data
    assert old_hydrate(one.projects.domain, 'manuscript_block', raw, ids=ids, user_id=OWNER)['text_content'] == 'Rollback-visible edit'


def test_inline_opt_in_does_not_bypass_encrypted_import_embargo(setup):
    one, _, _, _ = setup
    one.projects.domain.encrypted_fields = object()
    with pytest.raises(HostedValidationError, match='HOSTED_ENCRYPTED_PACKAGE_IMPORT_NOT_READY'):
        PackageImport(one.projects, inline_blocks=True)


@pytest.mark.parametrize('first_inline,second_inline', [(False,True),(True,False)])
def test_cross_version_retry_preserves_receipt_and_content(setup, first_inline, second_inline):
    one, make, docs, _ = setup
    first = PackageImport(one.projects, inline_blocks=first_inline)
    progress = first.begin(b'small', OWNER, operation='mixed_retry')
    progress = first.step(OWNER,progress['job_id'],0)
    retry = PackageImport(make().projects, inline_blocks=second_inline)
    assert retry.step(OWNER,progress['job_id'],0) == progress
    result=retry.finish(OWNER,progress['job_id'])
    rows=make().projects.domain.list('manuscript_block',{'project_id':result['project']['id']},user_id=OWNER)
    assert [r['text_content'] for r in rows] == [f'Synthetic paragraph {i}' for i in range(3)]


def test_inline_native_export_retains_exact_paragraphs(setup, tmp_path):
    import io, sqlite3, zipfile
    from hosted.packages import HostedProjectPackageService
    one, _, _, pid = imported_inline(setup)
    raw = HostedProjectPackageService(one.projects).export_local_bytes(pid, OWNER)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        database = archive.read('project.sqlite3')
    path = tmp_path / 'synthetic-export.sqlite3'
    path.write_bytes(database)
    with sqlite3.connect(path) as con:
        rows = con.execute('SELECT text_content,content_html FROM manuscript_blocks ORDER BY position').fetchall()
    assert rows == [(f'Synthetic paragraph {i}',f'<p>Synthetic paragraph {i}</p>') for i in range(3)]


@pytest.mark.parametrize('fence', ['cancel', 'account_freeze'])
def test_inline_staged_rows_never_publish_after_fence(setup, fence):
    from hosted.firestore.contracts import DocumentConflict
    one, _, docs, _ = setup
    service = PackageImport(one.projects, inline_blocks=True)
    progress = service.begin(b'small', OWNER, operation='inline_fence')
    finish_steps(service, progress)
    if fence == 'cancel':
        service.cancel(OWNER,progress['job_id'])
    else:
        def freeze(tx):
            row=tx.get(('users',OWNER))
            tx.set(('users',OWNER),{**row.data,'status':'erasure_pending'})
        docs.run_transaction(freeze)
    with pytest.raises(DocumentConflict):
        service.finish(OWNER,progress['job_id'])
    assert docs.get(('projects',stable_project_id('inline_fence'))) is None


@pytest.mark.parametrize('first_inline,second_inline', [(False,True),(True,False)])
def test_partial_import_continues_across_inline_flag_change(setup, first_inline, second_inline):
    one, make, docs, _ = setup
    first = PackageImport(one.projects, inline_blocks=first_inline)
    progress = first.begin(b'package', OWNER, operation='partial_version_retry')
    progress = first.step(OWNER,progress['job_id'],0)
    assert progress['next_batch'] == 1 and not progress['ready']
    later = PackageImport(make().projects, inline_blocks=second_inline)
    assert later.step(OWNER,progress['job_id'],0) == progress
    progress = finish_steps(later, progress)
    result=later.finish(OWNER,progress['job_id'])
    rows=make().projects.domain.list('manuscript_block',{'project_id':result['project']['id']},user_id=OWNER)
    assert len(rows)==130
    assert sorted(r['text_content'] for r in rows)==sorted(f'Synthetic paragraph {i}' for i in range(130))
    physical=docs.query_collection(('projects',result['project']['id'],'blocks'))
    assert any('payload_object' in row.data for row in physical)
    assert any('payload_object' not in row.data for row in physical)


@pytest.mark.parametrize('enable,ready,expected', [(None,None,False),('1',None,False),
    (None,'1',False),('1','0',False),('true','1',False),('1','true',False),('1','1',True)])
def test_http_rollout_requires_both_exact_operator_flags(monkeypatch, enable, ready, expected):
    from hosted.package_import import inline_import_enabled
    for name,value in [('BREADMAKER_HOSTED_INLINE_IMPORT_BLOCKS',enable),
                       ('BREADMAKER_HOSTED_INLINE_IMPORT_INDEXES_READY',ready)]:
        monkeypatch.delenv(name,raising=False)
        if value is not None:monkeypatch.setenv(name,value)
    assert inline_import_enabled() is expected
