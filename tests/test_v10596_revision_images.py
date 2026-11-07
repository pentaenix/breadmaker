"""Synthetic, inactive image staging. No production composition is changed."""
from copy import deepcopy
from io import BytesIO
import time

import pytest
from PIL import Image

from hosted.action_journal import ActionDocumentStore, ActionJournal, bind_action
from hosted.domain_store import HostedDomainStore
from hosted.firestore.contracts import DocumentConflict, DocumentNotFound, HostedValidationError, ProjectUnavailable
from hosted.firestore.repositories import HostedProjectRepository
from hosted.manuscript_visibility import ASSET_REVISIONS_FIELD, FORMAT_FIELD, HEAD_FIELD, ManuscriptDocumentStore
from hosted.revision_images import RevisionImageStager, prepare_revision_images
from hosted.runtime_services import HostedCommittedAssetStore
from hosted.storage.contracts import ObjectIntegrityError, ObjectGenerationConflict
from tests.test_v10594 import actor, fenced_replicas
from tests.test_v10353 import OWNER


def png(color='red'):
    out = BytesIO()
    Image.new('RGB', (8, 8), color).save(out, format='PNG')
    return out.getvalue()


def setup():
    make, raw, objects, pid = fenced_replicas()
    def replica():
        source = make()
        view = ManuscriptDocumentStore(source.documents)
        domain = HostedDomainStore(ActionDocumentStore(view), source.domain.objects)
        def authorize(tx, project, uid, job):
            root, member = HostedProjectRepository(None)._require_member(tx, project, uid)
            if member.get('role') not in {'owner', 'editor'}:
                raise DocumentNotFound('HOSTED_IMPORT_NOT_FOUND')
            owners = tx.query_collection(('projects', project, 'members'), field='role', value='owner')
            for user in {uid, *(row.path[-1] for row in owners)}:
                profile = tx.get(('users', user))
                if (profile is None or profile.data.get('status', 'active') != 'active'
                        or profile.data.get('_bm_erasure_operation') or tx.get(('account_erasure_locks', user)) is not None):
                    raise DocumentConflict('HOSTED_ACCOUNT_ERASURE_PENDING')
            row = tx.get(('projects', project, 'manuscript_import_jobs', job))
            if row is None:
                raise DocumentNotFound('HOSTED_IMPORT_NOT_FOUND')
            return dict(row.data)
        return RevisionImageStager(domain, authorize)
    root = raw.get(('projects', pid))
    raw.run_transaction(lambda tx: tx.set(root.path, {**root.data, FORMAT_FIELD: 1, 'status': 'active_manuscript_v1'}))
    tab = raw.query_collection(('projects', pid, 'tabs'))[0].path[-1]
    return replica, raw, objects, pid, tab


def reserve(make, pid, tab, job='image_job', colors=('red',)):
    service = make()
    manifest, payloads = prepare_revision_images(pid, [
        {'data': png(color), 'extension': '.png', 'tab_ids': [tab]} for color in colors])
    data = {'actor': OWNER, 'actor_user_id': OWNER, 'generation': service.retained.get(('projects', pid)).data['generation'],
        'operation': job, 'state': 'staging', 'expires_at': time.time() + 600,
        'result': {'document_ids': [tab]}, 'asset_manifest': manifest}
    service.retained.run_transaction(lambda tx: tx.create(('projects', pid, 'manuscript_import_jobs', job), data))
    return job, manifest, payloads


def assets(make, pid):
    domain = make().domain
    return HostedCommittedAssetStore(domain.documents, domain.objects, lambda: OWNER, lambda: pid)


