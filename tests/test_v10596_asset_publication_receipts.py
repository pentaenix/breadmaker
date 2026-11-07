"""Append-only publication evidence preserves images captured by real History."""
from copy import deepcopy
import json

import pytest

from hosted.action_journal import ActionDocumentStore, ActionJournal, _require_product_path, bind_action
from hosted.firestore.contracts import DocumentAlreadyExists, DocumentConflict, HostedValidationError
from hosted.manuscript_visibility import (ASSET_PUBLICATIONS_COLLECTION, ASSET_REVISIONS_FIELD, HEAD_FIELD,
    asset_publication_data)
from hosted.runtime_services import HostedHistoryStore
from tests.test_v10353 import OWNER
from tests.test_v10594 import actor
from tests.test_v10596_revision_images import setup, reserve, assets, png


def prepared(colors=('red',)):
    make, raw, objects, pid, tab = setup()
    with actor():
        job, manifest, payloads = reserve(make, pid, tab, colors=colors)
        service = make()
        service.publish_sources(pid, OWNER, job, payloads)
        for offset in range(0, len(manifest), 8):
            service.stage_sources(pid, OWNER, job, offset)
    return make, raw, objects, pid, tab, job, manifest, payloads


def promote(make, pid, tab, job, *, finish=True, manipulate=None):
    service = make(); journal = ActionJournal(service.domain.documents)
    token = journal.begin(pid, OWNER)['token']
    proof = service.verify_for_publication(pid, OWNER, job)
    with bind_action(token, pid, OWNER):
        def commit(tx):
            changes = service.publication_changes(tx, pid, OWNER, job, proof)
            receipts = service.publication_receipts(tx, pid, OWNER, job, proof)
            previous = tx.get(('projects', pid, 'tabs', tab))
            after = {**previous.data, HEAD_FIELD: job, 'generation': previous.data['generation'] + 1}
            if manipulate:
                return manipulate(tx, changes, receipts, previous.path, after)
            tx.set(previous.path, after)
            for path, value, _ in changes:
                tx.set(path, value)
            for path, value in receipts:
                tx.create_asset_publication(path, value)
        ActionDocumentStore(service.retained).run_transaction(commit)
    if finish:
        journal.finish(pid, OWNER, token)
    return journal, token


@pytest.mark.parametrize('direction', ['undo', 'rollback'])
def test_real_history_capture_keeps_image_after_import_undo_or_rollback(direction):
    make, raw, objects, pid, tab, job, manifest, payloads = prepared()
    with actor():
        entry = manifest[0]; asset_path = ('projects', pid, 'assets', entry['asset_id'])
        assert not assets(make, pid).exists(entry['api_path'])
        assert raw.query_collection(('projects', pid, ASSET_PUBLICATIONS_COLLECTION)) == []
        before_tab = deepcopy(raw.get(('projects', pid, 'tabs', tab)).data)
        journal, token = promote(make, pid, tab, job, finish=direction == 'undo')
        receipt_path = ('projects', pid, ASSET_PUBLICATIONS_COLLECTION, entry['asset_id'])
        receipt = deepcopy(raw.get(receipt_path))
        assert receipt.data == asset_publication_data(pid, dict(raw.get(asset_path).data))
        assert ASSET_REVISIONS_FIELD not in raw.get(asset_path).data
        action_before = deepcopy(raw.get(('hosted_actions', OWNER, 'tokens', token)).data)
        assert len(action_before['records']) == 2
        assert all(row['path'][2] != ASSET_PUBLICATIONS_COLLECTION for row in action_before['records'])
        generation = raw.get(('projects', pid)).data['generation']
        history = HostedHistoryStore(make().domain.documents, make().domain.objects, lambda: OWNER, lambda: pid)
        envelope = json.dumps({'blocks': [{'block_type': 'image', 'image_path': entry['api_path']}]}).encode()
        saved = history.create_revision(envelope, {'summary': 'Synthetic image history'})
        assert raw.get(('projects', pid)).data['generation'] == generation
        published_generation = raw.get(asset_path).data['generation']
        assert journal.restore(pid, OWNER, token, direction)['changed']
        assert raw.get(('projects', pid, 'tabs', tab)).data.get(HEAD_FIELD) == before_tab.get(HEAD_FIELD)
        assert ASSET_REVISIONS_FIELD in raw.get(asset_path).data
        assert raw.get(asset_path).data['generation'] > published_generation
        assert raw.get(receipt_path) == receipt
        _, captured = history.read_revision(saved['revision_id'])
        assert captured == envelope
        url = json.loads(captured)['blocks'][0]['image_path']
        assert assets(make, pid).read_blob(url) == payloads[entry['asset_id']]
        assert make().view.get(asset_path) is not None
        assert len(make().view.query_collection(('projects', pid, 'assets'))) == 1
        assert len(make().view.query_collection_group('assets', field='sha256', value=entry['sha256'])) == 1
        assert make().view.run_transaction(lambda tx: tx.count_collection(('projects', pid, 'assets'))) == 1
        if direction == 'undo':
            assert journal.restore(pid, OWNER, token, 'redo')['changed']
            assert assets(make, pid).read_blob(url) == payloads[entry['asset_id']]
            assert raw.get(receipt_path) == receipt
        assert len(objects.list_prefix(f'projects/{pid}/assets/')) == 1


