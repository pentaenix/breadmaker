"""Resumable image bytes survive legacy adoption without exposing asset URLs."""
from copy import deepcopy
from threading import Barrier, Lock

import pytest

from hosted.account_lifecycle import AccountLifecycleDocumentStore, AccountLifecycleObjectStore
from hosted.action_journal import bind_action
from hosted.firestore.contracts import DocumentConflict, HostedValidationError, ProjectUnavailable
from hosted.manuscript_visibility import FORMAT_FIELD
from hosted.revision_images import RevisionImageStager
from hosted.runtime_services import HostedCommittedAssetStore
from hosted.storage.contracts import ObjectGenerationConflict
from tests.test_v10594 import ACTOR, actor
from tests.test_v10353 import OWNER
from tests.test_v10596_revision_images import setup, reserve, assets


def legacy(raw, pid):
    root = raw.get(('projects', pid))
    raw.run_transaction(lambda tx: tx.set(root.path,
        {key: ('active' if key == 'status' else value) for key, value in root.data.items() if key != FORMAT_FIELD}))


def adopt(raw, pid):
    # The parent owns the bounded adoption protocol; this helper-only synthetic
    # fixture performs its final monotonic codec change without changing generation.
    root = raw.get(('projects', pid))
    raw.run_transaction(lambda tx: tx.set(root.path, {**root.data, FORMAT_FIELD: 1, 'status': 'active_manuscript_v1'}))


def test_sources_survive_multiple_replicas_and_pre_adoption_urls_remain_absent(monkeypatch):
    make, raw, objects, pid, tab = setup()
    legacy(raw, pid)
    with actor():
        job, manifest, payloads = reserve(make, pid, tab, colors=tuple((i, 0, 0) for i in range(17)))
        before = deepcopy(raw.get(('projects', pid)))
        source_receipts = make().publish_sources(pid, OWNER, job, payloads)
        assert make().publish_sources(pid, OWNER, job, payloads) == source_receipts
        path = ('projects', pid, 'manuscript_import_jobs', job)
        data = raw.get(path).data
        assert data['asset_source_receipts'] == source_receipts
        assert data['asset_receipts'] == [] and data['asset_next_index'] == 0
        assert raw.get(('projects', pid)) == before
        assert raw.query_collection(('projects', pid, 'assets')) == []
        # Even an unaware legacy committed reader cannot discover source bytes:
        # there is deliberately no metadata row to authorize its URL.
        legacy_assets = HostedCommittedAssetStore(make().view.store, make().domain.objects, lambda: OWNER, lambda: pid)
        for entry in manifest:
            assert not legacy_assets.exists(entry['api_path'])
            with pytest.raises(FileNotFoundError):
                legacy_assets.read_blob(entry['api_path'])
        with pytest.raises(HostedValidationError, match='FORMAT_REQUIRED'):
            make().stage_sources(pid, OWNER, job, 0)
        del payloads  # Nothing process-local is needed through adoption or steps.
        adopt(raw, pid)
        calls = []; original = objects.read_chunks
        def read(name, **kwargs):
            calls.append((name, kwargs['if_generation_match']))
            return original(name, **kwargs)
        monkeypatch.setattr(objects, 'read_chunks', read)
        for offset, expected in [(0, 8), (8, 16), (16, 17)]:
            calls.clear()
            status = make().stage_sources(pid, OWNER, job, offset)
            assert status == {'asset_next_index': expected, 'asset_total': 17, 'assets_ready': expected == 17}
            assert len(calls) == expected - offset <= 8
            assert raw.get(path).data['asset_receipts'] == source_receipts[:expected]
            assert len(raw.query_collection(('projects', pid, 'assets'))) == expected
            assert make().view.query_collection(('projects', pid, 'assets')) == []
            calls.clear()
            assert make().stage_sources(pid, OWNER, job, offset) == status
            assert calls == []
            if expected < 17:
                with pytest.raises(DocumentConflict, match='NOT_STAGED'):
                    make().verify_for_publication(pid, OWNER, job)
        proof = make().verify_for_publication(pid, OWNER, job)
        with bind_action('synthetic', pid, OWNER):
            changes = make().retained.run_transaction(lambda tx: make().publication_changes(tx, pid, OWNER, job, proof))
        assert len(changes) == 17
        assert make().stage_sources(pid, OWNER, job, 17)['assets_ready']


