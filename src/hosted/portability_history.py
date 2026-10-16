from __future__ import annotations

"""Explicit historical data translation for local Schema 42 downloads.

Hosted history and local history are NOT wire-compatible. Translate only
recognized, complete shapes; reject ambiguous or insufficient frozen data.
This is an export-only codec, never the hosted canonical storage authority.
"""

import hashlib
import json
import re
import zlib
from collections import Counter
from typing import Any, Mapping


class HistoryShapeError(ValueError):
    pass


def _reject(reason: str) -> None:
    raise HistoryShapeError(reason)


def _list(snapshot: Mapping[str, Any], key: str) -> list:
    value = snapshot.get(key)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        _reject('HOSTED_PORTABLE_REVIEW_SNAPSHOT_PROJECTION_REQUIRED')
    return value


def _record(table: str, snapshot_id: str, source: Mapping[str, Any], values: dict[str, Any]) -> dict:
    raw = f'{snapshot_id}/{table}/{source.get("id")}'
    ident = hashlib.sha256(raw.encode()).hexdigest()[:24]
    old = f'frozen_{ident}'
    return {'ids': {'snapshot_id': snapshot_id, 'frozen_id': old},
            'data': {'id': old, 'snapshot_id': snapshot_id, **values}}


def prepare_review_rounds(records: Mapping[str, Any]) -> tuple[dict[str, list], set[str]]:
    """Project only the known read-only manuscript Review shape.

    A hosted snapshot includes product UI state but does *not* freeze selected
    marker assignments or questionnaire questions. Consequently Reviews using
    those capabilities cannot be reconstructed as local rounds from it.
    """
    result = {key: [dict(item, ids=dict(item['ids']), data=dict(item['data']))
                    for item in items] if isinstance(items, list) else items
              for key, items in records.items()}
    snapshots = result.get('review_snapshot', [])
    reviews = {str(item['data'].get('id')): item['data'] for item in result.get('review', [])}
    counts = Counter(str(s['data'].get('review_id') or s['ids'].get('review_id') or '') for s in snapshots)
    normalized_ids: set[str] = set()
    for item in snapshots:
        row = item['data']
        sid = str(row.get('id') or '')
        rid = str(row.get('review_id') or item['ids'].get('review_id') or '')
        review = reviews.get(rid)
        body = row.get('snapshot')
        if not isinstance(body, dict) or not isinstance(review, dict):
            _reject('HOSTED_PORTABLE_REVIEW_SNAPSHOT_PROJECTION_REQUIRED')
        if any(key not in body for key in ('project','tabs','chapters','scenes','blocks','reviews')):
            _reject('HOSTED_PORTABLE_REVIEW_SNAPSHOT_PROJECTION_REQUIRED')
        if counts[rid] != 1:
            # Without a durable ordinal we cannot assign distinct rounds safely.
            _reject('HOSTED_PORTABLE_REVIEW_ROUND_ORDER_UNKNOWN')
        frozen_reviews = _list(body, 'reviews')
        frozen = next((r for r in frozen_reviews if str(r.get('id')) == rid), None)
        if frozen is None or len(frozen_reviews) != 1:
            _reject('HOSTED_PORTABLE_REVIEW_FROZEN_AUTHORITY_MISSING')
        if str(frozen.get('scope_type') or review.get('scope_type') or 'manuscript') != 'manuscript' or str(review.get('scope_type') or 'manuscript') != 'manuscript':
            _reject('HOSTED_PORTABLE_REVIEW_SCOPE_UNSUPPORTED')
        if not bool(frozen.get('permission_read', 1)) or not bool(review.get('permission_read', 1)):
            _reject('HOSTED_PORTABLE_REVIEW_PERMISSION_UNSUPPORTED')
        if any(bool(frozen.get(k) or review.get(k)) for k in ('permission_comment', 'permission_feedback')):
            _reject('HOSTED_PORTABLE_REVIEW_FROZEN_MARKERS_QUESTIONS_UNAVAILABLE')
        for key in ('canvases', 'maps', 'labels', 'icon_markers', 'scene_gaps'):
            if body.get(key):
                _reject('HOSTED_PORTABLE_REVIEW_NONMANUSCRIPT_SNAPSHOT_UNSUPPORTED')
        tabs = _list(body, 'tabs')
        chapters = _list(body, 'chapters')
        scenes = _list(body, 'scenes')
        blocks = _list(body, 'blocks')
        if body.get('documents') != tabs:
            _reject('HOSTED_PORTABLE_REVIEW_DOCUMENTS_DISAGREE')
        project = body['project']
        if not isinstance(project, dict) or not isinstance(project.get('generation'), int):
            _reject('HOSTED_PORTABLE_REVIEW_SOURCE_GENERATION_INVALID')
        if any(str(r.get('id') or '') == '' for r in tabs + chapters + scenes + blocks):
            _reject('HOSTED_PORTABLE_REVIEW_FROZEN_ID_MISSING')
        tab_order = {str(tab['id']): i for i, tab in enumerate(sorted(tabs, key=lambda t: (t.get('position', 0), str(t['id']))))}
        chapter_order = {str(ch['id']): i for i, ch in enumerate(sorted(
            chapters, key=lambda ch: (tab_order.get(str(ch.get('tab_id')), 10**9), ch.get('position', 0), str(ch['id']))))}
        if len(chapter_order) != len(chapters) or len(tab_order) != len(tabs):
            _reject('HOSTED_PORTABLE_REVIEW_DUPLICATE_FROZEN_ID')
        scenes = sorted(scenes, key=lambda s: (tab_order.get(str(s.get('tab_id')), 10**9), s.get('position', 0), str(s['id'])))
        blocks = sorted(blocks, key=lambda b: (chapter_order.get(str(b.get('chapter_id')), 10**9), b.get('position', 0), str(b['id'])))
        if any(str(c.get('tab_id')) not in tab_order for c in chapters) or any(str(b.get('chapter_id')) not in chapter_order for b in blocks):
            _reject('HOSTED_PORTABLE_REVIEW_FROZEN_HIERARCHY_INVALID')
        # No hidden source row silently falls through the local projection.
        allowed_ch = {'id','project_id','tab_id','position','title','description','is_implicit','generation','created_at','updated_at','chapter_id','content_html','content_hash'}
        allowed_sc = {'id','project_id','tab_id','position','title','description','generation','created_at','updated_at','scene_id'}
        allowed_bl = {'id','project_id','tab_id','chapter_id','scene_id','position','block_type','semantic_style',
                      'content_html','text_content','image_path','image_width','image_footer','image_description',
                      'image_row_id','content_hash','generation','created_at','updated_at','block_id',
                      'payload_object','object_name','object_generation','object_sha256','object_size'}
        for group, allowed in ((chapters,allowed_ch),(scenes,allowed_sc),(blocks,allowed_bl)):
            if any(set(obj) - allowed for obj in group):
                _reject('HOSTED_PORTABLE_REVIEW_FROZEN_FIELD_UNSUPPORTED')
        for i, ch in enumerate(sorted(chapters, key=lambda c: chapter_order[str(c['id'])])):
            result.setdefault('review_snapshot_chapters', []).append(_record('chapter', sid, ch, {
                'source_chapter_id': ch['id'], 'source_tab_id': ch.get('tab_id'), 'ordinal': i,
                'position': ch.get('position', 0), 'title': ch.get('title', ''),
                'is_implicit': ch.get('is_implicit', False), 'description': ch.get('description', '')}))
        for i, scene in enumerate(scenes):
            result.setdefault('review_snapshot_scenes', []).append(_record('scene', sid, scene, {
                'source_scene_id': scene['id'], 'source_tab_id': scene.get('tab_id'), 'ordinal': i,
                'position': scene.get('position', 0), 'title': scene.get('title', ''),
                'description': scene.get('description', '')}))
        for i, block in enumerate(blocks):
            result.setdefault('review_snapshot_blocks', []).append(_record('block', sid, block, {
                'source_block_id': block['id'], 'source_tab_id': block.get('tab_id'),
                'source_chapter_id': block['chapter_id'], 'source_scene_id': block.get('scene_id'),
                'ordinal': i, 'position': block.get('position', 0),
                'block_type': block.get('block_type', 'paragraph'), 'semantic_style': block.get('semantic_style', 'body'),
                'content_html': block.get('content_html', ''), 'text_content': block.get('text_content', ''),
                'image_path': block.get('image_path', ''), 'image_width': block.get('image_width', 100),
                'image_footer': block.get('image_footer', ''), 'image_description': block.get('image_description', ''),
                'image_row_id': block.get('image_row_id', ''), 'content_hash': block.get('content_hash', '')}))
        status = str(row.get('status') or 'active')
        if status not in {'active', 'open', 'paused', 'closed'}:
            _reject('HOSTED_PORTABLE_REVIEW_STATUS_UNSUPPORTED')
        row.clear()
        row.update({'id': sid, 'review_id': rid, 'round_number': 1,
                    'status': 'open' if status == 'active' else status,
                    'source_generation': project['generation'], 'scope_type': 'manuscript',
                    'permission_read': 1, 'permission_comment': 0,
                    'permission_suggest': int(bool(frozen.get('permission_suggest'))),
                    'permission_feedback': 0, 'content_hash': 'pending-recomputation'})
        normalized_ids.add(sid)
        if review.get('active_snapshot_id') not in (None, '', sid):
            _reject('HOSTED_PORTABLE_REVIEW_ACTIVE_SNAPSHOT_UNKNOWN')
        if review.get('closed_snapshot_ids'):
            _reject('HOSTED_PORTABLE_REVIEW_ROUND_ORDER_UNKNOWN')
        current_status = str(review.get('status') or 'draft')
        desired_status = 'open' if status == 'active' else status
        if current_status not in (desired_status, 'active' if desired_status == 'open' else desired_status):
            _reject('HOSTED_PORTABLE_REVIEW_LIFECYCLE_INCONSISTENT')
        review.pop('active_snapshot_id', None)
        review.pop('closed_snapshot_ids', None)
        if review.get('status') == 'active':
            review['status'] = 'open'
    for row in reviews.values():
        if row.get('status') == 'active':
            # A missing immutable round cannot be materialized as an Open
            # local Review; doing so would invent a review lifecycle.
            _reject('HOSTED_PORTABLE_REVIEW_ACTIVE_SNAPSHOT_UNKNOWN')
    return result, normalized_ids


