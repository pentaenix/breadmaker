"""Asset Undo admits only canonical top-level product paths, without new limits."""
from copy import deepcopy
import json

import pytest

from hosted.action_journal import (ActionDocumentStore, ActionJournal, MAX_BYTES,
    MAX_RECORDS, _PRODUCT_COLLECTIONS, _require_product_path, bind_action)
from hosted.firestore.contracts import HostedValidationError
from hosted.manuscript_visibility import ASSET_REVISIONS_FIELD, HEAD_FIELD
from tests.test_v10353 import OWNER
from tests.test_v10594 import actor
from tests.test_v10596_revision_images import setup, reserve, assets


PID = 'a' * 32
AID = 'asset_' + '0123456789abcdef' * 4


def invalid_paths(pid):
    return [
        ('projects', 'b' * 32, 'assets', AID),
        ('projects', pid, 'assets'),
        ('projects', pid, 'assets', 'asset_' + 'f' * 63),
        ('projects', pid, 'assets', 'asset_' + 'f' * 65),
        ('projects', pid, 'assets', 'asset_' + 'F' * 64),
        ('projects', pid, 'assets', 'asset_' + 'g' * 64),
        ('projects', pid, 'assets', 'asset_' + 'é' * 64),
        ('projects', pid, 'assets', 'asset_' + 'f' * 63 + '\n'),
        ('projects', pid, 'assets', 'ASSET_' + 'f' * 64),
        ('projects', pid, 'assets', 'asset_legacy'),
        ('projects', pid, 'assets', AID, 'metadata', 'one'),
        ('projects', pid, 'assets', AID, 'participants', OWNER),
        ('projects', pid, 'assets', AID, 'members', OWNER),
        ('projects', pid, 'assets', AID, 'assets', AID),
        ('projects', pid, 'reviews', 'review_one', 'assets', AID),
        ('projects', pid, 'tasks', 'task_one', 'assets', AID),
        ('projects', pid, 'members', OWNER, 'assets', AID),
        ('projects', pid, 'asset_sources', AID),
        ('projects', pid, 'asset_source_receipts', AID),
        ('projects', pid, 'mutation_receipts', AID),
        ('projects', pid, 'members', OWNER),
        ('projects', pid, 'invitations', AID),
        ('projects', pid),
    ]


def test_only_exact_authorized_canonical_asset_path_is_newly_eligible():
    assert 'assets' not in _PRODUCT_COLLECTIONS
    for digest in ['0' * 64, 'f' * 64, '0123456789abcdef' * 4]:
        _require_product_path(('projects', PID, 'assets', 'asset_' + digest), PID, OWNER)
    for path in invalid_paths(PID):
        with pytest.raises(HostedValidationError, match='ENTITY_UNSUPPORTED'):
            _require_product_path(path, PID, OWNER)
    # Existing product and exact own-bookmark semantics stay intact.
    for path in [('projects', PID, 'tasks', 'task_one'),
                 ('projects', PID, 'reviews', 'review_one', 'snapshots', 'snapshot_one'),
                 ('projects', PID, 'members', OWNER, 'state', 'bookmark')]:
        _require_product_path(path, PID, OWNER)


@pytest.mark.parametrize('family', ['descendant', 'nested', 'invalid_id', 'other_project'])
def test_bound_action_rejects_asset_scope_changes_atomically(family):
    make, raw, objects, pid, tab = setup()
    path = {
        'descendant': ('projects', pid, 'assets', AID, 'metadata', 'one'),
        'nested': ('projects', pid, 'reviews', 'review_one', 'assets', AID),
        'invalid_id': ('projects', pid, 'assets', 'legacy_id'),
        'other_project': ('projects', 'b' * 32, 'assets', AID),
    }[family]
    with actor():
        service = make(); journal = ActionJournal(service.domain.documents)
        token = journal.begin(pid, OWNER)['token']
        before = deepcopy(raw.get(('projects', pid)))
        with bind_action(token, pid, OWNER), pytest.raises(HostedValidationError, match='ENTITY_UNSUPPORTED'):
            ActionDocumentStore(service.retained).run_transaction(lambda tx: tx.create(path, {'generation': 1}))
        assert raw.get(path) is None
        assert raw.get(('projects', pid)) == before
        assert raw.get(('hosted_actions', OWNER, 'tokens', token)).data['records'] == []