def test_new_images_hidden_from_every_metadata_and_committed_asset_read_until_final():
    make, raw, objects, pid, tab = setup()
    with actor():
        job, manifest, payloads = reserve(make, pid, tab, colors=('red', 'blue'))
        generation = raw.get(('projects', pid)).data['generation']
        receipts = make().stage(pid, OWNER, job, payloads)
        assert make().stage(pid, OWNER, job, payloads) == receipts
        assert len(objects.list_prefix(f'projects/{pid}/assets/')) == 2
        assert raw.get(('projects', pid)).data['generation'] == generation
        for entry in manifest:
            path = ('projects', pid, 'assets', entry['asset_id'])
            assert raw.get(path).data[ASSET_REVISIONS_FIELD] == [{'tab_id': tab, 'revision': job}]
            assert make().view.get(path) is None
            assert make().view.run_transaction(lambda tx: tx.get_many([path])) == [None]
            assert not assets(make, pid).exists(entry['api_path'])
            with pytest.raises(FileNotFoundError):
                assets(make, pid).read_blob(entry['api_path'])
        assert make().view.query_collection(('projects', pid, 'assets')) == []
        assert make().view.query_collection_group('assets', field='sha256', value=manifest[0]['sha256']) == []
        assert make().view.run_transaction(lambda tx: tx.count_collection(('projects', pid, 'assets'))) == 0
        with bind_action('synthetic', pid, OWNER):
            proof = make().verify_for_publication(pid, OWNER, job)
            changes = make().retained.run_transaction(lambda tx: make().publication_changes(tx, pid, OWNER, job, proof))
        assert len(changes) == 2
        assert all(ASSET_REVISIONS_FIELD not in value and not create for _, value, create in changes)


def test_proposed_asset_journal_hook_round_trips_and_preserves_global_shared_assets(monkeypatch):
    # The parent owns the narrow production journal hook. This isolated synthetic
    # rehearsal supplies it only in memory; no source allowlist is broadened.
    import hosted.action_journal as journal_module
    monkeypatch.setattr(journal_module, '_PRODUCT_COLLECTIONS', journal_module._PRODUCT_COLLECTIONS | {'assets'})
    make, raw, objects, pid, tab = setup()
    with actor():
        shared = assets(make, pid).put_blob(png('red'), '.png')
        existing = deepcopy(raw.query_collection(('projects', pid, 'assets'))[0])
        job, manifest, payloads = reserve(make, pid, tab, colors=('red', 'blue'))
        make().stage(pid, OWNER, job, payloads)
        assert raw.get(existing.path) == existing
        service = make(); journal = ActionJournal(service.domain.documents)
        token = journal.begin(pid, OWNER)['token']
        proof = service.verify_for_publication(pid, OWNER, job)
        with bind_action(token, pid, OWNER):
            def publish(tx):
                changes = service.publication_changes(tx, pid, OWNER, job, proof)
                assert len(changes) == 1
                root = tx.get(('projects', pid)); previous = tx.get(('projects', pid, 'tabs', tab))
                tx.set(root.path, {**root.data, 'generation': root.data['generation'] + 1})
                tx.set(previous.path, {**previous.data, HEAD_FIELD: job, 'generation': previous.data['generation'] + 1})
                for path, value, _ in changes:
                    tx.set(path, value)
            ActionDocumentStore(service.retained).run_transaction(publish)
        assert journal.finish(pid, OWNER, token)['changed']
        assert len(raw.get(('hosted_actions', OWNER, 'tokens', token)).data['records']) == 2
        for entry in manifest:
            assert assets(make, pid).read_blob(entry['api_path']) == payloads[entry['asset_id']]
        assert journal.restore(pid, OWNER, token, 'undo')['changed']
        assert assets(make, pid).read_blob(shared.api_path) == png('red')
        blue = next(entry for entry in manifest if entry['sha256'] != shared.digest)
        assert not assets(make, pid).exists(blue['api_path'])
        assert len(objects.list_prefix(f'projects/{pid}/assets/')) == 2
        assert journal.restore(pid, OWNER, token, 'redo')['changed']
        assert assets(make, pid).read_blob(blue['api_path']) == png('blue')
        assert raw.get(existing.path) == existing