def test_source_upload_threads_preserve_actor_and_never_exceed_eight(monkeypatch):
    make, raw, objects, pid, tab = setup()
    legacy(raw, pid)
    with actor():
        job, manifest, payloads = reserve(make, pid, tab, colors=tuple((i, 0, 0) for i in range(17)))
        service = make(); original = service.publisher.publish_bytes
        lock, barrier = Lock(), Barrier(8)
        stats = {'active': 0, 'maximum': 0, 'calls': 0}
        def publish(*args, **kwargs):
            assert ACTOR.get() == OWNER
            with lock:
                stats['active'] += 1; stats['calls'] += 1
                number = stats['calls']; stats['maximum'] = max(stats['maximum'], stats['active'])
            try:
                if number <= 8:
                    barrier.wait(timeout=3)
                return original(*args, **kwargs)
            finally:
                with lock:
                    stats['active'] -= 1
        monkeypatch.setattr(service.publisher, 'publish_bytes', publish)
        service.publish_sources(pid, OWNER, job, payloads)
        assert stats == {'active': 0, 'maximum': 8, 'calls': 17}
        assert raw.query_collection(('projects', pid, 'assets')) == []
    assert ACTOR.get() is None


@pytest.mark.parametrize('change', ['foreign_key', 'hash', 'generation', 'extra_field', 'manifest_project', 'size'])
def test_every_source_pointer_is_validated_before_any_object_read(change, monkeypatch):
    make, raw, objects, pid, tab = setup()
    with actor():
        job, manifest, payloads = reserve(make, pid, tab, colors=tuple((i, 0, 0) for i in range(9)))
        make().publish_sources(pid, OWNER, job, payloads)
        path = ('projects', pid, 'manuscript_import_jobs', job)
        def tamper(tx):
            data = deepcopy(tx.get(path).data)
            # Corrupt the last pointer, beyond the first eight-image batch. All
            # pointers must be validated before even the first safe read starts.
            receipt = data['asset_source_receipts'][-1]
            if change == 'foreign_key': receipt['object_name'] = receipt['object_name'].replace(pid, 'f' * 32)
            if change == 'hash': receipt['object_sha256'] = 'f' * 64
            if change == 'generation': receipt['object_generation'] = True
            if change == 'extra_field': receipt['url'] = 'https://invalid.example/'
            if change == 'size': receipt['object_size'] += 1
            if change == 'manifest_project':
                receipt['object_name'] = receipt['object_name'].replace(pid, 'f' * 32)
                data['asset_manifest'][-1]['object_name'] = receipt['object_name']
            tx.set(path, data)
        raw.run_transaction(tamper)
        def unread(*args, **kwargs):
            raise AssertionError('Invalid source pointer must not cause any object read')
        monkeypatch.setattr(objects, 'read_chunks', unread)
        with pytest.raises(HostedValidationError):
            make().stage_sources(pid, OWNER, job, 0)
        assert raw.query_collection(('projects', pid, 'assets')) == []
        assert raw.get(path).data['asset_next_index'] == 0


@pytest.mark.parametrize('change', ['hash', 'pinned_generation', 'cancel', 'generation', 'erasure', 'revoke'])
def test_source_step_failures_never_advance_cursor_or_publish_batch(change, monkeypatch):
    make, raw, objects, pid, tab = setup()
    with actor():
        job, manifest, payloads = reserve(make, pid, tab, colors=('red', 'blue'))
        sources = make().publish_sources(pid, OWNER, job, payloads)
        path = ('projects', pid, 'manuscript_import_jobs', job)
        original = objects.read_chunks
        changed = False
        def read(name, **kwargs):
            nonlocal changed
            chunks = original(name, **kwargs)
            if not changed:
                changed = True
                if change == 'hash':
                    return [b'wrong']
                def alter(tx):
                    if change == 'cancel': tx.set(path, {**tx.get(path).data, 'state': 'cancelled'})
                    if change == 'generation':
                        root = tx.get(('projects', pid)); tx.set(root.path, {**root.data, 'generation': root.data['generation'] + 1})
                    if change == 'erasure':
                        user = tx.get(('users', OWNER)); tx.set(user.path, {**user.data, 'status': 'erasure_pending'})
                    if change == 'revoke': tx.delete(('projects', pid, 'members', OWNER))
                raw.run_transaction(alter)
            return chunks
        if change == 'pinned_generation':
            receipt = sources[0]
            objects.write_chunks(receipt['object_name'], [payloads[receipt['asset_id']]],
                max_bytes=receipt['object_size'], content_type='image/png', if_generation_match=receipt['object_generation'])
        else:
            monkeypatch.setattr(objects, 'read_chunks', read)
        with pytest.raises((HostedValidationError, DocumentConflict, ProjectUnavailable, ObjectGenerationConflict)):
            make().stage_sources(pid, OWNER, job, 0)
        assert raw.query_collection(('projects', pid, 'assets')) == []
        assert raw.get(path).data['asset_next_index'] == 0
        assert raw.get(path).data['asset_receipts'] == []
        assert len(objects.list_prefix(f'projects/{pid}/assets/')) == 2