@pytest.mark.parametrize('field,value', [
    ('format', True), ('format', 2), ('project_id', 'f' * 32),
    ('id', 'asset_' + 'f' * 64), ('asset_id', 'asset_' + 'f' * 64),
    ('name', 'img_' + 'f' * 24 + '.png'), ('api_path', '/api/assets/other.png'),
    ('sha256', 'f' * 64), ('object_name', 'projects/' + 'f' * 32 + '/assets/sha256/' + 'f' * 64 + '.png'),
    ('object_generation', True), ('object_generation', 99999), ('object_sha256', 'f' * 64),
    ('object_size', True), ('object_size', 99999), ('unexpected', 'field')])
def test_corrupt_or_foreign_receipts_fail_closed_after_undo(field, value):
    make, raw, objects, pid, tab, job, manifest, payloads = prepared()
    with actor():
        journal, token = promote(make, pid, tab, job)
        journal.restore(pid, OWNER, token, 'undo')
        entry = manifest[0]; path = ('projects', pid, ASSET_PUBLICATIONS_COLLECTION, entry['asset_id'])
        raw.run_transaction(lambda tx: tx.set(path, {**tx.get(path).data, field: value}))
        assert not assets(make, pid).exists(entry['api_path'])
        assert make().view.get(('projects', pid, 'assets', entry['asset_id'])) is None
        with pytest.raises(FileNotFoundError):
            assets(make, pid).read_blob(entry['api_path'])


@pytest.mark.parametrize('change', ['missing_asset', 'changed_asset', 'wrong_receipt_path', 'missing_receipt_field'])
def test_receipt_needs_existing_exact_asset_and_exact_path(change):
    make, raw, objects, pid, tab, job, manifest, payloads = prepared()
    with actor():
        journal, token = promote(make, pid, tab, job)
        journal.restore(pid, OWNER, token, 'undo')
        entry = manifest[0]; path = ('projects', pid, ASSET_PUBLICATIONS_COLLECTION, entry['asset_id'])
        asset_path = ('projects', pid, 'assets', entry['asset_id'])
        def corrupt(tx):
            if change == 'missing_asset': tx.delete(asset_path)
            if change == 'changed_asset': tx.set(asset_path, {**tx.get(asset_path).data, 'object_generation': 99999})
            if change == 'wrong_receipt_path':
                tx.create(('projects', pid, ASSET_PUBLICATIONS_COLLECTION, 'asset_' + 'f' * 64), tx.get(path).data)
                tx.delete(path)
            if change == 'missing_receipt_field':
                tx.set(path, {key: value for key, value in tx.get(path).data.items() if key != 'object_size'})
        raw.run_transaction(corrupt)
        assert not assets(make, pid).exists(entry['api_path'])


