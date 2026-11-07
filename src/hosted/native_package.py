from __future__ import annotations

"""Pure, bounded Schema-42 import planning; never hosted storage authority.

A native archive is a self-contained SQLite backup, not a live database plus a
WAL. Validate original bytes before adapting its journal header for deserialize.
All relational paths are resolved before the first provider/object staging call.
"""

import base64
import hashlib
import io
import json
import re
import zipfile
import sqlite3
import time
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from string import Formatter

from hosted.domain_store import _ID_FIELDS, HostedDomainStore
from hosted.firestore.ids import require_opaque_id, require_project_id
from hosted.firestore.model_registry import MODELS
from hosted.firestore.repositories import _canonical_payload, _reject_managed_create_fields, render_model_path
from hosted.portability_editing import native_annotation_ids, native_categories
from hosted.native_history import prepare_native_history
from security.image_security import HOSTED_DEFAULT_IMAGE_BYTES, UnsafeImage, inspect_hosted_image_bytes
from storage.schema import SCHEMA_SQL, SCHEMA_VERSION
from storage.schema_contracts import _GENERATION_CANONICAL_TABLES

_MAX_ROWS = 100_000
_SHA = re.compile(r'[0-9a-f]{64}')


def native_database_bytes(archive, manifest):
    """Verify the package's actual bytes, then adapt only the in-memory copy.

    sqlite3_deserialize does not support WAL mode. SQLite documents changing
    bytes 18/19 to 1 for a serialized, standalone database image. A WAL sidecar
    must never be ignored: native v2 permits only the DB and finalized assets.
    A WAL-marked image additionally needs its canonical snapshot hash, checked
    after deserialize, so a stale main DB cannot silently replace its WAL state.
    https://www.sqlite.org/c3ref/deserialize.html
    """
    members = [item for item in archive.infolist() if not item.is_dir()]
    names = {item.filename for item in members}
    if len(names) != len(members):
        raise ValueError('HOSTED_PACKAGE_DUPLICATE_PATH')
    if 'project.sqlite3' not in names:
        raise ValueError('HOSTED_PACKAGE_DATABASE_MISSING')
    if any(name not in {'manifest.json', 'project.sqlite3'} and not name.startswith('assets/') for name in names):
        raise ValueError('HOSTED_PACKAGE_UNEXPECTED_FILE')
    if manifest.get('schema_version') != SCHEMA_VERSION:
        raise ValueError('HOSTED_PACKAGE_SCHEMA_UNSUPPORTED')
    hashes = manifest.get('file_hashes')
    if not isinstance(hashes, dict) or set(hashes) != names - {'manifest.json'}:
        raise ValueError('HOSTED_PACKAGE_HASH_SET_MISMATCH')
    for name, expected in hashes.items():
        if not isinstance(expected, str) or _SHA.fullmatch(expected) is None:
            raise ValueError('HOSTED_PACKAGE_HASH_INVALID')
        digest = hashlib.sha256()
        with archive.open(name) as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise ValueError('HOSTED_PACKAGE_HASH_MISMATCH')
    fingerprint = manifest.get('project_fingerprint')
    if fingerprint is not None and fingerprint != hashlib.sha256(
            json.dumps(hashes, sort_keys=True, separators=(',', ':')).encode()).hexdigest():
        raise ValueError('HOSTED_PACKAGE_FINGERPRINT_MISMATCH')
    database = archive.read('project.sqlite3')
    if len(database) < 100 or database[:16] != b'SQLite format 3\0':
        raise ValueError('HOSTED_PACKAGE_DATABASE_INVALID')
    journal = database[18:20]
    if journal == b'\x02\x02':
        canonical = manifest.get('canonical_state_hash')
        if not isinstance(canonical, str) or _SHA.fullmatch(canonical) is None:
            raise ValueError('HOSTED_PACKAGE_WAL_SNAPSHOT_UNVERIFIED')
        database = database[:18] + b'\x01\x01' + database[20:]
    elif journal != b'\x01\x01':
        raise ValueError('HOSTED_PACKAGE_DATABASE_INVALID')
    return database


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def canonical_connection_hash(con, asset_hashes):
    """The local canonical-state wire hash, without opening a filesystem path.

    Keep parity with revision_store.local_store.canonical_state_hash. This
    reader deliberately uses the unchanged source schema and row values.
    """
    from revision_store.local_store import _hash_value
    digest = hashlib.sha256()
    tables = {str(row[0]) for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in sorted(set(_GENERATION_CANONICAL_TABLES)):
        if table not in tables:
            continue
        schema = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        digest.update(b'TABLE\0' + table.encode() + b'\0')
        digest.update(str(schema[0] or '').encode())
        info = list(con.execute(f'PRAGMA table_info({_quote(table)})'))
        columns = [str(row[1]) for row in info]
        for column in columns:
            digest.update(b'COL\0' + column.encode() + b'\0')
        pk = [str(row[1]) for row in sorted((row for row in info if int(row[5] or 0) > 0), key=lambda row: int(row[5]))]
        order = ','.join(_quote(column) for column in pk) if pk else 'rowid'
        select = ','.join(_quote(column) for column in columns)
        for row in con.execute(f'SELECT {select} FROM {_quote(table)} ORDER BY {order}'):
            digest.update(b'ROW\0')
            for value in row:
                _hash_value(digest, value)
            digest.update(b'\0ENDROW\0')
    digest.update(b'ASSETS\0')
    for name, value in sorted((k, v) for k, v in asset_hashes.items() if k != 'project.sqlite3'):
        digest.update(name.encode() + b'\0' + value.encode('ascii') + b'\0')
    return digest.hexdigest()


def validate_native_connection(con, manifest):
    rows = con.execute('SELECT id,project_uid,project_generation FROM projects').fetchall()
    if len(rows) != 1 or type(rows[0]['id']) is not int or rows[0]['id'] < 1 or rows[0]['project_uid'] != manifest.get('project_id'):
        raise ValueError('HOSTED_PACKAGE_IDENTITY_MISMATCH')
    require_project_id(rows[0]['project_uid'])
    if type(rows[0]['project_generation']) is not int or rows[0]['project_generation'] < 1 or rows[0]['project_generation'] != manifest.get('project_generation'):
        raise ValueError('HOSTED_PACKAGE_GENERATION_MISMATCH')
    schema = con.execute("SELECT value FROM storage_meta WHERE key='schema_version'").fetchone()
    if schema is None or str(schema[0]) != str(SCHEMA_VERSION):
        raise ValueError('HOSTED_PACKAGE_SCHEMA_UNSUPPORTED')
    expected = manifest.get('canonical_state_hash')
    if expected is not None and canonical_connection_hash(con, manifest['file_hashes']) != expected:
        raise ValueError('HOSTED_PACKAGE_CANONICAL_STATE_MISMATCH')


def _fields(template):
    return [field for _, field, _, _ in Formatter().parse(template) if field]


@lru_cache(maxsize=1)
def _contract():
    contract = json.loads((Path(__file__).resolve().parents[2] / 'docs' / 'HOSTED_DATA_MODEL_V1.json').read_text())
    mapping = {row['logical_entity']: row['source_table'] for row in contract['schema42_mappings']}
    by_table = {table: logical for logical, table in mapping.items()}
    # Never use an uploaded FK declaration as the relationship policy.
    with sqlite3.connect(':memory:') as schema:
        schema.executescript(SCHEMA_SQL)
        schema_objects = {(row[0], row[1]): row[2] for row in schema.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE type IN ('table','index')")}
        columns = {logical: {str(row[1]) for row in schema.execute(f'PRAGMA table_info({_quote(table)})')}
                   for logical, table in mapping.items()}
        foreign = {logical: {str(row[3]): by_table.get(str(row[2]))
                            for row in schema.execute(f'PRAGMA foreign_key_list({_quote(table)})')
                            if row[4] == 'id' and row[3] != 'project_id'}
                   for logical, table in mapping.items()}
    foreign['annotation']['thread_id'] = 'comment_thread'
    return mapping, foreign, columns, schema_objects


_TYPED = {'chapter': 'chapter', 'scene': 'scene', 'canvas': 'canvas',
          'story_map': 'story_map', 'map': 'story_map', 'task': 'task',
          'manuscript_block': 'manuscript_block', 'block': 'manuscript_block',
          'markup_label': 'markup_label', 'label': 'markup_label',
          'document': 'document_tab', 'tab': 'document_tab', 'node': 'canvas_node',
          'canvas_node': 'canvas_node', 'edge': 'canvas_edge', 'canvas_edge': 'canvas_edge',
          'map_item': 'map_item', 'map_line': 'map_line', 'line': 'map_line',
          'review': 'review', 'review_comment': 'review_comment',
          'review_highlight': 'review_highlight', 'review_suggestion': 'review_suggestion'}


def _historical_id(logical, value):
    if value is None or str(value) == '':
        raise ValueError('HOSTED_PACKAGE_HISTORY_REFERENCE_INVALID')
    return require_opaque_id(_ID_FIELDS[logical][1] + 'legacy_' + str(value))


def _semantic_references(logical, row, payload, resolve):
    if logical == 'map_item':
        for edge in ('start', 'end'):
            kind, value = row.get(edge + '_anchor_type'), row.get(edge + '_anchor_id')
            if kind in {'chapter', 'scene'}:
                payload[edge + '_anchor_id'] = resolve(kind, value)
            elif kind == 'manuscript_range':
                if value not in (None, '') or row.get(edge + '_block_id') in (None, ''):
                    raise ValueError('HOSTED_PACKAGE_MAP_ANCHOR_INVALID')
            elif kind not in (None, '') or value not in (None, ''):
                raise ValueError('HOSTED_PACKAGE_MAP_ANCHOR_INVALID')
        key = row.get('source_key')
        if isinstance(key, str):
            match = re.fullmatch(r'(chapter|scene):(.+)', key)
            if match:
                payload['source_key'] = match[1] + ':' + resolve(match[1], match[2])
    elif logical == 'cross_feature_reference':
        for edge in ('source', 'target'):
            kind, value = row.get(edge + '_type'), row.get(edge + '_id')
            if kind in _TYPED and value not in (None, ''):
                payload[edge + '_id'] = resolve(_TYPED[kind], value)
    elif logical == 'canvas_version_item':
        kind = row.get('entity_type')
        if kind not in {'chapter', 'scene'}:
            raise ValueError('HOSTED_PACKAGE_CANVAS_VERSION_ENTITY_INVALID')
        # Native Structure Versions deliberately retain deleted source rows.
        payload['entity_id'] = _historical_id(kind, row.get('entity_id'))
    elif logical == 'canvas_version':
        payload['workspace_json'] = _canvas_workspace(row.get('workspace_json'))


def _canvas_workspace(raw):
    try:
        saved = json.loads(raw or '{}')
    except (ValueError, TypeError) as exc:
        raise ValueError('HOSTED_PACKAGE_CANVAS_WORKSPACE_INVALID') from exc
    if not isinstance(saved, dict):
        raise ValueError('HOSTED_PACKAGE_CANVAS_WORKSPACE_INVALID')
    fields = {'label_id': 'markup_label', 'chapter_id': 'chapter', 'scene_id': 'scene',
              'icon_marker_id': 'icon_marker', 'marker_id': 'markup_label',
              'from_node_id': 'canvas_node', 'to_node_id': 'canvas_node'}
    for group, logical in (('nodes', 'canvas_node'), ('edges', 'canvas_edge')):
        rows = saved.get(group, [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError('HOSTED_PACKAGE_CANVAS_WORKSPACE_INVALID')
        for row in rows:
            row['id'] = _historical_id(logical, row.get('id'))
            for key, target in fields.items():
                if row.get(key) is not None:
                    row[key] = _historical_id(target, row[key])
    styles = saved.get('structural_styles', {})
    if not isinstance(styles, dict):
        raise ValueError('HOSTED_PACKAGE_CANVAS_WORKSPACE_INVALID')
    rebased = {}
    for key, value in styles.items():
        match = re.fullmatch(r'(chapter|scene):(.+)', key)
        if match is None or not isinstance(value, dict):
            raise ValueError('HOSTED_PACKAGE_CANVAS_WORKSPACE_INVALID')
        if value.get('label_id') is not None:
            value['label_id'] = _historical_id('markup_label', value['label_id'])
        rebased[match[1] + ':' + _historical_id(match[1], match[2])] = value
    if 'structural_styles' in saved:
        saved['structural_styles'] = rebased
    return json.dumps(saved, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _json_safe(value):
    if isinstance(value, bytes):
        return {'$bytes': base64.b64encode(value).decode('ascii')}
    return value


def plan_native_records(con, pid, *, excluded=()):
    return _plan_native_records(con, pid, excluded=excluded)[0]


def _plan_native_records(con, pid, *, excluded=()):
    """Materialize and validate records plus retained historical asset bytes."""
    mapping, foreign, columns, _ = _contract()
    supported = {logical: table for logical, table in mapping.items()
                 if logical in MODELS and logical in _ID_FIELDS and logical not in excluded}
    ordered = sorted(supported, key=lambda logical: (len(MODELS[logical].path_template.split('/')), logical))
    tables = {str(row[0]) for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for logical, table in mapping.items():
        if table in tables:
            actual = {str(row[1]) for row in con.execute(f'PRAGMA table_info({_quote(table)})')}
            if actual != columns[logical]:
                raise ValueError(f'HOSTED_PACKAGE_SCHEMA_COLUMNS_INVALID:{logical}')
    rows_by_entity, index, id_maps = {}, {}, {}
    count = 0
    for logical in ordered:
        table = supported[logical]
        rows = [dict(row) for row in con.execute(f'SELECT * FROM {_quote(table)} LIMIT {_MAX_ROWS + 1}')] if table in tables else []
        count += len(rows)
        if count > _MAX_ROWS:
            raise ValueError('HOSTED_PACKAGE_ENTITY_LIMIT')
        rows_by_entity[logical] = rows
        for row in rows:
            old = str(row.get('id') or '')
            if not old or (logical, old) in index:
                raise ValueError('HOSTED_PACKAGE_ENTITY_ID_INVALID')
            index[logical, old] = row
            id_maps[logical, old] = _ID_FIELDS[logical][1] + 'legacy_' + old
    def resolve(logical, value):
        result = id_maps.get((logical, str(value)))
        if result is None:
            raise ValueError(f'HOSTED_PACKAGE_REFERENCE_INVALID:{logical}')
        return result
    for old, eid in native_annotation_ids(pid, rows_by_entity).items():
        id_maps['annotation', old] = eid
    for logical, field, target in [('review_scope_chapter', 'chapter_id', 'chapter'),
                                   ('review_scope_scene', 'scene_id', 'scene'),
                                   ('review_questionnaire_assignment', 'questionnaire_id', 'questionnaire')]:
        for row in rows_by_entity.get(logical, []):
            id_maps[logical, str(row['id'])] = resolve(target, row[field])
    # The model path defines the unique immediate parent. Follow its trusted
    # relationship recursively, inheriting ancestors (Map via Line, Canvas via
    # Version). No field-name guess, arbitrary first match, or raw-ID fallback.
    by_path = {MODELS[logical].path_template: logical for logical in supported}
    identities = {}
    def identity(logical, old):
        key = logical, str(old)
        if key in identities:
            return identities[key]
        row = index.get(key)
        if row is None:
            raise ValueError('HOSTED_PACKAGE_PARENT_MISSING')
        template = MODELS[logical].path_template
        fields = _fields(template)
        ids = {'project_id': pid}
        if len(fields) > 2:
            parent = by_path.get('/'.join(template.split('/')[:-2]))
            if parent is None:
                raise ValueError('HOSTED_PACKAGE_PARENT_POLICY_INVALID')
            parent_field = _ID_FIELDS[parent][0]
            parent_raw = row.get(parent_field)
            if foreign[logical].get(parent_field) != parent:
                raise ValueError('HOSTED_PACKAGE_PARENT_POLICY_INVALID')
            ids.update(identity(parent, parent_raw))
            for field in fields[1:-1]:
                if row.get(field) is not None:
                    target = foreign[logical].get(field)
                    if target is None or resolve(target, row[field]) != ids.get(field):
                        raise ValueError('HOSTED_PACKAGE_PARENT_AMBIGUOUS')
        ids[fields[-1]] = resolve(logical, old)
        render_model_path(MODELS[logical], **ids)
        identities[key] = ids
        return ids
    project_id = con.execute('SELECT id FROM projects').fetchone()[0]
    if any('project_id' in row and row['project_id'] != project_id
           for rows in rows_by_entity.values() for row in rows):
        raise ValueError('HOSTED_PACKAGE_PROJECT_REFERENCE_INVALID')
    editing = native_categories(con)
    history = prepare_native_history(con, pid, id_maps)
    planned, paths = [], set()
    for logical in ordered:
        for row in rows_by_entity[logical]:
            old = str(row['id'])
            ids = identity(logical, old)
            eid = ids[_ID_FIELDS[logical][0]]
            path = render_model_path(MODELS[logical], **ids)
            if path in paths:
                raise ValueError('HOSTED_PACKAGE_ENTITY_COLLISION')
            paths.add(path)
            payload = {}
            for key, value in row.items():
                if key in {'id', 'project_id', 'created_at', 'updated_at', 'generation'}:
                    continue
                override = (logical, old, key)
                if override in history.references:
                    payload[key] = history.references[override]
                    continue
                target = foreign[logical].get(key)
                if target in supported and value is not None:
                    peer = identity(target, value)
                    target_final = _ID_FIELDS[target][0]
                    own_final = _ID_FIELDS[logical][0]
                    for field in (set(ids) & set(peer)) - {target_final, own_final}:
                        if ids[field] != peer[field]:
                            raise ValueError('HOSTED_PACKAGE_PARENT_AMBIGUOUS')
                    value = resolve(target, value)
                payload[key] = _json_safe(value)
            _semantic_references(logical, row, payload, resolve)
            if logical == 'checkpoint' and 'snapshot_blob' in payload:
                payload['snapshot'] = {'legacy_snapshot_blob': payload.pop('snapshot_blob')}
            if logical == 'markup_label' and old in editing:
                payload['editing_search_categories'] = editing[old]
            payload.update(history.payloads.get((logical, old), {}))
            _reject_managed_create_fields(_canonical_payload(payload))
            # This pure encoding check does not instantiate or contact a store.
            HostedDomainStore._hybrid_payload(None, logical, payload)
            planned.append({'entity': logical, 'parents': {k: v for k, v in ids.items() if k != _ID_FIELDS[logical][0]},
                            'data': payload, 'id': eid})
    return planned, history.assets


def _validated_asset(path, raw):
    if not re.fullmatch(r'/api/assets/[A-Za-z0-9_-]{1,160}\.(?:png|jpg|webp)', path):
        raise ValueError('HOSTED_PACKAGE_ASSET_PATH_INVALID')
    extension = '.' + path.rsplit('.', 1)[-1].lower()
    try:
        info = inspect_hosted_image_bytes(raw, extension, byte_limit=HOSTED_DEFAULT_IMAGE_BYTES)
    except UnsafeImage as exc:
        raise ValueError('HOSTED_PACKAGE_ASSET_UNSAFE') from exc
    return {'path': path, 'extension': info.extension,
            'sha256': hashlib.sha256(raw).hexdigest(), 'data': raw}


def _validated_assets(archive):
    assets = []
    for member in archive.infolist():
        name = member.filename
        if member.is_dir() or not name.startswith('assets/'):
            continue
        if member.file_size > HOSTED_DEFAULT_IMAGE_BYTES:
            raise ValueError('HOSTED_PACKAGE_ASSET_UNSAFE')
        assets.append(_validated_asset('/api/' + name, archive.read(name)))
    return assets


class _ImageSources(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.paths = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'img':
            self.paths.extend(value for key, value in attrs if key.lower() == 'src')

    handle_startendtag = handle_starttag


def _require_asset_closure(entries, available):
    for entry in entries:
        stack = [(entry['data'], '')]
        while stack:
            value, key = stack.pop()
            if isinstance(value, dict):
                # Source evidence is inert, original bytes retained for audit;
                # the separately rebased rendering projection is checked below.
                stack.extend((v, k) for k, v in value.items() if k != 'native_source')
            elif isinstance(value, list):
                stack.extend((item, key) for item in value)
            elif isinstance(value, str):
                if key == 'image_path' and value and value not in available:
                    raise ValueError('HOSTED_PACKAGE_ASSET_REFERENCE_MISSING')
                if key == 'workspace_json':
                    stack.append((json.loads(value), key))
                if key == 'content_html':
                    parser = _ImageSources()
                    parser.feed(value)
                    parser.close()
                    if any(path not in available for path in parser.paths):
                        raise ValueError('HOSTED_PACKAGE_ASSET_REFERENCE_MISSING')


def _validate_native_schema(con):
    # Inspect SQLite metadata only, before integrity_check or SELECT against any
    # uploaded product table. In particular, a recursive view named projects
    # must never execute. Exact current-schema DDL also prevents attacker CHECK
    # expressions/generated columns from executing during integrity_check.
    expected = _contract()[3]
    actual = {}
    for kind, name, sql in con.execute('SELECT type,name,sql FROM sqlite_master'):
        if kind == 'trigger':
            # Native generation-maintenance triggers are legitimate. Query-only
            # mode prevents all writes, so no archived trigger can execute.
            continue
        if kind not in {'table', 'index'} or (kind, name) not in expected:
            raise ValueError('HOSTED_PACKAGE_SCHEMA_OBJECT_INVALID')
        actual[kind, name] = sql
    if actual != expected:
        raise ValueError('HOSTED_PACKAGE_SCHEMA_DEFINITION_INVALID')


def prepare_native_archive(archive, manifest, pid, *, name_override=None, excluded=()):
    database = native_database_bytes(archive, manifest)
    assets = _validated_assets(archive)
    con = sqlite3.connect(':memory:')
    con.row_factory = sqlite3.Row
    try:
        if not hasattr(con, 'deserialize'):
            raise ValueError('HOSTED_LEGACY_IMPORT_DESERIALIZE_UNAVAILABLE')
        con.deserialize(database)
        deadline = time.monotonic() + 10.0
        remaining = 5_000
        def budget():
            nonlocal remaining
            remaining -= 1
            return remaining < 0 or time.monotonic() > deadline
        con.set_progress_handler(budget, 10_000)
        con.execute('PRAGMA trusted_schema=OFF')
        con.execute('PRAGMA query_only=ON')
        _validate_native_schema(con)
        if str(con.execute('PRAGMA integrity_check').fetchone()[0]).lower() != 'ok':
            raise ValueError('HOSTED_PACKAGE_DATABASE_INVALID')
        if con.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise ValueError('HOSTED_PACKAGE_DATABASE_INVALID')
        validate_native_connection(con, manifest)
        title_row = con.execute('SELECT title FROM projects').fetchone()
        title = str(name_override or title_row[0] or manifest.get('name') or 'Imported Project').strip()[:240] or 'Imported Project'
        from security.content_security import find_unsafe_project_html
        if find_unsafe_project_html(con) is not None:
            raise ValueError('HOSTED_PACKAGE_UNSAFE_HTML')
        planned, historical_assets = _plan_native_records(con, pid, excluded=excluded)
        by_path = {asset['path']: asset for asset in assets}
        for path, raw in historical_assets:
            asset = _validated_asset(path, raw)
            if path in by_path and by_path[path]['sha256'] != asset['sha256']:
                raise ValueError('HOSTED_PACKAGE_ASSET_PATH_CONFLICT')
            by_path[path] = asset
        assets = list(by_path.values())
        _require_asset_closure(planned, set(by_path))
        records, annotations = [], []
        for entry in planned:
            if entry['entity'] == 'annotation':
                annotations.append({'ids': {**entry['parents'], 'annotation_id': entry['id']},
                                    'data': {**entry['data'], 'id': entry['id']}})
            else:
                records.append(entry)
        return {'title': title, 'records': records, 'annotations': annotations, 'assets': assets}
    except sqlite3.Error as exc:
        raise ValueError('HOSTED_PACKAGE_DATABASE_INVALID') from exc
    finally:
        con.close()


def prepare_native_package(payload, pid, *, name_override=None):
    return prepare_project_package(payload, pid, name_override=name_override, native_only=True)


def prepare_project_package(payload, pid, *, name_override=None, native_only=False):
    """Provider-free preflight for ordinary and resumable native import callers.

    The result contains complete parent IDs, canonical product payloads and
    validated immutable asset bytes. No project, receipt or object is written.
    """
    from hosted.packages import _SKIP, _MAX_PACKAGE, _MAX_INLINE_ENTRY, _MAX_INLINE_EXPANDED, _MAX_INLINE_ASSET
    pid = require_project_id(pid)
    raw = bytes(payload)
    if len(raw) > _MAX_PACKAGE:
        raise ValueError('HOSTED_PACKAGE_TOO_LARGE')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            if len(members) > 25000:
                raise ValueError('HOSTED_PACKAGE_MEMBER_LIMIT')
            if any(name.startswith('/') or '..' in name.split('/') or '\\' in name
                   for name in archive.namelist()):
                raise ValueError('HOSTED_PACKAGE_PATH_INVALID')
            if any(item.file_size > _MAX_INLINE_ENTRY or (item.filename.startswith('assets/') and item.file_size > _MAX_INLINE_ASSET)
                   for item in members) or sum(item.file_size for item in members) > _MAX_INLINE_EXPANDED:
                raise ValueError('HOSTED_PACKAGE_EXPANDED_TOO_LARGE')
            manifest = json.loads(archive.read('manifest.json'))
            if isinstance(manifest, dict) and not native_only and manifest.get('format') == 'breadmaker-hosted-project' and manifest.get('format_version') == 3:
                return _prepare_hosted_archive(archive, manifest, pid, name_override=name_override, excluded=_SKIP)
            if not isinstance(manifest, dict) or manifest.get('package_format') != 'breadmaker-project' or manifest.get('package_version') != 2:
                raise ValueError('HOSTED_PACKAGE_UNSUPPORTED')
            return prepare_native_archive(archive, manifest, pid, name_override=name_override, excluded=_SKIP)
    except (KeyError, zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError('HOSTED_PACKAGE_INVALID') from exc


def _validate_rendered_payload(logical, data):
    from security.content_security import is_safe_inline_html, is_safe_cut_html, is_safe_project_asset_path
    stack = [data]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            if 'block_type' in value:
                kind = str(value['block_type'])
                fragment, path = str(value.get('content_html') or ''), str(value.get('image_path') or '')
                if kind == 'image':
                    if fragment or not is_safe_project_asset_path(path):
                        raise ValueError('HOSTED_PACKAGE_UNSAFE_HTML')
                elif kind not in {'paragraph', 'text'} or path or (fragment and not is_safe_inline_html(fragment, str(value.get('text_content') or ''))):
                    raise ValueError('HOSTED_PACKAGE_UNSAFE_HTML')
            elif value.get('content_html') and not is_safe_cut_html(str(value['content_html'])):
                raise ValueError('HOSTED_PACKAGE_UNSAFE_HTML')
            if value.get('image_path') and not is_safe_project_asset_path(str(value['image_path'])):
                raise ValueError('HOSTED_PACKAGE_UNSAFE_HTML')
            stack.extend(child for key, child in value.items() if key != 'native_source')
        elif isinstance(value, list):
            stack.extend(value)


def _prepare_hosted_archive(archive, manifest, pid, *, name_override=None, excluded=()):
    """Preserve v3 support for the resumable upload path with the same plan API."""
    from hosted.packages import _restore_safe, _portable_import_payload
    snapshot = json.loads(archive.read('project.json'))
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('entities'), dict):
        raise ValueError('HOSTED_PACKAGE_INVALID')
    entities = snapshot['entities']
    if len(archive.namelist()) != len(set(archive.namelist())):
        raise ValueError('HOSTED_PACKAGE_DUPLICATE_PATH')
    records, annotations, paths = [], [], set()
    ordered = sorted((name for name in entities if name in MODELS and name not in excluded),
                     key=lambda name: (len(MODELS[name].path_template.split('/')), name))
    for logical in ordered:
        model = MODELS[logical]
        fields = _fields(model.path_template)
        items = entities[logical]
        if logical not in _ID_FIELDS or not isinstance(items, list):
            raise ValueError('HOSTED_PACKAGE_ENTITY_UNSUPPORTED')
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get('ids'), dict) or not isinstance(item.get('data'), dict):
                raise ValueError('HOSTED_PACKAGE_INVALID')
            ids = {str(key): str(value) for key, value in item['ids'].items()}
            ids['project_id'] = pid
            path = render_model_path(model, **ids)
            if path in paths:
                raise ValueError('HOSTED_PACKAGE_ENTITY_COLLISION')
            paths.add(path)
            data = {key: _restore_safe(value) for key, value in item['data'].items()}
            data.pop('project_id', None)
            data = _portable_import_payload(logical, data, ids=ids, entity_id=ids[fields[-1]])
            _reject_managed_create_fields(_canonical_payload(data))
            HostedDomainStore._hybrid_payload(None, logical, data)
            _validate_rendered_payload(logical, data)
            if logical == 'annotation':
                annotations.append({'ids': ids, 'data': data})
            else:
                records.append({'entity': logical, 'parents': {key: value for key, value in ids.items() if key != fields[-1]},
                                'id': ids[fields[-1]], 'data': data})
            if len(records) + len(annotations) > _MAX_ROWS:
                raise ValueError('HOSTED_PACKAGE_ENTITY_LIMIT')
    assets = _validated_assets(archive)
    aliases = manifest.get('asset_paths')
    names = {'assets/' + asset['path'].rsplit('/', 1)[-1] for asset in assets}
    if aliases is not None and (not isinstance(aliases, dict) or set(aliases) != names):
        raise ValueError('HOSTED_PACKAGE_ASSET_ALIAS_MAP_INVALID')
    expanded, seen = [], set()
    for asset in assets:
        leaf = asset['path'].rsplit('/', 1)[-1]
        if leaf != asset['sha256'] + asset['extension']:
            raise ValueError('HOSTED_PACKAGE_ASSET_HASH_MISMATCH')
        paths = aliases['assets/' + leaf] if aliases is not None else [
            '/api/assets/img_' + asset['sha256'][:24] + asset['extension']]
        if not isinstance(paths, list) or not paths:
            raise ValueError('HOSTED_PACKAGE_ASSET_ALIAS_MAP_INVALID')
        for path in paths:
            if (not isinstance(path, str) or not re.fullmatch(r'/api/assets/[A-Za-z0-9_-]{1,160}\.(?:png|jpg|webp)', path)
                    or not path.endswith(asset['extension']) or path in seen):
                raise ValueError('HOSTED_PACKAGE_ASSET_ALIAS_MAP_INVALID')
            seen.add(path)
            if len(seen) > 25000:
                raise ValueError('HOSTED_PACKAGE_ASSET_ALIAS_LIMIT')
            expanded.append({**asset, 'path': path})
    assets = expanded
    _require_asset_closure(records, {asset['path'] for asset in assets})
    title = str(name_override or manifest.get('title') or 'Imported Project').strip()[:240] or 'Imported Project'
    return {'title': title, 'records': records, 'annotations': annotations, 'assets': assets}
