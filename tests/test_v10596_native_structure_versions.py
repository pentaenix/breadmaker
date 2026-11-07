"""Imported Structure Versions activate atomically, with ordinary hosted Undo."""
from copy import deepcopy
import json

import pytest

from hosted.action_journal import ActionDocumentStore, ActionJournal, bind_action
from hosted.canvas_versions import activate
from hosted.firestore.contracts import DocumentConflict, HostedAuthorizationError, HostedValidationError, ProjectUnavailable
from tests.test_v10353 import _services, OWNER, OUTSIDER


def setup_canvas(count=4, scope='chapters'):
    docs, objects, authority, users, projects = _services()
    pid = projects.create('Synthetic Structure Versions')['id']
    domain = projects.domain
    domain.documents = ActionDocumentStore(docs)
    prefix = ('projects', pid, 'canvases', 'canvas_native')
    kind = 'scene' if scope == 'scenes' else 'chapter'
    items = []
    rows = {prefix: {'id': 'canvas_native', 'canvas_type': 'developmental', 'developmental_scope': scope}}
    for number in range(count):
        source = f'{kind}_legacy_{number}'
        node = f'node_legacy_{number}'
        rows[('projects', pid, 'scenes' if kind == 'scene' else 'chapters', source)] = {'id': source, 'title': source, 'position': number}
        rows[prefix + ('nodes', node)] = {'id': node, 'node_id': node, 'node_type': kind, kind + '_id': source,
                'x': 900, 'y': 900, 'w': 143, 'h': 52, 'lane_position': number, 'color': '', 'label_id': None}
        items.append({'id': f'item_{number}', 'entity_type': kind, 'entity_id': source,
                      'placed': 1, 'lane_position': count-number-1, 'x': 120, 'y': 390, 'title': source})
    rows[prefix + ('nodes', 'free_saved')] = {'id': 'free_saved', 'node_id': 'free_saved', 'node_type': 'free',
            'title': 'Unsaved title', 'body': 'Unsaved body', 'x': 900, 'y': 900, 'lane_position': None}
    rows[prefix + ('nodes', 'free_new')] = {'id': 'free_new', 'node_id': 'free_new', 'node_type': 'free',
            'title': 'Not in version', 'x': 900, 'y': 900, 'lane_position': None}
    rows[prefix + ('edges', 'edge_saved')] = {'id': 'edge_saved', 'edge_id': 'edge_saved',
            'from_node_id': 'free_saved', 'to_node_id': 'free_new', 'label': 'Unsaved edge'}
    rows[prefix + ('edges', 'edge_new')] = {'id': 'edge_new', 'edge_id': 'edge_new',
            'from_node_id': 'free_saved', 'to_node_id': 'free_new'}
    rows[('projects', pid, 'markup_labels', 'label_saved')] = {'id': 'label_saved', 'color': '#123456'}
    saved = {'schema': 3, 'structural_styles': {f'{kind}:{kind}_legacy_0': {'color': '#aabbcc', 'label_id': 'label_saved'}},
             'nodes': [{'id': 'free_saved', 'node_type': 'free', 'title': 'Saved title', 'body': 'Saved body',
                        'x': 45, 'y': 500, 'w': 230, 'h': 130, 'lane_position': None, 'label_id': 'label_saved'}],
             'edges': [{'id': 'edge_saved', 'from_node_id': 'free_saved', 'to_node_id': 'free_new',
                        'label': 'Saved edge', 'marker_id': 'label_saved'}],
             'viewport': {'zoom': 1.2, 'left': 45, 'top': 70, 'laneLeft': 90}}
    rows[prefix + ('versions', 'version_native')] = {'id': 'version_native', 'name': 'Native saved version', 'workspace_json': json.dumps(saved)}
    for item in items:
        rows[prefix + ('versions', 'version_native', 'items', item['id'])] = item
    def seed(tx):
        for path, data in rows.items():
            tx.set(path, {**data, 'generation': 1, 'created_at': 'synthetic', 'updated_at': 'synthetic'})
    docs.run_transaction(seed)
    return domain, docs, pid, prefix


def snapshot(docs, prefix):
    return {row.path: dict(row.data) for collection in ['nodes', 'edges', 'versions']
            for row in docs.query_collection(prefix + (collection,))}


def use(domain, pid, *, actor=OWNER, operation='synthetic-use'):
    return activate(domain, pid, actor, 'canvas_native', 'version_native', operation=operation)


