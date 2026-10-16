from __future__ import annotations

"""M9B: fail-closed hosted-to-local Schema-42 portable export.

This is a one-way *export codec*, not a hosted SQLite repository. All state is
read from an already-authorized immutable hosted-v3 package; a temporary
in-memory SQLite database is built to satisfy the unchanged local v2 importer.
No hosted request ever opens or writes a local project directory.
"""

import base64
import hashlib
import io
import json
import re
import sqlite3
import zipfile
from pathlib import Path
from typing import Any, Mapping

from storage.schema import SCHEMA_SQL, SCHEMA_VERSION
from hosted.portability_history import (HistoryShapeError, prepare_review_rounds,
    checkpoint_v1, finalize_review_round_hashes)

_MAX_INPUT = 64 * 1024 * 1024
_MAX_EXPANDED = 192 * 1024 * 1024
_SHA = re.compile(r'^[a-f0-9]{64}$')
_ID = re.compile(r'^[a-f0-9]{32}$')
_LOCAL_TEXT_IDENTITIES = {
    'reviews': 'review_', 'review_participants': 'review_participant_',
    'review_snapshots': 'review_snapshot_',
    'questionnaires': 'questionnaire_', 'questionnaire_questions': 'questionnaire_question_',
    'review_questionnaires': 'review_questionnaire_',
}
# The schema mapping is the sole source of logical-entity -> local-table policy.
_MAPPING_PATH = Path(__file__).resolve().parents[2] / 'docs' / 'HOSTED_DATA_MODEL_V1.json'
_EXCLUDED = frozenset({
    'project', 'project_membership', 'hosted_model_metadata', 'recovery_state',
    'hosted_user_profile', 'identity_binding', 'project_asset',
    'hosted_revision_history', 'checkpoint_asset_reference',
    'backup_destination_configuration', 'member_bookmark', 'member_ui_state',
})
_MANAGED = frozenset({
    'generation', 'object_name', 'object_generation', 'object_sha256',
    'object_size', 'payload_object', 'api_path',
})
# Some non-FK columns refer to live IDs, rather than portable human-readable text.
_PLAIN_REFERENCES = {
    'source_tab_id': 'document_tabs', 'source_chapter_id': 'chapters',
    'current_source_chapter_id': 'chapters',
    'start_block_id': 'manuscript_blocks', 'end_block_id': 'manuscript_blocks',
    'source_marker_id': 'markup_labels',
    'source_scene_id': 'scenes',
}


class LocalPortabilityError(ValueError):
    """An export would lose content or produce an unimportable local package."""