def test_hidden_dedup_merges_references_and_does_not_delete_orphan_objects():
    make, raw, objects, pid, tab = setup()
    with actor():
        job, manifest, payloads = reserve(make, pid, tab, 'job_a')
        make().stage(pid, OWNER, job, payloads)
        other, _, more = reserve(make, pid, tab, 'job_b')
        make().stage(pid, OWNER, other, more)
        row = raw.get(('projects', pid, 'assets', manifest[0]['asset_id']))
        assert row.data[ASSET_REVISIONS_FIELD] == [{'tab_id': tab, 'revision': job}, {'tab_id': tab, 'revision': other}]
        assert len(objects.list_prefix(f'projects/{pid}/assets/')) == 1
        proof = make().verify_for_publication(pid, OWNER, job)
        with bind_action('synthetic', pid, OWNER):
            changes = make().retained.run_transaction(lambda tx: make().publication_changes(tx, pid, OWNER, job, proof))
        assert len(changes) == 1 and ASSET_REVISIONS_FIELD not in changes[0][1]
        path = ('projects', pid, 'manuscript_import_jobs', job)
        raw.run_transaction(lambda tx: tx.set(path, {**tx.get(path).data, 'state': 'cancelled'}))
        with pytest.raises(DocumentConflict, match='NOT_STAGING'):
            make().stage(pid, OWNER, job, payloads)
        assert objects.stat(manifest[0]['object_name']) is not None


def test_pure_validation_deduplicates_scope_and_rejects_invalid_images_before_objects():
    pid = 'a' * 32
    image = {'data': png(), 'extension': '.png', 'tab_ids': ['tab_one']}
    manifest, payloads = prepare_revision_images(pid, [image, {**image, 'tab_ids': ['tab_two']}])
    assert len(manifest) == len(payloads) == 1
    assert manifest[0]['tab_ids'] == ['tab_one', 'tab_two']
    assert manifest[0]['object_name'].startswith(f'projects/{pid}/assets/sha256/')
    assert prepare_revision_images('b' * 32, [image])[0][0]['object_name'] != manifest[0]['object_name']
    for bad in [b'', b'<svg></svg>', png()[:-1], b'x' * (12 * 1024 * 1024 + 1)]:
        with pytest.raises(HostedValidationError, match='UNSAFE'):
            prepare_revision_images(pid, [{**image, 'data': bad}])
    with pytest.raises(HostedValidationError, match='UNSAFE'):
        prepare_revision_images(pid, [{**image, 'extension': '.jpg'}])
    with pytest.raises(HostedValidationError, match='BUDGET'):
        prepare_revision_images(pid, [{**image, 'data': png((i, 0, 0))} for i in range(65)])
    with pytest.raises(HostedValidationError, match='MANIFEST'):
        prepare_revision_images(pid, [{**image, 'tab_ids': []}])


def test_aggregate_image_limit_is_32_mib(monkeypatch):
    # Validating large image data structurally is owned/tested by the inspector.
    # Keep this budget test synthetic and deterministic without a PNG compressor.
    from types import SimpleNamespace
    import hosted.revision_images as module
    monkeypatch.setattr(module, 'inspect_hosted_image_bytes', lambda *a, **k: SimpleNamespace(extension='.png', media_type='image/png'))
    with pytest.raises(HostedValidationError, match='BUDGET'):
        prepare_revision_images('a' * 32, [
            {'data': bytes([i]) * (11 * 1024 * 1024), 'extension': '.png', 'tab_ids': ['tab']} for i in range(3)])