@pytest.mark.parametrize('retained', [False, True])
@pytest.mark.parametrize('operation', ['create', 'set', 'delete'])
def test_generic_receipt_writes_are_forbidden_even_with_action_context(retained, operation):
    make, raw, objects, pid, tab, job, manifest, payloads = prepared()
    with actor():
        service = make(); entry = manifest[0]
        asset_path = ('projects', pid, 'assets', entry['asset_id'])
        path = ('projects', pid, ASSET_PUBLICATIONS_COLLECTION, entry['asset_id'])
        data = asset_publication_data(pid, dict(raw.get(asset_path).data))
        store = service.retained if retained else service.view
        def write(tx):
            method = getattr(tx, operation)
            method(path) if operation == 'delete' else method(path, data)
        for wrapped in [store, ActionDocumentStore(store)]:
            with bind_action('nonexistent_but_not_permission', pid, OWNER), pytest.raises(HostedValidationError, match='WRITE_FORBIDDEN'):
                wrapped.run_transaction(write)
        assert raw.get(path) is None
        assert not assets(make, pid).exists(entry['api_path'])


@pytest.mark.parametrize('mode', ['no_promotion', 'no_head', 'unchanged_head', 'foreign_head', 'wrong_asset', 'wrong_pointer', 'extra_field', 'foreign_receipt', 'descendant', 'override_after_capability'])
def test_capability_requires_exact_same_transaction_promotion_and_head(mode):
    make, raw, objects, pid, tab, job, manifest, payloads = prepared()
    with actor():
        before_root = deepcopy(raw.get(('projects', pid)))
        before_assets = deepcopy(raw.query_collection(('projects', pid, 'assets')))
        def attempt(tx, changes, receipts, tab_path, after):
            if mode not in {'no_head', 'unchanged_head'}:
                tx.set(tab_path, {**after, HEAD_FIELD: 'different_job'} if mode == 'foreign_head' else after)
            elif mode == 'unchanged_head':
                tx.set(tab_path, dict(tx.get(tab_path).data))
            if mode != 'no_promotion':
                for path, value, _ in changes:
                    tx.set(path, value)
            for path, value in receipts:
                if mode == 'wrong_asset': path = (*path[:-1], 'asset_' + 'f' * 64)
                if mode == 'foreign_receipt': path = ('projects', 'f' * 32, *path[2:])
                if mode == 'descendant': path = (*path, 'children', 'one')
                if mode == 'wrong_pointer': value = {**value, 'object_generation': value['object_generation'] + 1}
                if mode == 'extra_field': value = {**value, 'job': job}
                tx.create_asset_publication(path, value)
                if mode == 'override_after_capability': tx.set(path, {**value, 'format': 2})
        with pytest.raises(HostedValidationError, match='PUBLICATION_'):
            promote(make, pid, tab, job, manipulate=attempt)
        assert raw.get(('projects', pid)) == before_root
        assert raw.query_collection(('projects', pid, 'assets')) == before_assets
        assert raw.query_collection(('projects', pid, ASSET_PUBLICATIONS_COLLECTION)) == []
        # A failed capability grant must not leak to later ordinary transactions.
        receipt_path = ('projects', pid, ASSET_PUBLICATIONS_COLLECTION, manifest[0]['asset_id'])
        with pytest.raises(HostedValidationError, match='WRITE_FORBIDDEN'):
            make().retained.run_transaction(lambda tx: tx.create(receipt_path, {}))


def test_missing_actor_cannot_use_publication_capability():
    make, raw, objects, pid, tab, job, manifest, payloads = prepared()
    with actor():
        service = make(); journal = ActionJournal(service.domain.documents)
        token = journal.begin(pid, OWNER)['token']; proof = service.verify_for_publication(pid, OWNER, job)
        with bind_action(token, pid, OWNER):
            def plans(tx):
                return service.publication_changes(tx, pid, OWNER, job, proof), service.publication_receipts(tx, pid, OWNER, job, proof)
            changes, receipts = service.retained.run_transaction(plans)
        before = raw.get(('projects', pid, 'tabs', tab))
    with actor(None), bind_action(token, pid, OWNER), pytest.raises(HostedValidationError, match='AUTHENTICATED_ACTOR_REQUIRED'):
        def write(tx):
            tx.set(before.path, {**before.data, HEAD_FIELD: job, 'generation': before.data['generation'] + 1})
            for path, value, _ in changes: tx.set(path, value)
            for path, value in receipts: tx.create_asset_publication(path, value)
        ActionDocumentStore(service.retained).run_transaction(write)
    assert raw.query_collection(('projects', pid, ASSET_PUBLICATIONS_COLLECTION)) == []