def _byte_value(value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {'$bytes'}:
        encoded = value['$bytes']
        if not isinstance(encoded, str):
            raise LocalPortabilityError('HOSTED_PORTABLE_BYTES_INVALID')
        try:
            return base64.b64decode(encoded, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise LocalPortabilityError('HOSTED_PORTABLE_BYTES_INVALID') from exc
    if isinstance(value, (dict, list)):
        # JSON-shaped fields are explicitly encoded below; arbitrary new hosted
        # objects are not silently stringified into unrelated local columns.
        return value
    return value


def _scalar(value: Any, *, column: str) -> Any:
    value = _byte_value(value)
    if isinstance(value, (dict, list)):
        if column.endswith('_json') or column in {'workspace_json', 'value_json'}:
            return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        raise LocalPortabilityError(f'HOSTED_PORTABLE_UNSUPPORTED_FIELD:{column}')
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (str, int, float, bytes)) or value is None:
        return value
    raise LocalPortabilityError(f'HOSTED_PORTABLE_UNSUPPORTED_FIELD:{column}')


def _archive_members(source: bytes) -> tuple[Mapping[str, Any], Mapping[str, Any], dict[str, bytes]]:
    if len(source) > _MAX_INPUT or not zipfile.is_zipfile(io.BytesIO(source)):
        raise LocalPortabilityError('HOSTED_PORTABLE_SOURCE_INVALID')
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(names) != len(set(names)) or len(entries) > 50000:
            raise LocalPortabilityError('HOSTED_PORTABLE_SOURCE_INVALID')
        if set(names) - {'manifest.json', 'project.json'} - {n for n in names if re.fullmatch(r'assets/[a-f0-9]{64}\.(?:png|jpg|webp)', n)}:
            raise LocalPortabilityError('HOSTED_PORTABLE_SOURCE_INVALID')
        if not {'manifest.json', 'project.json'}.issubset(names):
            raise LocalPortabilityError('HOSTED_PORTABLE_SOURCE_INVALID')
        if archive.getinfo('manifest.json').file_size > 2*1024*1024:
            raise LocalPortabilityError('HOSTED_PORTABLE_SOURCE_INVALID')
        if sum(i.file_size for i in entries) > _MAX_EXPANDED:
            raise LocalPortabilityError('HOSTED_PORTABLE_SOURCE_TOO_LARGE')
        try:
            manifest = json.loads(archive.read('manifest.json'))
            document = json.loads(archive.read('project.json'))
        except (ValueError, UnicodeError, KeyError) as exc:
            raise LocalPortabilityError('HOSTED_PORTABLE_SOURCE_INVALID') from exc
        if not isinstance(manifest, dict) or not isinstance(document, dict) or not isinstance(document.get('entities'), dict):
            raise LocalPortabilityError('HOSTED_PORTABLE_SOURCE_INVALID')
        if manifest.get('format') != 'breadmaker-hosted-project' or manifest.get('format_version') != 3:
            raise LocalPortabilityError('HOSTED_PORTABLE_SOURCE_UNSUPPORTED')
        assets: dict[str, bytes] = {}
        for entry in entries:
            if not entry.filename.startswith('assets/'):
                continue
            data = archive.read(entry)
            digest = entry.filename.rsplit('/', 1)[-1].split('.')[0]
            if hashlib.sha256(data).hexdigest() != digest:
                raise LocalPortabilityError('HOSTED_PORTABLE_ASSET_HASH_MISMATCH')
            local_name = 'assets/img_' + digest[:24] + '.' + entry.filename.rsplit('.', 1)[-1]
            if local_name in assets:
                raise LocalPortabilityError('HOSTED_PORTABLE_ASSET_COLLISION')
            assets[local_name] = data
        return manifest, document, assets


def _mapping() -> dict[str, str]:
    values = json.loads(_MAPPING_PATH.read_text(encoding='utf-8'))['schema42_mappings']
    return {row['logical_entity']: row['source_table'] for row in values}


def _columns(con: sqlite3.Connection, table: str) -> dict[str, tuple[str, int]]:
    # Table is from the checked-in model mapping, never external input.
    return {str(r[1]): (str(r[2]).upper(), int(r[5])) for r in con.execute(f'PRAGMA table_info("{table}")')}


def _foreign(con: sqlite3.Connection, table: str) -> dict[str, str]:
    return {str(r[3]): str(r[2]) for r in con.execute(f'PRAGMA foreign_key_list("{table}")')}


# These links are *not* SQLite foreign keys in Schema 42. Converting only
# declared FKs can therefore produce an apparently valid but semantically
# detached Canvas/Map. All portable logical references must be rebased, too.
_TYPED_REFERENCES = {
    'chapter': 'chapters', 'scene': 'scenes',
    'canvas': 'canvases', 'story_map': 'story_maps',
    'map': 'story_maps', 'task': 'tasks',
    'manuscript_block': 'manuscript_blocks', 'block': 'manuscript_blocks',
    'markup_label': 'markup_labels',
}


def _rebase_typed_reference(ids: Mapping[tuple[str, str], int | str], kind: str,
                            value: Any, *, where: str) -> str:
    target = _TYPED_REFERENCES.get(str(kind))
    if target is None:
        raise LocalPortabilityError(f'HOSTED_PORTABLE_REFERENCE_TYPE_UNSUPPORTED:{where}')
    mapped = ids.get((target, str(value)))
    if mapped is None:
        raise LocalPortabilityError(f'HOSTED_PORTABLE_SOURCE_REFERENCE_INVALID:{where}')
    return str(mapped)


def _rebase_non_fk_semantics(table: str, values: dict[str, Any],
                              ids: Mapping[tuple[str, str], int | str]) -> None:
    if table == 'map_items':
        for edge in ('start', 'end'):
            kind = values.get(f'{edge}_anchor_type')
            column = f'{edge}_anchor_id'
            anchor = values.get(column)
            if kind in ('chapter', 'scene'):
                if anchor in (None, ''):
                    raise LocalPortabilityError(f'HOSTED_PORTABLE_ANCHOR_INCOMPLETE:{edge}')
                values[column] = _rebase_typed_reference(ids, kind, anchor, where=f'map_items.{column}')
            elif kind == 'manuscript_range':
                block_column = f'{edge}_block_id'
                if values.get(block_column) in (None, ''):
                    raise LocalPortabilityError(f'HOSTED_PORTABLE_ANCHOR_INCOMPLETE:{edge}')
                # The block FK has already been rebased; anchor_id is not used
                # by Schema 42 for range anchors. Keep it only when empty.
                if anchor not in (None, ''):
                    raise LocalPortabilityError(f'HOSTED_PORTABLE_RANGE_ANCHOR_UNSUPPORTED:{edge}')
            elif kind not in (None, '') or anchor not in (None, ''):
                raise LocalPortabilityError(f'HOSTED_PORTABLE_ANCHOR_TYPE_INVALID:{edge}')
        key = values.get('source_key')
        if isinstance(key, str):
            match = re.fullmatch(r'(chapter|scene):(.+)', key)
            if match:
                values['source_key'] = match.group(1) + ':' + _rebase_typed_reference(
                    ids, match.group(1), match.group(2), where='map_items.source_key')
    elif table == 'references_map':
        for edge in ('source', 'target'):
            kind = values.get(f'{edge}_type')
            value = values.get(f'{edge}_id')
            if kind in _TYPED_REFERENCES and value not in (None, ''):
                values[f'{edge}_id'] = _rebase_typed_reference(
                    ids, kind, value, where=f'references_map.{edge}_id')
    elif table == 'canvas_version_items':
        if any(key in values for key in ('kind', 'source_id', 'payload')):
            # Hosted versions freeze nodes+edges; local versions freeze
            # developmental chapter/scene placements. These are different
            # histories, NOT two representations of the same entity. Never
            # fabricate placements or throw away frozen geometry.
            raise LocalPortabilityError('HOSTED_PORTABLE_CANVAS_VERSION_SHAPE_UNSUPPORTED')
        kind = values.get('entity_type')
        value = values.get('entity_id')
        if kind not in {'chapter', 'scene'} or value is None:
            raise LocalPortabilityError('HOSTED_PORTABLE_CANVAS_VERSION_ENTITY_INVALID')
        values['entity_id'] = int(_rebase_typed_reference(
            ids, kind, value, where='canvas_version_items.entity_id'))
    elif table == 'review_snapshots' and 'snapshot' in values:
        # Hosted full-project snapshots need an explicit relational projection
        # (Review rounds, frozen blocks, markers, checkpoints and questions).
        # This must not be silently coerced to a string or dropped.
        raise LocalPortabilityError('HOSTED_PORTABLE_REVIEW_SNAPSHOT_PROJECTION_REQUIRED')
    elif table == 'checkpoints' and 'snapshot' in values:
        # Hosted checkpoint snapshots may be raw project JSON or legacy bytes.
        # Only a recognized local checkpoint byte format can be restored;
        # neither a JSON dump nor a random gzip value is that format.
        raise LocalPortabilityError('HOSTED_PORTABLE_CHECKPOINT_CODEC_REQUIRED')


def to_local_package(source: bytes, *, project_generation: int, created_at: str = '', updated_at: str = '') -> bytes:
    """Build a *local-importable* v2 package, or raise instead of dropping state.

    The upstream caller must fence the whole canonical export with Firestore's
    project generation, including GCS hybrid payload hydration. This pure codec
    has no database provider, storage credentials, user identities, or membership.
    """
    manifest, document, assets = _archive_members(bytes(source))
    pid = str(manifest.get('source_project_id') or '')
    if not _ID.fullmatch(pid) or int(project_generation) < 1:
        raise LocalPortabilityError('HOSTED_PORTABLE_PROJECT_IDENTITY_INVALID')
    name = str(manifest.get('title') or 'Untitled Book')[:240]
    when = str(updated_at or created_at or '1970-01-01T00:00:00Z')
    created = str(created_at or when)
    mapping = _mapping()
    try:
        records, review_round_ids = prepare_review_rounds(document['entities'])
    except HistoryShapeError as exc:
        raise LocalPortabilityError(str(exc)) from exc
    # Preflight incompatible frozen Canvas history before the relational build.
    # Parent Canvas rows may be incomplete for the local Schema 42 layout; an
    # incidental NOT NULL error must not hide this explicit loss-of-history
    # boundary. Unsupported snapshots are *never* silently discarded.
    for item in records.get('canvas_version_item', ()):
        if not isinstance(item, dict) or not isinstance(item.get('data'), dict):
            raise LocalPortabilityError('HOSTED_PORTABLE_SOURCE_INVALID')
        if any(key in item['data'] for key in ('kind', 'source_id', 'payload')):
            raise LocalPortabilityError('HOSTED_PORTABLE_CANVAS_VERSION_SHAPE_UNSUPPORTED')
    with sqlite3.connect(':memory:') as con:
        con.executescript(SCHEMA_SQL)
        con.execute('INSERT INTO storage_meta(key,value) VALUES (?,?)', ('schema_version', str(SCHEMA_VERSION)))
        con.execute('INSERT INTO projects(id,project_uid,title,created_at,updated_at,project_generation) VALUES (1,?,?,?,?,?)',
                    (pid, name, created, when, int(project_generation)))
        # First allocate every local primary key. No data is inserted until all
        # referenced IDs can be translated without guesses or collisions.
        ids: dict[tuple[str, str], int | str] = {}
        planned: list[tuple[str, dict, dict, dict]] = []
        next_id: dict[str, int] = {}
        for logical, items in records.items():
            if logical in _EXCLUDED:
                # Sensitive per-user/provider metadata is not portable. It is
                # deliberately absent from regular hosted-v3 packages.
                if items:
                    raise LocalPortabilityError(f'HOSTED_PORTABLE_NONPORTABLE_ENTITY:{logical}')
                continue
            table = mapping.get(logical)
            if table is None or not _columns(con, table):
                raise LocalPortabilityError(f'HOSTED_PORTABLE_UNMAPPED_ENTITY:{logical}')
            if not isinstance(items, list):
                raise LocalPortabilityError('HOSTED_PORTABLE_SOURCE_INVALID')
            cols = _columns(con, table)
            if 'id' not in cols:
                raise LocalPortabilityError(f'HOSTED_PORTABLE_COMPOSITE_KEY:{logical}')
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get('ids'), dict) or not isinstance(item.get('data'), dict):
                    raise LocalPortabilityError('HOSTED_PORTABLE_SOURCE_INVALID')
                identity, row = dict(item['ids']), dict(item['data'])
                # Hosted paths are generated with one entity-ID field that
                # varies by entity; the canonical row always carries `id`.
                old = str(row.get('id') or '')
                if not old or old not in {str(v) for k, v in identity.items() if k != 'project_id'}:
                    raise LocalPortabilityError(f'HOSTED_PORTABLE_ENTITY_ID_INVALID:{logical}')
                if (table, old) in ids:
                    raise LocalPortabilityError(f'HOSTED_PORTABLE_DUPLICATE_ID:{logical}')
                if cols['id'][0] == 'INTEGER':
                    next_id[table] = next_id.get(table, 0) + 1
                    assigned: int | str = next_id[table]
                else:
                    prefix = _LOCAL_TEXT_IDENTITIES.get(table)
                    if prefix and not re.fullmatch(re.escape(prefix) + r'[a-f0-9]{32}', old):
                        # Locally validated identities use 32 hex digits; hosted
                        # Firestore IDs generally use 24. This mapping is stable
                        # within the package and all FK references use it.
                        assigned = prefix + hashlib.sha256(f'{table}:{old}'.encode()).hexdigest()[:32]
                    else:
                        assigned = old
                ids[table, old] = assigned
                planned.append((table, identity, row, {'logical': logical, 'old': old, 'id': assigned}))
        if len(planned) > 100_000:
            raise LocalPortabilityError('HOSTED_PORTABLE_ENTITY_LIMIT')
        # For foreign keys the target must have been enumerated above. There is
        # no fallback to provider-specific IDs or to NULL for a non-null source.
        for table, identity, row, meta in planned:
            cols = _columns(con, table)
            foreign = _foreign(con, table)
            allowed = set(cols)
            values: dict[str, Any] = {'id': meta['id']}
            # Reject incompatible *shapes* before the generic unknown-field
            # guard, so callers can identify which explicit adapter is missing.
            if table == 'canvas_version_items' and any(k in row for k in ('kind', 'source_id', 'payload')):
                raise LocalPortabilityError('HOSTED_PORTABLE_CANVAS_VERSION_SHAPE_UNSUPPORTED')
            if table == 'review_snapshots' and 'snapshot' in row:
                raise LocalPortabilityError('HOSTED_PORTABLE_REVIEW_SNAPSHOT_PROJECTION_REQUIRED')
            if table == 'checkpoints' and 'snapshot' in row:
                try:
                    row = checkpoint_v1(row, ids, assets, created)
                except HistoryShapeError as exc:
                    raise LocalPortabilityError(str(exc)) from exc
            # Hosted document Tabs encode the primary Manuscript role using
            # `kind`; local Schema 42 uses `is_manuscript`. This role is not
            # merely decorative: local project invariants require exactly one.
            if table == 'document_tabs' and 'kind' in row:
                kind = row.get('kind')
                if kind not in ('manuscript', 'document', 'notes', 'secondary'):
                    raise LocalPortabilityError('HOSTED_PORTABLE_TAB_KIND_UNSUPPORTED')
                manuscript = 1 if kind == 'manuscript' else 0
                if 'is_manuscript' in row and int(row['is_manuscript']) != manuscript:
                    raise LocalPortabilityError('HOSTED_PORTABLE_TAB_ROLE_CONFLICT')
                row = {k: v for k, v in row.items() if k != 'kind'}
                row['is_manuscript'] = manuscript
            # Id aliases used by hosted document paths are transport metadata.
            id_field = next((k for k, v in identity.items() if k != 'project_id' and str(v) == meta['old']), '')
            for key, cell in {**identity, **row}.items():
                if key == 'id' or (key == id_field and key not in foreign) or key in _MANAGED or key.startswith('_bm_'):
                    continue
                # Hosted Firestore paths carry ancestors absent from the local
                # child table (e.g. project_id on canvas_nodes and canvas_id
                # on canvas_version_items). Only *path identity* aliases may
                # be skipped; unmatched data fields must still fail closed.
                if key in identity and key not in row and key not in allowed:
                    continue
                if key not in allowed:
                    raise LocalPortabilityError(f'HOSTED_PORTABLE_UNREPRESENTABLE_FIELD:{meta["logical"]}.{key}')
                values[key] = _scalar(cell, column=key)
            if 'project_id' in cols:
                values['project_id'] = 1
            # Hosted-v3 recovery archives intentionally omit the provider-managed
            # created_at/updated_at fields. Local Schema 42 requires these fields
            # on chapters, blocks, tabs, etc. For this *derived local copy only*,
            # use the source project's authoritative timestamps when an entity
            # timestamp is absent. Never replace an explicit entity timestamp.
            # This is a structural compatibility fallback, not a claim to have
            # recovered individual entity creation/update times (see M9B docs).
            if 'created_at' in cols and 'created_at' not in values:
                values['created_at'] = created
            if 'updated_at' in cols and 'updated_at' not in values:
                values['updated_at'] = when
            for col, target in foreign.items():
                if col not in values or values[col] is None:
                    continue
                if target == 'projects':
                    values[col] = 1
                    continue
                raw = str(values[col])
                translated = ids.get((target, raw))
                if translated is None:
                    raise LocalPortabilityError(f'HOSTED_PORTABLE_DANGLING_REFERENCE:{table}.{col}')
                values[col] = translated
            # Source-location IDs on snapshots/checkpoints are *not* SQL FKs.
            for col, target in _PLAIN_REFERENCES.items():
                if col not in values or values[col] in (None, '') or col in foreign:
                    continue
                if cols.get(col, ('', 0))[0] != 'INTEGER':
                    continue
                raw = str(values[col])
                if (target, raw) not in ids:
                    raise LocalPortabilityError(f'HOSTED_PORTABLE_SOURCE_REFERENCE_INVALID:{table}.{col}')
                values[col] = ids[target, raw]
            _rebase_non_fk_semantics(table, values, ids)
            # SQLite's loose affinity would otherwise accept an opaque hosted
            # identifier inside an INTEGER reference not declared as a foreign
            # key. Such an export *opens* but cannot represent the same links.
            for col, cell in list(values.items()):
                if cell is None:
                    continue
                affinity = cols[col][0]
                if affinity == 'INTEGER':
                    if isinstance(cell, int) or (isinstance(cell, float) and cell.is_integer()):
                        values[col] = int(cell)
                    elif isinstance(cell, str) and re.fullmatch(r'-?[0-9]+', cell):
                        values[col] = int(cell)
                    else:
                        raise LocalPortabilityError(f'HOSTED_PORTABLE_INTEGER_REFERENCE_INVALID:{table}.{col}')
                elif isinstance(cell, bytes) and affinity != 'BLOB':
                    raise LocalPortabilityError(f'HOSTED_PORTABLE_BINARY_FIELD_INVALID:{table}.{col}')
            fields = sorted(values)
            quoted = ','.join('"' + key + '"' for key in fields)
            markers = ','.join('?' for _ in fields)
            try:
                con.execute(f'INSERT INTO "{table}" ({quoted}) VALUES ({markers})', [values[k] for k in fields])
            except sqlite3.Error as exc:
                raise LocalPortabilityError(f'HOSTED_PORTABLE_SCHEMA_CONFLICT:{meta["logical"]}') from exc
        try:
            finalize_review_round_hashes(con, {str(ids['review_snapshots', sid]) for sid in review_round_ids})
        except HistoryShapeError as exc:
            raise LocalPortabilityError(str(exc)) from exc
        # Checkpoint snapshots may own image bytes even if their image block is
        # no longer live. Recreate their local immutable image reference rows,
        # never depending on the editor filesystem to supply those bytes.
        for checkpoint_id, blob in con.execute('SELECT id,snapshot_blob FROM checkpoints').fetchall():
            try:
                frozen = json.loads(__import__('zlib').decompress(blob))
            except (ValueError, __import__('zlib').error) as exc:
                raise LocalPortabilityError('HOSTED_PORTABLE_CHECKPOINT_CODEC_REQUIRED') from exc
            for block in frozen.get('blocks', []):
                if block.get('block_type') != 'image':
                    continue
                path = str(block.get('image_path') or '')
                if not path.startswith('/api/assets/'):
                    continue
                archive_path = 'assets/' + path.removeprefix('/api/assets/')
                if archive_path not in assets:
                    raise LocalPortabilityError('HOSTED_PORTABLE_CHECKPOINT_ASSET_MISSING')
                payload = assets[archive_path]
                digest = hashlib.sha256(payload).hexdigest()
                con.execute('INSERT OR IGNORE INTO checkpoint_assets(hash,payload,byte_size,created_at) VALUES (?,?,?,?)',
                            (digest,payload,len(payload),created))
                con.execute('INSERT INTO checkpoint_asset_refs(checkpoint_id,asset_path,asset_hash) VALUES (?,?,?)',
                            (checkpoint_id,path,digest))
        # The local importer refuses incomplete asset references. Check them
        # here as well, before producing a purportedly portable download.
        referenced_assets: set[str] = set()
        for table, column in [('manuscript_blocks', 'image_path'),
                              ('canvas_nodes', 'image_path'),
                              ('review_snapshot_blocks', 'image_path')]:
            for (asset_path,) in con.execute(f'SELECT "{column}" FROM "{table}" WHERE "{column}" <> \'\''):
                if str(asset_path).startswith('/api/assets/'):
                    referenced_assets.add('assets/' + str(asset_path).removeprefix('/api/assets/'))
        for (html,) in con.execute('SELECT content_html FROM cuts WHERE content_html <> \'\''):
            referenced_assets.update('assets/' + leaf for leaf in re.findall(
                r'/api/assets/([A-Za-z0-9._-]+)', str(html or '')))
        if not referenced_assets.issubset(assets):
            raise LocalPortabilityError('HOSTED_PORTABLE_ASSET_REFERENCE_MISSING')
        if con.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise LocalPortabilityError('HOSTED_PORTABLE_FOREIGN_KEYS_INVALID')
        if str(con.execute('PRAGMA quick_check').fetchone()[0]) != 'ok':
            raise LocalPortabilityError('HOSTED_PORTABLE_DATABASE_INVALID')
        db = con.serialize()
    if len(db) + sum(map(len, assets.values())) > _MAX_EXPANDED:
        raise LocalPortabilityError('HOSTED_PORTABLE_SOURCE_TOO_LARGE')
    hashes = {'project.sqlite3': hashlib.sha256(db).hexdigest()}
    hashes.update({k: hashlib.sha256(v).hexdigest() for k, v in assets.items()})
    portable_manifest = {
        'package_format': 'breadmaker-project', 'package_version': 2,
        'schema_version': SCHEMA_VERSION, 'project_id': pid,
        'name': name, 'created_at': created, 'updated_at': when,
        'project_generation': int(project_generation),
        'snapshot_source': 'hosted-canonical-generation',
        'exported_at': when, 'file_hashes': hashes,
    }
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr('manifest.json', json.dumps(portable_manifest, sort_keys=True, ensure_ascii=False))
        zf.writestr('project.sqlite3', db)
        for path, contents in sorted(assets.items()):
            zf.writestr(path, contents)
    payload = result.getvalue()
    if len(payload) > _MAX_INPUT:
        raise LocalPortabilityError('HOSTED_PORTABLE_OUTPUT_TOO_LARGE')
    return payload