@pytest.mark.parametrize('change', ['actor', 'revoked', 'generation', 'cancel', 'expired', 'erasure', 'receipt', 'scope'])
def test_actor_authorization_source_generation_and_job_scope_are_rechecked(change):
    make, raw, objects, pid, tab = setup()
    with actor():
        job, manifest, payloads = reserve(make, pid, tab)
        path = ('projects', pid, 'manuscript_import_jobs', job)
        def alter(tx):
            if change == 'revoked':
                tx.delete(('projects', pid, 'members', OWNER))
            elif change == 'generation':
                root = tx.get(('projects', pid)); tx.set(root.path, {**root.data, 'generation': root.data['generation'] + 1})
            elif change == 'erasure':
                user = tx.get(('users', OWNER)); tx.set(user.path, {**user.data, 'status': 'erasure_pending'})
            elif change == 'receipt':
                tx.create(('projects', pid, 'mutation_receipts', job), {})
            elif change != 'actor':
                data = deepcopy(tx.get(path).data)
                if change == 'cancel': data['state'] = 'cancelled'
                if change == 'expired': data['expires_at'] = 0
                if change == 'scope': data['asset_manifest'][0]['tab_ids'] = ['other_tab']
                tx.set(path, data)
        raw.run_transaction(alter)
        with actor(None if change == 'actor' else OWNER), pytest.raises((HostedValidationError, DocumentConflict, ProjectUnavailable, DocumentNotFound)):
            make().stage(pid, OWNER, job, payloads)
        assert objects.list_prefix(f'projects/{pid}/assets/') == []
        assert raw.query_collection(('projects', pid, 'assets')) == []


def test_interruption_after_objects_rechecks_job_and_leaves_only_unreachable_bytes(monkeypatch):
    make, raw, objects, pid, tab = setup()
    with actor():
        job, manifest, payloads = reserve(make, pid, tab)
        service = make(); original = service.publisher.publish_bytes
        def publish(*args, **kwargs):
            result = original(*args, **kwargs)
            path = ('projects', pid, 'manuscript_import_jobs', job)
            raw.run_transaction(lambda tx: tx.set(path, {**tx.get(path).data, 'state': 'cancelled'}))
            return result
        monkeypatch.setattr(service.publisher, 'publish_bytes', publish)
        with pytest.raises(DocumentConflict, match='NOT_STAGING'):
            service.stage(pid, OWNER, job, payloads)
        assert objects.stat(manifest[0]['object_name']) is not None
        assert raw.query_collection(('projects', pid, 'assets')) == []
        assert not assets(make, pid).exists(manifest[0]['api_path'])


def test_existing_object_corruption_fails_before_metadata_and_final_reverify_detects_corruption():
    make, raw, objects, pid, tab = setup()
    with actor():
        job, manifest, payloads = reserve(make, pid, tab)
        name = manifest[0]['object_name']
        objects.write_chunks(name, [b'bad'], max_bytes=100, content_type='image/png', if_generation_match=0)
        with pytest.raises(ObjectIntegrityError):
            make().stage(pid, OWNER, job, payloads)
        assert raw.query_collection(('projects', pid, 'assets')) == []
    make, raw, objects, pid, tab = setup()
    with actor():
        job, manifest, payloads = reserve(make, pid, tab)
        make().stage(pid, OWNER, job, payloads)
        name = manifest[0]['object_name']
        objects._rows[name].payload = b'bad'  # Synthetic provider corruption, no object cleanup.
        with pytest.raises(HostedValidationError, match='INTEGRITY'):
            make().verify_for_publication(pid, OWNER, job)


@pytest.mark.parametrize('collision', ['id', 'name', 'object_project', 'visible_scoped', 'reference_limit'])
def test_metadata_collision_and_visible_scoped_assets_fail_closed(collision):
    make, raw, objects, pid, tab = setup()
    with actor():
        job, manifest, payloads = reserve(make, pid, tab)
        make().stage(pid, OWNER, job, payloads)
        path = ('projects', pid, 'assets', manifest[0]['asset_id'])
        def alter(tx):
            data = dict(tx.get(path).data)
            if collision == 'id': data['asset_id'] = 'other'
            if collision == 'name': tx.create(('projects', pid, 'assets', 'other'), data); return
            if collision == 'object_project': data['object_name'] = data['object_name'].replace(pid, 'b' * 32)
            if collision == 'visible_scoped':
                data[ASSET_REVISIONS_FIELD] = [{'tab_id': tab, 'revision': 'old_revision'}]
                old = tx.get(('projects', pid, 'tabs', tab)); tx.set(old.path, {**old.data, HEAD_FIELD: 'old_revision'})
            if collision == 'reference_limit':
                data[ASSET_REVISIONS_FIELD] = [{'tab_id': tab, 'revision': f'old_{i}'} for i in range(64)]
            tx.set(path, data)
        raw.run_transaction(alter)
        before = deepcopy(raw.get(path))
        with pytest.raises((DocumentConflict, HostedValidationError)):
            make().stage(pid, OWNER, job, payloads)
        assert raw.get(path) == before