def test_source_publish_interruption_leaves_only_orphans_and_retry_recovers(monkeypatch):
    make, raw, objects, pid, tab = setup()
    legacy(raw, pid)
    with actor():
        job, manifest, payloads = reserve(make, pid, tab, colors=('red', 'blue'))
        service = make(); original = service.publisher.publish_bytes
        def interrupt(*args, **kwargs):
            result = original(*args, **kwargs)
            raise RuntimeError('synthetic post-upload interruption')
        monkeypatch.setattr(service.publisher, 'publish_bytes', interrupt)
        with pytest.raises(RuntimeError, match='interruption'):
            service.publish_sources(pid, OWNER, job, payloads)
        path = ('projects', pid, 'manuscript_import_jobs', job)
        assert 'asset_source_receipts' not in raw.get(path).data
        assert raw.query_collection(('projects', pid, 'assets')) == []
        assert len(objects.list_prefix(f'projects/{pid}/assets/')) == 2
        assert len(make().publish_sources(pid, OWNER, job, payloads)) == 2


def test_source_publish_rechecks_authority_after_upload(monkeypatch):
    make, raw, objects, pid, tab = setup()
    legacy(raw, pid)
    with actor():
        job, manifest, payloads = reserve(make, pid, tab)
        service = make(); original = service.publisher.publish_bytes
        path = ('projects', pid, 'manuscript_import_jobs', job)
        def publish(*args, **kwargs):
            result = original(*args, **kwargs)
            raw.run_transaction(lambda tx: tx.set(path, {**tx.get(path).data, 'state': 'cancelled'}))
            return result
        monkeypatch.setattr(service.publisher, 'publish_bytes', publish)
        with pytest.raises(DocumentConflict, match='NOT_STAGING'):
            service.publish_sources(pid, OWNER, job, payloads)
        assert 'asset_source_receipts' not in raw.get(path).data
        assert raw.query_collection(('projects', pid, 'assets')) == []
        assert objects.stat(manifest[0]['object_name']) is not None


def test_offsets_and_cursor_proofs_are_exact_and_out_of_order_steps_do_not_read(monkeypatch):
    make, raw, objects, pid, tab = setup()
    with actor():
        job, manifest, payloads = reserve(make, pid, tab, colors=tuple((i, 0, 0) for i in range(17)))
        make().publish_sources(pid, OWNER, job, payloads)
        with monkeypatch.context() as patch:
            patch.setattr(objects, 'read_chunks', lambda *a, **k: (_ for _ in ()).throw(AssertionError('Unexpected read')))
            for index in [-1, True, 1, 7, 18]:
                with pytest.raises(HostedValidationError, match='BATCH_INVALID'):
                    make().stage_sources(pid, OWNER, job, index)
            for index in [8, 16, 17]:
                with pytest.raises(DocumentConflict, match='OUT_OF_ORDER'):
                    make().stage_sources(pid, OWNER, job, index)
        make().stage_sources(pid, OWNER, job, 0)
        path = ('projects', pid, 'manuscript_import_jobs', job)
        good = deepcopy(raw.get(path).data)
        for cursor, receipts in [(True, good['asset_receipts']), (7, good['asset_receipts']), (16, good['asset_receipts']),
                (8, []), (8, list(reversed(good['asset_receipts'])))]:
            raw.run_transaction(lambda tx: tx.set(path, {**good, 'asset_next_index': cursor, 'asset_receipts': receipts}))
            with pytest.raises(HostedValidationError, match='CURSOR_INVALID'):
                make().stage_sources(pid, OWNER, job, 8)


