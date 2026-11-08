"""Atomic activation of native placements and hosted Canvas snapshots.

Versions remain historical records. Activation changes only the current Canvas,
never recreates a deleted manuscript source or applies its order to Manuscript.
"""
from copy import deepcopy
import hashlib
import json
import math

from backend.content import CONTENT_CARD_SHAPES, FIGURE_NODE_SHAPES, normalize_color
from hosted.firestore.contracts import DocumentConflict, DocumentNotFound, HostedAuthorizationError, HostedValidationError
from hosted.firestore.ids import require_opaque_id, require_project_id, require_user_id
from hosted.firestore.repositories import _canonical_payload, _fingerprint
from security.content_security import is_safe_project_asset_path

MAX_CHANGED_RECORDS = 100
MAX_METADATA_BYTES = 300_000
MAX_SOURCE_RECORDS = 2048
_LIMIT = 'This Structure Version exceeds the safe activation limit. Nothing was changed.'
_IDENTITY = {'id', 'node_id', 'edge_id', 'canvas_id', 'project_id', 'generation', 'created_at', 'updated_at'}


def workspace(version):
    value = version.get('workspace_json', {})
    try:
        value = json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError) as exc:
        raise HostedValidationError('HOSTED_CANVAS_VERSION_WORKSPACE_INVALID') from exc
    if not isinstance(value, dict):
        raise HostedValidationError('HOSTED_CANVAS_VERSION_WORKSPACE_INVALID')
    return value


def _number(value, default=0):
    try:
        number = float(default if value is None else value)
    except (TypeError, ValueError) as exc:
        raise HostedValidationError('HOSTED_CANVAS_VERSION_GEOMETRY_INVALID') from exc
    if not math.isfinite(number):
        raise HostedValidationError('HOSTED_CANVAS_VERSION_GEOMETRY_INVALID')
    return number


def _lane(value):
    if value is None:
        return None
    number = _number(value)
    if number < 0 or int(number) != number:
        raise HostedValidationError('HOSTED_CANVAS_VERSION_LANE_INVALID')
    return int(number)


def _reference(value, valid):
    return str(value) if value not in (None, '') and str(value) in valid else None


def _entries(value):
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise HostedValidationError('HOSTED_CANVAS_VERSION_WORKSPACE_INVALID')
    ids = [str(item.get('id') or '') for item in value]
    if any(not item for item in ids) or len(set(ids)) != len(ids):
        raise HostedValidationError('HOSTED_CANVAS_VERSION_WORKSPACE_INVALID')
    return value