def test_receipt_tampering_publication_actor_and_bound_scope_are_rejected():
    make, raw, objects, pid, tab = setup()
    with actor():
        job, manifest, payloads = reserve(make, pid, tab)
        make().stage(pid, OWNER, job, payloads)
        proof = make().verify_for_publication(pid, OWNER, job)
        with pytest.raises(HostedValidationError, match='ACTION_REQUIRED'):
            make().retained.run_transaction(lambda tx: make().publication_changes(tx, pid, OWNER, job, proof))
        path = ('projects', pid, 'manuscript_import_jobs', job)
        def tamper(tx):
            value = deepcopy(tx.get(path).data)
            value['asset_receipts'][0]['object_generation'] += 1
            tx.set(path, value)
        raw.run_transaction(tamper)
        with bind_action('synthetic', pid, OWNER), pytest.raises(DocumentConflict, match='VERIFICATION_CHANGED'):
            make().retained.run_transaction(lambda tx: make().publication_changes(tx, pid, OWNER, job, proof))
        with pytest.raises(ObjectGenerationConflict):
            make().verify_for_publication(pid, OWNER, job)


def test_callback_cannot_substitute_non_durable_job_and_private_or_unfenced_domains_refuse():
    make, raw, objects, pid, tab = setup()
    with actor():
        job, manifest, payloads = reserve(make, pid, tab)
        service = make(); original = service.authorize
        service.authorize = lambda *args: {**original(*args), 'generation': 888}
        with pytest.raises(DocumentNotFound):
            service.stage(pid, OWNER, job, payloads)
        assert objects.list_prefix(f'projects/{pid}/assets/') == []
        with pytest.raises(HostedValidationError, match='READER_REQUIRED'):
            RevisionImageStager(HostedDomainStore(raw, objects), original)
        service.domain.encrypted_fields = object()
        with pytest.raises(HostedValidationError, match='PRIVATE'):
            RevisionImageStager(service.domain, original)


def test_real_synthetic_docx_images_bind_to_validated_content_and_destinations():
    from hosted.document_io import parse_document_bytes
    from tests.test_v10594 import illustrated_docx
    make, raw, objects, pid, tab = setup()
    payload, originals = illustrated_docx(count=2)
    collected = []
    def image_store(data, ext):
        entry = {'data': data, 'extension': ext, 'tab_ids': [tab]}
        manifest, _ = prepare_revision_images(pid, [entry])
        collected.append(entry)
        return manifest[0]['api_path']
    tabs, warnings = parse_document_bytes(payload, 'Synthetic.docx', image_store=image_store)
    manifest, bodies = prepare_revision_images(pid, collected)
    paths = {block['image_path'] for section in tabs for chapter in section['chapters']
        for block in chapter['blocks'] if block.get('kind') == 'image'}
    assert paths == {entry['api_path'] for entry in manifest}
    assert set(bodies.values()) == set(originals)
    assert not warnings