def test_concurrent_duplicate_step_cas_commits_metadata_once(monkeypatch):
    make, raw, objects, pid, tab = setup()
    with actor():
        job, manifest, payloads = reserve(make, pid, tab, colors=tuple((i, 0, 0) for i in range(9)))
        make().publish_sources(pid, OWNER, job, payloads)
        service = make(); original = service._parallel
        def concurrent(fn, entries):
            result = original(fn, entries)
            assert make().stage_sources(pid, OWNER, job, 0)['asset_next_index'] == 8
            return result
        monkeypatch.setattr(service, '_parallel', concurrent)
        assert service.stage_sources(pid, OWNER, job, 0)['asset_next_index'] == 8
        path = ('projects', pid, 'manuscript_import_jobs', job)
        assert raw.get(path).data['asset_next_index'] == 8
        rows = raw.query_collection(('projects', pid, 'assets'))
        assert len(rows) == 8 and all(row.data['generation'] == 1 for row in rows)
        assert make().stage_sources(pid, OWNER, job, 8)['assets_ready']


def test_object_fence_must_match_the_same_document_actor_boundary():
    make, raw, objects, pid, tab = setup()
    with actor():
        service = make(); domain = service.domain
        for invalid in [objects, AccountLifecycleObjectStore(objects, AccountLifecycleDocumentStore(raw, actor=ACTOR.get))]:
            domain.objects = invalid
            with pytest.raises(HostedValidationError, match='OBJECT_ERASURE_FENCE_REQUIRED'):
                RevisionImageStager(domain, service.authorize)


def test_empty_sources_complete_without_objects_or_metadata():
    make, raw, objects, pid, tab = setup()
    with actor():
        job, manifest, payloads = reserve(make, pid, tab, colors=())
        assert make().publish_sources(pid, OWNER, job, payloads) == []
        assert make().stage_sources(pid, OWNER, job, 0) == {'asset_next_index': 0, 'asset_total': 0, 'assets_ready': True}
        assert make().verify_for_publication(pid, OWNER, job).receipts == ()
        assert raw.query_collection(('projects', pid, 'assets')) == []
        assert objects.list_prefix(f'projects/{pid}/assets/') == []


def test_later_batch_collision_keeps_prior_hidden_batch_and_rolls_back_current_batch():
    make, raw, objects, pid, tab = setup()
    with actor():
        job, manifest, payloads = reserve(make, pid, tab, colors=tuple((i, 0, 0) for i in range(17)))
        make().publish_sources(pid, OWNER, job, payloads)
        make().stage_sources(pid, OWNER, job, 0)
        collision = ('projects', pid, 'assets', manifest[15]['asset_id'])
        raw.run_transaction(lambda tx: tx.create(collision, {'name': 'wrong'}))
        with pytest.raises(DocumentConflict, match='COLLISION'):
            make().stage_sources(pid, OWNER, job, 8)
        path = ('projects', pid, 'manuscript_import_jobs', job)
        assert raw.get(path).data['asset_next_index'] == 8
        assert len(raw.get(path).data['asset_receipts']) == 8
        assert len(raw.query_collection(('projects', pid, 'assets'))) == 9
        for entry in manifest[:15]:
            assert not assets(make, pid).exists(entry['api_path'])
        assert len(objects.list_prefix(f'projects/{pid}/assets/')) == 17


def test_changed_source_receipt_between_read_and_commit_refuses_cursor_advance(monkeypatch):
    make, raw, objects, pid, tab = setup()
    with actor():
        job, manifest, payloads = reserve(make, pid, tab, colors=('red', 'blue'))
        make().publish_sources(pid, OWNER, job, payloads)
        service = make(); original = service._parallel
        path = ('projects', pid, 'manuscript_import_jobs', job)
        def changed(fn, entries):
            result = original(fn, entries)
            def alter(tx):
                data = deepcopy(tx.get(path).data)
                data['asset_source_receipts'][-1]['object_generation'] += 1
                tx.set(path, data)
            raw.run_transaction(alter)
            return result
        monkeypatch.setattr(service, '_parallel', changed)
        with pytest.raises(DocumentConflict, match='RECEIPT_CHANGED'):
            service.stage_sources(pid, OWNER, job, 0)
        assert raw.get(path).data['asset_next_index'] == 0
        assert raw.query_collection(('projects', pid, 'assets')) == []


def test_final_verification_requires_complete_sources_and_finished_cursor():
    make, raw, objects, pid, tab = setup()
    with actor():
        job, manifest, payloads = reserve(make, pid, tab)
        make().stage(pid, OWNER, job, payloads)
        path = ('projects', pid, 'manuscript_import_jobs', job)
        good = deepcopy(raw.get(path).data)
        for field in ['asset_source_receipts', 'asset_next_index']:
            data = {key: value for key, value in good.items() if key != field}
            raw.run_transaction(lambda tx: tx.set(path, data))
            with pytest.raises((HostedValidationError, DocumentConflict)):
                make().verify_for_publication(pid, OWNER, job)