def test_native_structure_68_placements_workspace_and_undo_redo_are_atomic():
    domain, docs, pid, prefix = setup_canvas(68)
    before = snapshot(docs, prefix)
    manuscript = deepcopy(docs.query_collection(('projects', pid, 'chapters')))
    journal = ActionJournal(domain.documents)
    token = journal.begin(pid, OWNER, 'Use Canvas version')['token']
    generation = domain.project_generation(pid, OWNER)
    with bind_action(token, pid, OWNER):
        result = use(domain, pid)
        assert use(domain, pid) == result  # Lost-response replay does not apply twice.
    assert result['changed'] == 72
    assert result['workspace']['viewport'] == {'zoom': 1.2, 'left': 45, 'top': 70, 'laneLeft': 90}
    assert domain.project_generation(pid, OWNER) == generation + 1
    assert docs.get(prefix + ('nodes', 'node_legacy_0')).data['lane_position'] == 67
    assert docs.get(prefix + ('nodes', 'node_legacy_0')).data['color'] == '#aabbcc'
    assert docs.get(prefix + ('nodes', 'free_saved')).data['body'] == 'Saved body'
    assert docs.get(prefix + ('nodes', 'free_new')).data['developmental_hidden'] == 1
    assert docs.get(prefix + ('edges', 'edge_saved')).data['color'] == '#123456'
    assert docs.get(prefix + ('edges', 'edge_new')).data['developmental_hidden'] == 1
    assert docs.query_collection(('projects', pid, 'chapters')) == manuscript
    assert journal.finish(pid, OWNER, token)['changed']
    journal.restore(pid, OWNER, token, 'undo')
    clean = lambda value: {key: item for key, item in value.items() if key not in {'generation', 'updated_at'}}
    assert {path: clean(data) for path, data in snapshot(docs, prefix).items()} == {path: clean(data) for path, data in before.items()}
    journal.restore(pid, OWNER, token, 'redo')
    assert docs.get(prefix + ('nodes', 'node_legacy_0')).data['lane_position'] == 67
    assert docs.get(prefix + ('nodes', 'free_saved')).data['body'] == 'Saved body'


def test_native_scene_version_retains_missing_source_ghost_and_lane_reservation():
    domain, docs, pid, prefix = setup_canvas(4, 'scenes')
    docs.run_transaction(lambda tx: tx.delete(('projects', pid, 'scenes', 'scene_legacy_2')))
    use(domain, pid)
    assert docs.get(prefix + ('nodes', 'node_legacy_2')) is None
    assert docs.get(prefix + ('versions', 'version_native', 'items', 'item_2')).data['entity_id'] == 'scene_legacy_2'
    assert docs.get(('projects', pid, 'scenes', 'scene_legacy_2')) is None
    assert sorted(row.data['lane_position'] for row in docs.query_collection(prefix + ('nodes',)) if row.data.get('node_type') == 'scene') == [0, 2, 3]


def test_native_structure_over_record_limit_does_not_partially_apply():
    domain, docs, pid, prefix = setup_canvas(101)
    before, generation = snapshot(docs, prefix), domain.project_generation(pid, OWNER)
    with pytest.raises(HostedValidationError, match='Nothing was changed'):
        use(domain, pid)
    assert snapshot(docs, prefix) == before
    assert domain.project_generation(pid, OWNER) == generation
    assert docs.query_collection(('projects', pid, 'mutation_receipts')) == []


def test_native_structure_invalid_last_geometry_never_applies_earlier_rows():
    domain, docs, pid, prefix = setup_canvas(4)
    path = prefix + ('versions', 'version_native', 'items', 'item_3')
    docs.run_transaction(lambda tx: tx.set(path, {**tx.get(path).data, 'x': float('inf')}))
    before = snapshot(docs, prefix)
    with pytest.raises(HostedValidationError, match='GEOMETRY_INVALID'):
        use(domain, pid)
    assert snapshot(docs, prefix) == before


def test_native_structure_missing_member_and_reader_cannot_activate():
    domain, docs, pid, prefix = setup_canvas()
    before = snapshot(docs, prefix)
    with pytest.raises(ProjectUnavailable):
        use(domain, pid, actor=OUTSIDER)
    docs.run_transaction(lambda tx: tx.set(('projects', pid, 'members', OUTSIDER), {'role': 'reader'}))
    with pytest.raises(HostedAuthorizationError):
        use(domain, pid, actor=OUTSIDER)
    assert snapshot(docs, prefix) == before


def test_native_structure_editor_membership_can_activate():
    domain, docs, pid, prefix = setup_canvas()
    docs.run_transaction(lambda tx: tx.set(('projects', pid, 'members', OUTSIDER), {'role': 'editor'}))
    use(domain, pid, actor=OUTSIDER)
    assert docs.get(prefix + ('nodes', 'free_saved')).data['body'] == 'Saved body'


def test_native_structure_stale_project_generation_is_rejected(monkeypatch):
    domain, docs, pid, prefix = setup_canvas()
    before = snapshot(docs, prefix)
    real = domain.project_generation
    def race(project, actor):
        generation = real(project, actor)
        docs.run_transaction(lambda tx: tx.set(('projects', pid), {**tx.get(('projects', pid)).data, 'generation': generation+1}))
        return generation
    monkeypatch.setattr(domain, 'project_generation', race)
    with pytest.raises(DocumentConflict, match='GENERATION_CONFLICT'):
        use(domain, pid)
    assert snapshot(docs, prefix) == before