def test_final_generation_recheck_rejects_intervening_edit_without_asset_promotion():
    make, raw, objects, pid, tab = setup()
    with actor():
        job, manifest, payloads = reserve(make, pid, tab)
        make().stage(pid, OWNER, job, payloads)
        proof = make().verify_for_publication(pid, OWNER, job)
        root = raw.get(('projects', pid))
        raw.run_transaction(lambda tx: tx.set(root.path, {**root.data, 'generation': root.data['generation'] + 1}))
        with bind_action('synthetic', pid, OWNER), pytest.raises(DocumentConflict, match='GENERATION'):
            make().retained.run_transaction(lambda tx: make().publication_changes(tx, pid, OWNER, job, proof))
        assert not assets(make, pid).exists(manifest[0]['api_path'])


def test_final_pending_ref_removal_and_foreign_proof_refuse_promotion():
    from dataclasses import replace
    make, raw, objects, pid, tab = setup()
    with actor():
        job, manifest, payloads = reserve(make, pid, tab)
        make().stage(pid, OWNER, job, payloads)
        proof = make().verify_for_publication(pid, OWNER, job)
        with bind_action('synthetic', pid, OWNER):
            for changed in [replace(proof, project_id='f' * 32), replace(proof, actor='usr_' + 'f' * 32), replace(proof, job='other')]:
                with pytest.raises(DocumentConflict, match='VERIFICATION_CHANGED'):
                    make().retained.run_transaction(lambda tx: make().publication_changes(tx, pid, OWNER, job, changed))
            path = ('projects', pid, 'assets', manifest[0]['asset_id'])
            raw.run_transaction(lambda tx: tx.set(path, {**tx.get(path).data,
                ASSET_REVISIONS_FIELD: [{'tab_id': tab, 'revision': 'different_job'}]}))
            with pytest.raises(DocumentConflict, match='REFERENCE_CHANGED'):
                make().retained.run_transaction(lambda tx: make().publication_changes(tx, pid, OWNER, job, proof))


def test_late_metadata_collision_rolls_back_all_new_assets_and_preserves_orphans():
    make, raw, objects, pid, tab = setup()
    with actor():
        job, manifest, payloads = reserve(make, pid, tab, colors=('red', 'blue'))
        target = ('projects', pid, 'assets', manifest[-1]['asset_id'])
        raw.run_transaction(lambda tx: tx.create(target, {'name': 'wrong'}))
        with pytest.raises(DocumentConflict, match='COLLISION'):
            make().stage(pid, OWNER, job, payloads)
        assert len(raw.query_collection(('projects', pid, 'assets'))) == 1
        assert len(objects.list_prefix(f'projects/{pid}/assets/')) == 2
        assert 'asset_receipts' not in raw.get(('projects', pid, 'manuscript_import_jobs', job)).data


def test_sixty_four_distinct_assets_produce_at_most_sixty_four_final_rows():
    make, raw, objects, pid, tab = setup()
    with actor():
        job, manifest, payloads = reserve(make, pid, tab, colors=tuple((i, 0, 0) for i in range(64)))
        make().stage(pid, OWNER, job, payloads)
        proof = make().verify_for_publication(pid, OWNER, job)
        with bind_action('synthetic', pid, OWNER):
            changes = make().retained.run_transaction(lambda tx: make().publication_changes(tx, pid, OWNER, job, proof))
        assert len(changes) == 64
        assert all(len(path) == 4 and path[:3] == ('projects', pid, 'assets') for path, _, _ in changes)



def test_private_root_rejected_before_plaintext_object_publication():
    make, raw, objects, pid, tab = setup()
    with actor():
        job, manifest, payloads = reserve(make, pid, tab)
        root = raw.get(('projects', pid))
        raw.run_transaction(lambda tx: tx.set(root.path, {**root.data, '_bm_private_title': {'format': 'synthetic'}}))
        with pytest.raises(HostedValidationError, match='PRIVATE_TITLE_CODEC_REQUIRED'):
            make().stage(pid, OWNER, job, payloads)
        assert objects.list_prefix(f'projects/{pid}/assets/') == []
        assert raw.query_collection(('projects', pid, 'assets')) == []