def test_receipt_reuse_is_read_only_and_conflicts_cannot_be_overwritten():
    make, raw, objects, pid, tab, job, manifest, payloads = prepared()
    with actor():
        journal, token = promote(make, pid, tab, job)
        receipt_path = ('projects', pid, ASSET_PUBLICATIONS_COLLECTION, manifest[0]['asset_id'])
        original = deepcopy(raw.get(receipt_path))
        journal.restore(pid, OWNER, token, 'undo')
        other, _, bodies = reserve(make, pid, tab, job='new_import')
        service = make(); service.stage(pid, OWNER, other, bodies)
        proof = service.verify_for_publication(pid, OWNER, other)
        with bind_action('synthetic', pid, OWNER):
            assert service.retained.run_transaction(lambda tx: service.publication_receipts(tx, pid, OWNER, other, proof)) == []
        assert raw.get(receipt_path) == original
        raw.run_transaction(lambda tx: tx.set(receipt_path, {**original.data, 'object_generation': 99999}))
        with bind_action('synthetic', pid, OWNER), pytest.raises(DocumentConflict, match='PUBLICATION_CONFLICT'):
            service.retained.run_transaction(lambda tx: service.publication_receipts(tx, pid, OWNER, other, proof))
        assert raw.get(receipt_path).data['object_generation'] == 99999


def test_receipts_are_not_restorable_or_rewritable_and_asset_aba_checks_stay_strict():
    make, raw, objects, pid, tab, job, manifest, payloads = prepared()
    with actor():
        journal, token = promote(make, pid, tab, job)
        entry = manifest[0]
        path = ('projects', pid, ASSET_PUBLICATIONS_COLLECTION, entry['asset_id'])
        with pytest.raises(HostedValidationError, match='ENTITY_UNSUPPORTED'):
            _require_product_path(path, pid, OWNER)
        with bind_action(token, pid, OWNER), pytest.raises(DocumentAlreadyExists):
            ActionDocumentStore(make().retained).run_transaction(lambda tx: tx.create_asset_publication(path, raw.get(path).data))
        asset_path = ('projects', pid, 'assets', entry['asset_id'])
        raw.run_transaction(lambda tx: tx.set(asset_path, {**tx.get(asset_path).data, 'generation': 99999}))
        with pytest.raises(DocumentConflict, match='RECORD_CHANGED'):
            journal.restore(pid, OWNER, token, 'undo')


def test_sixty_four_receipts_are_bounded_physical_writes_not_undo_records():
    make, raw, objects, pid, tab, job, manifest, payloads = prepared(tuple((i, 0, 0) for i in range(64)))
    with actor():
        journal, token = promote(make, pid, tab, job)
        action = raw.get(('hosted_actions', OWNER, 'tokens', token)).data
        publications = raw.query_collection(('projects', pid, ASSET_PUBLICATIONS_COLLECTION))
        assert len(publications) == 64
        assert len(action['records']) == 65  # 64 promoted assets plus one head.
        assert len(json.dumps([dict(row.data) for row in publications]).encode()) < 64 * 1000
        journal.restore(pid, OWNER, token, 'undo')
        assert len(raw.query_collection(('projects', pid, ASSET_PUBLICATIONS_COLLECTION))) == 64
        assert len(make().view.query_collection(('projects', pid, 'assets'))) == 64


def test_publication_capability_rejects_more_than_sixty_four_physical_receipts():
    make, raw, objects, pid, tab, job, manifest, payloads = prepared()
    with actor():
        service = make(); journal = ActionJournal(service.domain.documents)
        token = journal.begin(pid, OWNER)['token']
        before = deepcopy(raw.get(('projects', pid)))
        def too_many(tx):
            for index in range(65):
                tx.create_asset_publication(('projects', pid, ASSET_PUBLICATIONS_COLLECTION,
                    'asset_' + format(index, '064x')), {})
        with bind_action(token, pid, OWNER), pytest.raises(HostedValidationError, match='PUBLICATION_LIMIT'):
            ActionDocumentStore(service.retained).run_transaction(too_many)
        assert raw.get(('projects', pid)) == before
        assert raw.query_collection(('projects', pid, ASSET_PUBLICATIONS_COLLECTION)) == []
