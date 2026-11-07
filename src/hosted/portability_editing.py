"""Exact Highlights metadata translation between hosted state and Schema 42.

Keep marker ownership after rename/clear using the native project's existing
registry. This registry is product metadata, never membership or account state.
"""
import hashlib
import json

KINDS = frozenset({'telling', 'filter', 'adverbs', 'qualifiers', 'wordy', 'redundancies', 'emotions'})
REGISTRY = 'editing-search-markers/v1'


def project_row(table, row, native_id, created, registry):
    if table in {'document_tabs', 'chapters'} and 'native_created_at' in row:
        timestamp = row['native_created_at']
        if not isinstance(timestamp, str) or not timestamp or len(timestamp) > 128:
            raise ValueError('HOSTED_PORTABLE_NATIVE_LINEAGE_INVALID')
        # Import kept the original lineage separately from fresh hosted server
        # timestamps. Restore that portable source value, never hosted authority.
        row = {key: value for key, value in row.items() if key != 'native_created_at'}
        row['created_at'] = timestamp
    if table == 'annotations' and 'source_generation' in row:
        # Authorized canonical reads already materialized staleness. A provider
        # revision has no meaning as write authority in the new local database.
        if type(row['source_generation']) is not int or row['source_generation'] < 1:
            raise ValueError('HOSTED_PORTABLE_ANNOTATION_SOURCE_INVALID')
        row = {key: value for key, value in row.items() if key != 'source_generation'}
    if table == 'markup_labels' and 'editing_search_categories' in row:
        kinds = row['editing_search_categories']
        if (not isinstance(kinds, list) or len(kinds) > len(KINDS)
                or any(not isinstance(kind, str) or kind not in KINDS for kind in kinds)
                or len(set(kinds)) != len(kinds)):
            raise ValueError('HOSTED_PORTABLE_EDITING_REGISTRY_INVALID')
        for kind in kinds:
            if kind in registry:
                raise ValueError('HOSTED_PORTABLE_EDITING_MARKER_AMBIGUOUS')
            registry[kind] = dict(id=native_id, created_at=row.get('created_at', created))
        row = {key: value for key, value in row.items() if key != 'editing_search_categories'}
    return row


def native_categories(con):
    """Only live matching native marker identities can regain category ownership."""
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ui_state'").fetchone():
        return {}
    result = {}
    for project_id, raw in con.execute('SELECT project_id,value_json FROM ui_state WHERE key=?', (REGISTRY,)):
        if not isinstance(raw, str) or len(raw.encode()) > 16 * 1024:
            raise ValueError('HOSTED_PORTABLE_EDITING_REGISTRY_INVALID')
        value = json.loads(raw)
        if not isinstance(value, dict) or not set(value) <= KINDS:
            raise ValueError('HOSTED_PORTABLE_EDITING_REGISTRY_INVALID')
        for kind, hint in value.items():
            if (not isinstance(hint, dict) or type(hint.get('id')) is not int
                    or not isinstance(hint.get('created_at'), str)):
                raise ValueError('HOSTED_PORTABLE_EDITING_REGISTRY_INVALID')
            # Native Undo may retain tombstoned hints. Match its live identity
            # rule instead of assigning a deleted/reused ID to another marker.
            marker = con.execute('SELECT id FROM markup_labels WHERE project_id=? AND id=? AND created_at=?',
                                 (project_id, hint['id'], hint['created_at'])).fetchone()
            if marker:
                result.setdefault(str(marker[0]), []).append(kind)
    return result


def native_annotation_ids(pid, entities):
    """Recover generated pack grouping after native integer ID translation.

    Group only the same canonical document/marker/category; native manual
    annotations retain ordinary identities. Every range is validated by the
    unpublished hosted importer before the new project can become readable.
    """
    annotations = entities.get('annotation', [])
    if len(annotations) > 100_000:
        raise ValueError('HOSTED_PACKAGE_ANNOTATION_LIMIT')
    chapters = {str(row['id']): row.get('tab_id') for row in entities.get('chapter', [])}
    blocks = {str(row['id']): row for row in entities.get('manuscript_block', [])}
    result = {}
    for row in annotations:
        body = str(row.get('body') or '')
        kind = body.removeprefix('editing-search/v1:')
        if not body.startswith('editing-search/v1:') or kind not in KINDS or row.get('annotation_type') != 'highlight':
            continue
        block = blocks.get(str(row.get('block_id')))
        tab = chapters.get(str(block.get('chapter_id'))) if block else None
        if tab is None or row.get('label_id') is None:
            raise ValueError('HOSTED_PACKAGE_ANNOTATION_TARGET_INVALID')
        group = json.dumps([pid, str(tab), str(row['label_id']), kind], separators=(',', ':'))
        head = hashlib.sha256(group.encode()).hexdigest()[:24]
        child = hashlib.sha256(str(row['id']).encode()).hexdigest()[:24]
        result[str(row['id'])] = f'eann_{head}_{child}'
    return result