def test_native_structure_provider_failure_rolls_back_all_rows(monkeypatch):
    from hosted.firestore.memory import _MemoryTransaction
    domain, docs, pid, prefix = setup_canvas()
    before, generation = snapshot(docs, prefix), domain.project_generation(pid, OWNER)
    original = _MemoryTransaction.set
    def fail(tx, path, data):
        if path == prefix + ('nodes', 'node_legacy_3'):
            raise RuntimeError('Synthetic provider failure')
        return original(tx, path, data)
    monkeypatch.setattr(_MemoryTransaction, 'set', fail)
    with pytest.raises(RuntimeError, match='Synthetic provider'):
        use(domain, pid)
    assert snapshot(docs, prefix) == before and domain.project_generation(pid, OWNER) == generation


def test_hosted_snapshot_versions_keep_supported_node_edge_restore():
    domain, docs, pid, prefix = setup_canvas(2)
    def hosted(tx):
        version = prefix + ('versions', 'version_native')
        tx.set(version, {'id': 'version_native', 'generation': 1})
        for row in tx.query_collection(version + ('items',)):
            tx.delete(row.path)
        tx.set(version + ('items', 'saved_node'), {'id': 'saved_node', 'kind': 'node', 'source_id': 'free_saved',
               'payload': {'id': 'free_saved', 'node_type': 'free', 'title': 'Hosted snapshot title'}})
        tx.set(version + ('items', 'restored_node'), {'id': 'restored_node', 'kind': 'node', 'source_id': 'free_restored',
               'payload': {'id': 'free_restored', 'node_type': 'free', 'title': 'Restored hosted object'}})
    docs.run_transaction(hosted)
    use(domain, pid)
    assert docs.get(prefix + ('nodes', 'free_saved')).data['title'] == 'Hosted snapshot title'
    assert docs.get(prefix + ('nodes', 'free_restored')).data['title'] == 'Restored hosted object'


def test_native_structure_metadata_limit_rejects_without_partial_write():
    domain, docs, pid, prefix = setup_canvas()
    path = prefix + ('versions', 'version_native')
    data = dict(docs.get(path).data)
    saved = json.loads(data['workspace_json'])
    saved['nodes'][0]['body'] = 'Synthetic long saved body ' * 16000
    data['workspace_json'] = json.dumps(saved)
    docs.run_transaction(lambda tx: tx.set(path, data))
    before = snapshot(docs, prefix)
    with pytest.raises(HostedValidationError, match='Nothing was changed'):
        use(domain, pid)
    assert snapshot(docs, prefix) == before


def test_native_structure_http_list_and_detail_match_existing_ui_contract(monkeypatch):
    from hosted import http_compat as api
    from types import SimpleNamespace
    version = {'id': 'native', 'workspace_json': json.dumps({'schema': 3, 'nodes': [], 'edges': []})}
    monkeypatch.setattr(api, '_pid', lambda request: 'a' * 32)
    monkeypatch.setattr(api, '_get', lambda kind, ids: version)
    monkeypatch.setattr(api, '_list', lambda kind, ids: [version, {'id': 'deleted', 'status': 'deleted'}] if kind == 'canvas_version' else [{'entity_type': 'chapter', 'entity_id': 'chapter_legacy_1'}])
    assert api.hosted_canvas_versions('canvas', SimpleNamespace()) == {'versions': [version]}
    detail = api.hosted_canvas_version_detail('canvas', 'native', SimpleNamespace())
    assert detail['workspace'] == {'schema': 3, 'nodes': [], 'edges': []}
    assert detail['items'][0]['entity_id'] == 'chapter_legacy_1'


def test_native_structure_zero_coordinates_remain_exact():
    domain, docs, pid, prefix = setup_canvas()
    path = prefix + ('versions', 'version_native', 'items', 'item_0')
    docs.run_transaction(lambda tx: tx.set(path, {**tx.get(path).data, 'x': 0, 'y': 0, 'placed': 0}))
    use(domain, pid)
    node = docs.get(prefix + ('nodes', 'node_legacy_0')).data
    assert (node['x'], node['y'], node['lane_position']) == (0, 0, None)


def test_hosted_snapshot_does_not_resurrect_deleted_manuscript_source():
    domain, docs, pid, prefix = setup_canvas(2)
    def hosted(tx):
        version = prefix + ('versions', 'version_native')
        tx.set(version, {'id': 'version_native', 'generation': 1})
        for row in tx.query_collection(version + ('items',)):
            tx.delete(row.path)
        tx.set(version + ('items', 'missing_source'), {'id': 'missing_source', 'kind': 'node', 'source_id': 'node_legacy_0',
               'payload': {'id': 'node_legacy_0', 'node_type': 'chapter', 'chapter_id': 'chapter_legacy_0'}})
        tx.delete(('projects', pid, 'chapters', 'chapter_legacy_0'))
    docs.run_transaction(hosted)
    use(domain, pid)
    assert docs.get(prefix + ('nodes', 'node_legacy_0')) is None
    assert docs.get(('projects', pid, 'chapters', 'chapter_legacy_0')) is None
    assert docs.get(prefix + ('versions', 'version_native', 'items', 'missing_source')) is not None