@pytest.mark.parametrize('family', ['descendant', 'nested', 'invalid_id', 'other_project'])
def test_restore_revalidates_asset_paths_before_any_write(family):
    make, raw, objects, pid, tab = setup()
    path = {
        'descendant': ('projects', pid, 'assets', AID, 'metadata', 'one'),
        'nested': ('projects', pid, 'reviews', 'review_one', 'assets', AID),
        'invalid_id': ('projects', pid, 'assets', 'legacy_id'),
        'other_project': ('projects', 'b' * 32, 'assets', AID),
    }[family]
    with actor():
        service = make(); journal = ActionJournal(service.domain.documents)
        token = journal.begin(pid, OWNER)['token']
        action_path = ('hosted_actions', OWNER, 'tokens', token)
        value = {'generation': 1, 'name': 'synthetic'}
        def corrupt(tx):
            current = tx.get(action_path)
            tx.create(path, value)
            tx.set(action_path, {**current.data, 'records': [
                {'path': list(path), 'before': None, 'after': value, 'high_water': 1}]})
        raw.run_transaction(corrupt)
        before_root, before_row = deepcopy(raw.get(('projects', pid))), deepcopy(raw.get(path))
        with pytest.raises(HostedValidationError, match='ENTITY_UNSUPPORTED'):
            journal.restore(pid, OWNER, token, 'rollback')
        assert raw.get(path) == before_row
        assert raw.get(('projects', pid)) == before_root
        assert raw.get(action_path).data['state'] == 'open'


def test_asset_record_count_limit_is_unchanged_and_atomic():
    assert MAX_RECORDS == 100 and MAX_BYTES == 450_000
    make, raw, objects, pid, tab = setup()
    with actor():
        service = make(); journal = ActionJournal(service.domain.documents)
        token = journal.begin(pid, OWNER)['token']
        before = deepcopy(raw.get(('projects', pid)))
        paths = [('projects', pid, 'assets', 'asset_' + format(index, '064x')) for index in range(MAX_RECORDS + 1)]
        def publish(tx):
            for path in paths:
                tx.create(path, {'generation': 1})
        with bind_action(token, pid, OWNER), pytest.raises(HostedValidationError, match='ACTION_TOO_LARGE'):
            ActionDocumentStore(service.retained).run_transaction(publish)
        assert raw.query_collection(('projects', pid, 'assets')) == []
        assert raw.get(('projects', pid)) == before
        assert raw.get(('hosted_actions', OWNER, 'tokens', token)).data['records'] == []


def test_real_asset_before_images_count_against_unchanged_journal_byte_limit():
    # 24 legitimate hidden metadata records with 64 long pending refs each fit
    # the record cap but exceed the existing byte cap. Promotion must not leak.
    make, raw, objects, pid, tab = setup()
    with actor():
        job, manifest, payloads = reserve(make, pid, tab, colors=tuple((i, 0, 0) for i in range(24)))
        service = make(); service.publish_sources(pid, OWNER, job, payloads)
        for offset in range(0, 24, 8):
            service.stage_sources(pid, OWNER, job, offset)
        prior_refs = [{'tab_id': 'tab_' + format(i, '03d') + 'x' * 150,
            'revision': 'revision_' + format(i, '03d') + 'y' * 148} for i in range(63)]
        def retained_refs(tx):
            for entry in manifest:
                path = ('projects', pid, 'assets', entry['asset_id'])
                row = tx.get(path)
                tx.set(path, {**row.data, ASSET_REVISIONS_FIELD: [*prior_refs, *row.data[ASSET_REVISIONS_FIELD]]})
        raw.run_transaction(retained_refs)
        proof = service.verify_for_publication(pid, OWNER, job)
        journal = ActionJournal(service.domain.documents); token = journal.begin(pid, OWNER)['token']
        before_root = deepcopy(raw.get(('projects', pid)))
        before_tab = deepcopy(raw.get(('projects', pid, 'tabs', tab)))
        before_assets = deepcopy(raw.query_collection(('projects', pid, 'assets')))
        with bind_action(token, pid, OWNER):
            def publish(tx):
                changes = service.publication_changes(tx, pid, OWNER, job, proof)
                assert len(changes) == 24 < MAX_RECORDS
                images = [{'before': tx.get(path).data, 'after': value} for path, value, _ in changes]
                assert len(json.dumps(images).encode()) > MAX_BYTES
                tab_row = tx.get(('projects', pid, 'tabs', tab))
                tx.set(tab_row.path, {**tab_row.data, HEAD_FIELD: job, 'generation': tab_row.data['generation'] + 1})
                for path, value, _ in changes:
                    tx.set(path, value)
            with pytest.raises(HostedValidationError, match='ACTION_TOO_LARGE'):
                ActionDocumentStore(service.retained).run_transaction(publish)
        assert raw.get(('projects', pid)) == before_root
        assert raw.get(('projects', pid, 'tabs', tab)) == before_tab
        assert raw.query_collection(('projects', pid, 'assets')) == before_assets
        assert raw.get(('hosted_actions', OWNER, 'tokens', token)).data['records'] == []
        for entry in manifest:
            assert not assets(make, pid).exists(entry['api_path'])
        assert len(objects.list_prefix(f'projects/{pid}/assets/')) == 24