def checkpoint_v1(row: Mapping[str, Any], ids: Mapping[tuple[str,str], int | str],
                  assets: Mapping[str, bytes], created: str) -> dict[str, Any]:
    """Encode an *actual* local version-1 capture, never arbitrary project JSON.

    This strict subset supports checkpoints with no frozen annotations/comments;
    their wider codecs require additional evidence and must not be discarded.
    """
    snapshot = row.get('snapshot')
    if not isinstance(snapshot, dict) or snapshot.get('version') != 1 or snapshot.get('scope') not in {'book','chapter'}:
        _reject('HOSTED_PORTABLE_CHECKPOINT_CODEC_REQUIRED')
    if str(row.get('scope') or '') != snapshot['scope']:
        _reject('HOSTED_PORTABLE_CHECKPOINT_SCOPE_MISMATCH')
    expected = {'version','scope','captured_at','tab','chapter','chapters','blocks','annotations',
                'comment_threads','comment_messages','scenes','include_scenes','include_markup','include_notes'}
    if set(snapshot) - expected:
        _reject('HOSTED_PORTABLE_CHECKPOINT_UNKNOWN_SNAPSHOT_FIELD')
    if any(not isinstance(snapshot.get(k), list) or snapshot[k] for k in ('annotations','comment_threads','comment_messages')):
        _reject('HOSTED_PORTABLE_CHECKPOINT_MARKUP_CODEC_REQUIRED')
    for flag in ('blocks','scenes'):
        if not isinstance(snapshot.get(flag), list):
            _reject('HOSTED_PORTABLE_CHECKPOINT_CODEC_REQUIRED')
    for flag in ('include_markup','include_notes'):
        if snapshot.get(flag) is not False:
            _reject('HOSTED_PORTABLE_CHECKPOINT_MARKUP_CODEC_REQUIRED')
    def rebase(obj: Mapping[str, Any], key: str, table: str, *, nullable: bool=False):
        old = obj.get(key)
        if nullable and old in (None, ''):
            return None
        new = ids.get((table, str(old)))
        if new is None:
            _reject(f'HOSTED_PORTABLE_CHECKPOINT_HISTORY_ID_MISSING:{key}')
        return new
    saved = dict(snapshot)
    provider = {'project_id','generation','updated_at','payload_object','object_name',
                'object_generation','object_sha256','object_size'}
    def without_provider(value: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict):
            _reject('HOSTED_PORTABLE_CHECKPOINT_CODEC_REQUIRED')
        return {key: val for key,val in value.items() if key not in provider and not key.startswith('_bm_')}
    tab = saved.get('tab')
    if not isinstance(tab, dict):
        _reject('HOSTED_PORTABLE_CHECKPOINT_CODEC_REQUIRED')
    saved['tab'] = dict(without_provider(tab), id=rebase(tab,'id','document_tabs'))
    saved['tab'].pop('tab_id', None)
    kind = saved['tab'].pop('kind', None)
    if kind is not None:
        if kind not in ('manuscript','document','notes','secondary'):
            _reject('HOSTED_PORTABLE_TAB_KIND_UNSUPPORTED')
        saved['tab']['is_manuscript'] = int(kind == 'manuscript')
    chapter = saved.get('chapter')
    if snapshot['scope'] == 'chapter':
        if not isinstance(chapter, dict) or 'chapters' in snapshot:
            _reject('HOSTED_PORTABLE_CHECKPOINT_CODEC_REQUIRED')
        saved['chapter'] = dict(without_provider(chapter), id=rebase(chapter,'id','chapters'),
                                tab_id=rebase(chapter,'tab_id','document_tabs'))
        if saved['chapter'].get('chapter_id') == chapter.get('id'):
            saved['chapter'].pop('chapter_id', None)
    else:
        if not isinstance(saved.get('chapters'), list) or chapter is not None:
            _reject('HOSTED_PORTABLE_CHECKPOINT_CODEC_REQUIRED')
        saved['chapters'] = [dict(without_provider(ch), id=rebase(ch,'id','chapters'),
                                  tab_id=rebase(ch,'tab_id','document_tabs')) for ch in saved['chapters']]
        for old_chapter, portable_chapter in zip(snapshot['chapters'], saved['chapters']):
            if portable_chapter.get('chapter_id') == old_chapter.get('id'):
                portable_chapter.pop('chapter_id', None)
    saved['scenes'] = [dict(without_provider(scene), id=rebase(scene,'id','scenes'),
                             tab_id=rebase(scene,'tab_id','document_tabs')) for scene in saved['scenes']]
    for old_scene, portable_scene in zip(snapshot['scenes'], saved['scenes']):
        if portable_scene.get('scene_id') == old_scene.get('id'):
            portable_scene.pop('scene_id', None)
    converted_blocks = []
    for b in snapshot['blocks']:
        if not isinstance(b, dict):
            _reject('HOSTED_PORTABLE_CHECKPOINT_CODEC_REQUIRED')
        if (str(b.get('id')) and ('manuscript_blocks',str(b.get('id'))) not in ids):
            _reject('HOSTED_PORTABLE_CHECKPOINT_HISTORY_ID_MISSING:block')
        result = without_provider(b)
        result.pop('block_id', None)
        if 'chapter_id' in b:
            result['chapter_id'] = rebase(b,'chapter_id','chapters')
        if b.get('scene_id') is not None:
            result['scene_id'] = rebase(b,'scene_id','scenes')
        converted_blocks.append(result)
    saved['blocks'] = converted_blocks
    raw = json.dumps(saved, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    digest = hashlib.sha256(raw).hexdigest()
    name = str(row.get('name') or '')
    import re as _re
    word_count = len(_re.findall(r"[\w’'-]+", ' '.join(str(b.get('text_content') or '') for b in converted_blocks), _re.UNICODE))
    chapter_obj = saved.get('chapter') or {}
    # The outer source IDs remain hosted values so the generic converter can
    # rebase them once. The nested immutable bytes carry local IDs already.
    return {k:v for k,v in row.items() if k != 'snapshot'} | {
        'snapshot_blob': zlib.compress(raw,6), 'snapshot_hash': digest,
        'word_count': word_count, 'source_tab_id': tab['id'],
        'source_tab_title': str(tab.get('title') or ''),
        'source_tab_created_at': str(tab.get('created_at') or created),
        'source_chapter_id': chapter.get('id') if isinstance(chapter,dict) else None,
        'source_chapter_title': str(chapter_obj.get('title') or ''),
        'source_chapter_created_at': str(chapter_obj.get('created_at') or ''),
        'source_chapter_position': chapter_obj.get('position'),
        'current_source_chapter_id': chapter.get('id') if isinstance(chapter,dict) else None,
        'current_source_chapter_created_at': str(chapter_obj.get('created_at') or ''),
        'include_scenes': int(bool(snapshot.get('include_scenes',True))),
        'include_markup': 0, 'include_notes': 0,
    }


def finalize_review_round_hashes(con, snapshot_ids: set[str]):
    """Seal read-only frozen manuscript rounds with required restore baselines.

    The local Review model requires each round to have a checkpoint. This
    derives one from the *frozen* manuscript blocks, not mutable live content.
    No historical annotations, markers or questions are invented.
    """
    from storage.values import review_snapshot_content_hash
    con.row_factory = __import__('sqlite3').Row
    for sid in sorted(snapshot_ids):
        snap = con.execute('SELECT * FROM review_snapshots WHERE id=?', (sid,)).fetchone()
        if snap is None:
            _reject('HOSTED_PORTABLE_REVIEW_SNAPSHOT_MISSING')

        def records(table, order):
            return [dict(row) for row in con.execute(f'SELECT * FROM "{table}" WHERE snapshot_id=? ORDER BY {order}', (sid,))]

        chapters = records('review_snapshot_chapters', 'ordinal,id')
        scenes = records('review_snapshot_scenes', 'ordinal,id')
        blocks = records('review_snapshot_blocks', 'ordinal,id')
        tab_row = con.execute('SELECT * FROM document_tabs WHERE project_id=1 AND is_manuscript=1').fetchone()
        if tab_row is None or any(row['source_tab_id'] != tab_row['id'] for row in chapters + scenes + blocks):
            _reject('HOSTED_PORTABLE_REVIEW_MULTITAB_BASELINE_UNSUPPORTED')
        perms = {k: snap[k] for k in ('permission_read', 'permission_comment', 'permission_suggest', 'permission_feedback')}
        digest = review_snapshot_content_hash(snap['review_id'], snap['source_generation'], snap['scope_type'], perms,
                                              chapters, scenes, blocks, [], [])
        con.execute('UPDATE review_snapshots SET content_hash=? WHERE id=?', (digest, sid))
        chapter_keys = {'id': 'source_chapter_id', 'tab_id': 'source_tab_id', 'position': 'position',
                        'title': 'title', 'description': 'description', 'created_at': 'created_at'}
        block_keys = {'id': 'source_block_id', 'chapter_id': 'source_chapter_id', 'scene_id': 'source_scene_id',
                      'position': 'position', 'block_type': 'block_type', 'semantic_style': 'semantic_style',
                      'content_html': 'content_html', 'text_content': 'text_content', 'image_path': 'image_path',
                      'image_width': 'image_width', 'image_footer': 'image_footer',
                      'image_description': 'image_description', 'image_row_id': 'image_row_id'}
        scene_keys = {'id': 'source_scene_id', 'tab_id': 'source_tab_id', 'position': 'position',
                      'title': 'title', 'description': 'description', 'created_at': 'created_at'}
        frozen_chapters = [{key: row[value] for key, value in chapter_keys.items()} for row in chapters]
        frozen_blocks = [{key: row[value] for key, value in block_keys.items()} for row in blocks]
        frozen_scenes = [{key: row[value] for key, value in scene_keys.items()} for row in scenes]
        captured = str(snap['created_at'])
        baseline = {
            'version': 1, 'scope': 'book', 'captured_at': captured,
            'tab': {key: tab_row[key] for key in ('id', 'title', 'description', 'is_manuscript', 'position', 'created_at')},
            'chapters': frozen_chapters, 'blocks': frozen_blocks, 'scenes': frozen_scenes,
            'annotations': [], 'comment_threads': [], 'comment_messages': [],
            'include_scenes': True, 'include_markup': False, 'include_notes': False,
        }
        raw = json.dumps(baseline, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        blob = zlib.compress(raw, 6)
        digest = hashlib.sha256(raw).hexdigest()
        parent = con.execute('SELECT name FROM reviews WHERE id=?', (snap['review_id'],)).fetchone()
        label = f"{str(parent['name'] if parent else 'Review')[:110]} · R{snap['round_number']} (portable baseline)"
        contents = ' '.join(str(block.get('text_content') or '') for block in frozen_blocks)
        words = len(re.findall(r"[\w’'-]+", contents, re.UNICODE))
        checkpoint_id = con.execute("""INSERT INTO checkpoints(project_id,scope,name,description,
            source_tab_id,source_tab_title,source_tab_created_at,snapshot_blob,snapshot_hash,
            word_count,include_scenes,include_markup,include_notes,created_at,updated_at)
            VALUES(1,'book',?,?,?,?,?,?,?,?,1,0,0,?,?)""",
            (label, '', tab_row['id'], tab_row['title'], tab_row['created_at'], blob, digest, words, captured, captured)).lastrowid
        con.execute("""INSERT INTO review_snapshot_checkpoints(snapshot_id,checkpoint_id,ordinal,source_chapter_id,created_at)
            VALUES(?,?,0,NULL,?)""", (sid, checkpoint_id, captured))