def _native_plan(canvas, version, items, nodes, edges, refs):
    if canvas.get('canvas_type') != 'developmental':
        raise HostedValidationError('Versions belong to a Developmental Canvas')
    saved = workspace(version)
    scope = 'scene' if canvas.get('developmental_scope') == 'scenes' else 'chapter'
    key = scope + '_id'
    placements = {}
    for item in items:
        if item.get('entity_type') not in {'chapter', 'scene'} or not item.get('entity_id'):
            raise HostedValidationError('HOSTED_CANVAS_VERSION_ITEM_INVALID')
        if item['entity_type'] != scope:
            continue
        eid = str(item['entity_id'])
        if eid in placements:
            raise HostedValidationError('HOSTED_CANVAS_VERSION_ITEM_DUPLICATE')
        placements[eid] = item
    styles = saved.get('structural_styles', {})
    if not isinstance(styles, dict):
        raise HostedValidationError('HOSTED_CANVAS_VERSION_WORKSPACE_INVALID')
    live = sorted((node for node in nodes.values() if node.get('node_type') == scope),
                  key=lambda n: (n.get('lane_position') is None, _number(n.get('lane_position')),
                                 _number(n.get('x')), _number(n.get('y')), str(n['id'])))
    by_entity, free_slot = {}, 0
    for node in live:
        eid = str(node.get(key) or '')
        if eid not in refs[scope]:
            # Local reconciliation also removes live tiles after source deletion.
            # Their saved placement remains a missing historical item.
            del nodes[node['id']]
            continue
        if eid in by_entity:
            raise HostedValidationError('HOSTED_CANVAS_VERSION_SOURCE_DUPLICATE')
        by_entity[eid] = node
        item = placements.get(eid)
        if item:
            placed = bool(int(item.get('placed') or 0))
            node.update(lane_position=_lane(item.get('lane_position', 0)) if placed else None,
                        x=_number(item.get('x'), 120), y=_number(item.get('y'), 390), w=143, h=52)
            if placed and node['lane_position'] is None:
                node['lane_position'] = 0
        else:
            node.update(lane_position=None, x=120+(free_slot % 5)*250,
                        y=390+(free_slot // 5)*92, w=143, h=52)
            free_slot += 1
        style = styles.get(f'{scope}:{eid}')
        if style is not None:
            if not isinstance(style, dict):
                raise HostedValidationError('HOSTED_CANVAS_VERSION_WORKSPACE_INVALID')
            node.update(color=style.get('color') or '', text_color='',
                        label_id=_reference(style.get('label_id'), refs['label']))
    for node in nodes.values():
        if node.get('node_type') not in {'chapter', 'scene'}:
            node.update(developmental_hidden=1, lane_position=None)
    full = _number(saved.get('schema')) >= 2
    for snap in _entries(saved.get('nodes', [])):
        node = nodes.get(str(snap['id']))
        if node is None or node.get('node_type') in {'chapter', 'scene'}:
            continue  # History does not recreate physically removed objects.
        kind = node.get('node_type')
        shape = str(snap.get('shape') or 'rect')[:24]
        if ((kind == 'free' and shape not in CONTENT_CARD_SHAPES)
                or (kind == 'figure' and shape not in FIGURE_NODE_SHAPES)):
            shape = 'rect'
        node.update(developmental_hidden=0, x=_number(snap.get('x')), y=_number(snap.get('y')),
                    lane_position=_lane(snap.get('lane_position')), w=max(18, _number(snap.get('w') or 220)),
                    h=max(18, _number(snap.get('h') or 125)), shape=shape, icon=str(snap.get('icon') or '')[:16],
                    color=str(snap.get('color') or '')[:16], text_color=str(snap.get('text_color') or '')[:16],
                    label_id=_reference(snap.get('label_id'), refs['label']),
                    icon_marker_id=_reference(snap.get('icon_marker_id'), refs['icon']))
        if full:
            image = str(snap.get('image_path') or '')
            node.update(title=str(snap.get('title') or ''), body=str(snap.get('body') or ''),
                        display_mode=snap.get('display_mode') if snap.get('display_mode') in {'full', 'header', 'body'} else 'full',
                        text_align=snap.get('text_align') if snap.get('text_align') in {'left', 'center', 'right'} else 'left',
                        figure_layer=snap.get('figure_layer') if snap.get('figure_layer') in {'back', 'front'} else 'back',
                        figure_border_width=max(.5, min(12, _number(snap.get('figure_border_width') or 1))),
                        figure_fill_color=normalize_color(str(snap.get('figure_fill_color') or ''), True),
                        figure_stroke_color=normalize_color(str(snap.get('figure_stroke_color') or ''), True),
                        chapter_id=_reference(snap.get('chapter_id'), refs['chapter']),
                        scene_id=_reference(snap.get('scene_id'), refs['scene']),
                        image_path=image if kind == 'image' and is_safe_project_asset_path(image) else '')
            for field, default in [('figure_line_x1', .02), ('figure_line_y1', .5),
                                   ('figure_line_x2', .98), ('figure_line_y2', .5)]:
                node[field] = _number(snap.get(field), default)
    for eid, edge in list(edges.items()):
        if str(edge.get('from_node_id')) not in nodes or str(edge.get('to_node_id')) not in nodes:
            del edges[eid]
            continue
        edge['developmental_hidden'] = 1
    if full:
        for snap in _entries(saved.get('edges', [])):
            edge = edges.get(str(snap['id']))
            source, target = str(snap.get('from_node_id') or ''), str(snap.get('to_node_id') or '')
            if edge is None or source == target or source not in nodes or target not in nodes:
                continue
            marker = _reference(snap.get('marker_id'), refs['label'])
            edge.update(developmental_hidden=0, from_node_id=source, to_node_id=target,
                        from_port=str(snap.get('from_port') or 'right'), to_port=str(snap.get('to_port') or 'left'),
                        label=str(snap.get('label') or '')[:180], marker_id=marker,
                        color=refs['label'][marker].get('color', '') if marker else str(snap.get('color') or '')[:16],
                        line_style=str(snap.get('line_style') or 'solid'), end_style=str(snap.get('end_style') or 'arrow'),
                        line_width=_number(snap.get('line_width') or 1.5), route_style=str(snap.get('route_style') or 'curved'))
    else:
        values = saved.get('edges', [])
        if not isinstance(values, list):
            raise HostedValidationError('HOSTED_CANVAS_VERSION_WORKSPACE_INVALID')
        for eid in values:
            edge = edges.get(str(eid))
            if edge and str(edge.get('from_node_id')) in nodes and str(edge.get('to_node_id')) in nodes:
                edge['developmental_hidden'] = 0
    reserved = {_lane(item['lane_position']) for eid, item in placements.items()
                if int(item.get('placed') or 0) and item.get('lane_position') is not None and eid not in by_entity}
    lane = sorted((node for node in nodes.values() if not node.get('developmental_hidden')
                   and node.get('lane_position') is not None and node.get('node_type') in {scope, 'free'}),
                  key=lambda n: (_lane(n['lane_position']), n.get('node_type') != scope, str(n['id'])))
    position = 0
    for node in lane:
        while position in reserved:
            position += 1
        node['lane_position'] = position
        position += 1
    viewport = saved.get('viewport', {})
    if not isinstance(viewport, dict):
        raise HostedValidationError('HOSTED_CANVAS_VERSION_WORKSPACE_INVALID')
    return {key: _number(value) for key, value in viewport.items() if key in {'zoom', 'left', 'top', 'laneLeft'}}


def activate(domain, project_id, user_id, canvas_id, version_id, *, operation):
    pid, uid = require_project_id(project_id), require_user_id(user_id)
    cid, vid = require_opaque_id(canvas_id), require_opaque_id(version_id)
    if domain.encrypted_fields is not None or domain.encrypted_objects is not None:
        raise HostedValidationError('HOSTED_PRIVATE_BATCH_NOT_READY')
    generation, stamp = domain.project_generation(pid, uid), domain.clock.now_iso()
    parents = {'project_id': pid, 'canvas_id': cid}
    model = domain._model('canvas')
    prefix = ('projects', pid, 'canvases', cid)
    receipt_path = ('projects', pid, 'mutation_receipts', hashlib.sha256(operation.encode()).hexdigest()[:40])
    digest = _fingerprint({'canvas': cid, 'version': vid, 'kind': 'canvas_version_activate'})

    def commit(tx):
        root_path, root = domain._authorize_and_project(tx, model, parents, uid, mutation=True)
        member = tx.get(('projects', pid, 'members', uid))
        if member is None or member.data.get('role') not in {'owner', 'editor'}:
            raise HostedAuthorizationError('PROJECT_PERMISSION_DENIED')
        receipt = tx.get(receipt_path)
        if receipt:
            if receipt.data.get('actor') != uid or receipt.data.get('digest') != digest:
                raise DocumentConflict('HOSTED_CANVAS_VERSION_REQUEST_CHANGED')
            if root['generation'] != receipt.data['generation']:
                raise DocumentConflict('HOSTED_GENERATION_CONFLICT')
            return dict(receipt.data['result'])
        if root['generation'] != generation:
            raise DocumentConflict('HOSTED_GENERATION_CONFLICT')
        canvas, version = tx.get(prefix), tx.get(prefix + ('versions', vid))
        if canvas is None or version is None or version.data.get('status') == 'deleted':
            raise DocumentNotFound('HOSTED_CANVAS_VERSION_NOT_FOUND')
        rows = tx.query_collection(prefix + ('nodes',)) + tx.query_collection(prefix + ('edges',))
        items = [dict(row.data) for row in tx.query_collection(prefix + ('versions', vid, 'items'))]
        if len(rows) + len(items) > MAX_SOURCE_RECORDS:
            raise HostedValidationError(_LIMIT)
        before = {row.path: dict(row.data) for row in rows}
        nodes = {row.path[-1]: deepcopy(dict(row.data)) for row in rows if row.path[-2] == 'nodes'}
        edges = {row.path[-1]: deepcopy(dict(row.data)) for row in rows if row.path[-2] == 'edges'}
        native = 'workspace_json' in version.data or any('entity_type' in item for item in items)
        viewport = {}
        if native:
            if any('kind' in item or 'payload' in item for item in items):
                raise HostedValidationError('HOSTED_CANVAS_VERSION_SHAPE_MIXED')
            refs = {kind: {row.path[-1]: dict(row.data) for row in tx.query_collection(('projects', pid, collection))}
                    for kind, collection in [('chapter', 'chapters'), ('scene', 'scenes'), ('label', 'markup_labels'), ('icon', 'icon_markers')]}
            viewport = _native_plan(dict(canvas.data), dict(version.data), items, nodes, edges, refs)
        else:
            seen, missing_structure = set(), set()
            sources = {kind: {row.path[-1] for row in tx.query_collection(('projects', pid, collection))}
                       for kind, collection in [('chapter', 'chapters'), ('scene', 'scenes')]}
            for item in items:
                kind, payload = item.get('kind'), item.get('payload')
                if kind not in {'node', 'edge'} or not isinstance(payload, dict):
                    raise HostedValidationError('HOSTED_CANVAS_VERSION_ITEM_INVALID')
                eid = require_opaque_id(str(item.get('source_id') or payload.get('id') or ''))
                if (kind, eid) in seen:
                    raise HostedValidationError('HOSTED_CANVAS_VERSION_ITEM_DUPLICATE')
                seen.add((kind, eid))
                target = nodes if kind == 'node' else edges
                data = {**target.get(eid, {}), **{key: value for key, value in payload.items()
                        if key not in _IDENTITY and not key.startswith('_bm_')}}
                data.update(id=eid, **{kind + '_id': eid})
                source_kind = data.get('node_type') if kind == 'node' else None
                if source_kind in {'chapter', 'scene'}:
                    source_id = require_opaque_id(str(data.get(source_kind + '_id') or ''))
                    if source_id not in sources[source_kind]:
                        missing_structure.add(eid)
                        target.pop(eid, None)
                        continue
                target[eid] = data
            for eid, edge in list(edges.items()):
                if str(edge.get('from_node_id')) in missing_structure or str(edge.get('to_node_id')) in missing_structure:
                    del edges[eid]
                    continue
                if str(edge.get('from_node_id')) not in nodes or str(edge.get('to_node_id')) not in nodes:
                    raise HostedValidationError('HOSTED_CANVAS_VERSION_ENDPOINT_MISSING')
        after = {prefix + (collection, eid): {
                    **_canonical_payload({key: value for key, value in data.items() if not key.startswith('_bm_')}),
                    **{key: value for key, value in data.items() if key.startswith('_bm_')}}
                 for collection, records in [('nodes', nodes), ('edges', edges)] for eid, data in records.items()}
        changed = sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
        if len(changed) > MAX_CHANGED_RECORDS or len(json.dumps(
                [[before.get(path), after.get(path)] for path in changed], ensure_ascii=False).encode()) > MAX_METADATA_BYTES:
            raise HostedValidationError(_LIMIT)
        result = {'ok': True, 'version_id': vid, 'changed': len(changed), 'workspace': {'viewport': viewport}}
        # Every authority, source and geometry check precedes every write. The
        # existing ActionDocumentStore journals this transaction as a whole.
        domain._advance_project(tx, root_path, root, expected_generation=generation, stamp=stamp)
        for path in changed:
            data = after.get(path)
            if data is None:
                tx.delete(path)
                continue
            data.update(generation=int((before.get(path) or {}).get('generation') or 0)+1,
                        created_at=(before.get(path) or {}).get('created_at', stamp), updated_at=stamp,
                        _bm_last_operation=operation, _bm_last_fingerprint=_fingerprint(data))
            if path in before:
                tx.set(path, data)
            else:
                tx.create(path, data)
        tx.create(receipt_path, {'actor': uid, 'digest': digest, 'generation': generation+1, 'result': result})
        return result
    return domain.documents.run_transaction(commit)
