from __future__ import annotations

"""Working-session structural Undo/Redo journal."""

import json
import sqlite3
import zlib
from datetime import datetime, timezone
from pathlib import Path

from storage import context as storage_context
from storage import coordination as storage_coordination
from storage import mutations as storage_mutations
from storage import values as storage_values

_SESSION_UNDO_JOURNAL: dict[str, dict] = {}


_SESSION_UNDO_MAX_ENTRIES = 500


_SESSION_UNDO_MIN_RETAIN = 200


_SESSION_UNDO_TABLE_ORDER = (
    'document_tabs', 'chapters', 'scenes', 'bookmarks', 'markup_labels',
    'icon_markers', 'canvases', 'story_maps', 'map_lines', 'manuscript_blocks',
    'comment_threads', 'annotations', 'comment_messages', 'tasks', 'task_milestones', 'canvas_nodes', 'canvas_edges', 'canvas_versions', 'canvas_version_items',
    'map_items', 'map_junctions', 'references_map', 'reviews', 'review_participants', 'review_scope_chapters', 'review_scope_scenes', 'review_marker_labels', 'review_markers', 'review_snapshots', 'review_snapshot_checkpoints', 'review_snapshot_markers', 'review_snapshot_chapters', 'review_snapshot_scenes', 'review_snapshot_blocks', 'review_anchors', 'review_highlights', 'review_comments', 'review_comment_replies', 'review_suggestions', 'questionnaires', 'questionnaire_questions', 'review_questionnaires', 'review_snapshot_questions', 'review_question_answers', 'review_general_feedback', 'review_events', 'review_imports', 'cuts',
)


_SESSION_UNDO_VOLATILE_COLUMNS = {'updated_at'}


_SESSION_UNDO_SCOPES = {
    'canvas': frozenset({'canvas_nodes','canvas_edges'}),
    # Canvas creation may also seed Developmental versions/cards. This is still a
    # tiny domain-local snapshot compared with the whole manuscript/review project.
    'canvas-tree': frozenset({'canvases','canvas_nodes','canvas_edges','canvas_versions','canvas_version_items'}),
    'maps': frozenset({'map_lines','map_items','map_junctions','references_map'}),
    'maps-meta': frozenset({'story_maps'}),
}


_SESSION_UNDO_EXCLUDED_TABLES = frozenset({
    'projects','ui_state','checkpoints','checkpoint_assets','checkpoint_asset_refs','storage_meta',
})


def project_state_fingerprint(con:sqlite3.Connection,project_id:int)->str:
    """Stable fingerprint for Checkpoint preflight staleness detection."""
    tables=('document_tabs','chapters','scenes','bookmarks','markup_labels','manuscript_blocks','comment_threads','annotations','comment_messages')
    payload={}
    for table in tables:
        cols,pk=_session_undo_table_meta(con,table)
        if 'project_id' in cols:
            rows=[dict(r) for r in con.execute(f"SELECT * FROM {table} WHERE project_id=? ORDER BY {pk}",(project_id,))]
        else:
            rows=[]
        payload[table]=rows
    return storage_values.sha(json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False,default=str))


def _session_undo_pack(value: dict) -> bytes:
    raw=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str).encode("utf-8")
    return zlib.compress(raw,6)


def _session_undo_unpack(payload: bytes) -> dict:
    return json.loads(zlib.decompress(payload).decode("utf-8"))


def _session_undo_table_meta(con: sqlite3.Connection, table: str) -> tuple[list[str],str]:
    rows=con.execute(f"PRAGMA table_info({table})").fetchall()
    columns=[str(row[1]) for row in rows]
    pk=[str(row[1]) for row in rows if int(row[5] or 0)>0]
    if len(pk)!=1:
        raise RuntimeError(f"Session Undo requires one primary key for {table}")
    return columns,pk[0]


def _session_undo_scope_tables(scope:str='full') -> tuple[str,...]:
    name=str(scope or 'full').strip().lower()
    if name in {'','full'}:return tuple(_SESSION_UNDO_TABLE_ORDER)
    selected=_SESSION_UNDO_SCOPES.get(name)
    if selected is None:raise ValueError(f'Unsupported Session Undo scope: {name}')
    return tuple(table for table in _SESSION_UNDO_TABLE_ORDER if table in selected)


def _session_undo_snapshot(con: sqlite3.Connection, tables:tuple[str,...]|None=None, *, scope:str='full', scope_id:int|None=None) -> dict:
    """Capture the mutable rows owned by one structural action.

    Domain scopes may additionally bind to one Canvas/Map. That changes the cost from
    O(all Canvas/Map rows in the project) to O(the object being edited), which is
    critical for interactive Populate/drag operations in large books.
    """
    selected=tuple(tables or _SESSION_UNDO_TABLE_ORDER); payload={}; scope_name=str(scope or 'full').lower()
    for table in selected:
        if table not in _SESSION_UNDO_TABLE_ORDER:raise ValueError(f'Unsupported Session Undo table: {table}')
        columns,pk=_session_undo_table_meta(con,table)
        query=f"SELECT * FROM {table}";args=()
        if scope_id is not None and scope_name=='canvas':
            if table in {'canvas_nodes','canvas_edges'}:
                query+=f" WHERE canvas_id=?";args=(int(scope_id),)
        elif scope_id is not None and scope_name=='maps':
            mid=int(scope_id)
            if table=='map_lines':query+=" WHERE map_id=?";args=(mid,)
            elif table=='map_items':query+=" WHERE line_id IN (SELECT id FROM map_lines WHERE map_id=?)";args=(mid,)
            elif table=='map_junctions':query+=" WHERE map_id=?";args=(mid,)
            elif table=='references_map':
                query+=""" WHERE project_id=(SELECT project_id FROM story_maps WHERE id=?) AND (
                    (source_type='map' AND source_id=CAST(? AS TEXT)) OR (target_type='map' AND target_id=CAST(? AS TEXT)) OR
                    (source_type='map_line' AND source_id IN (SELECT CAST(id AS TEXT) FROM map_lines WHERE map_id=?)) OR
                    (target_type='map_line' AND target_id IN (SELECT CAST(id AS TEXT) FROM map_lines WHERE map_id=?)) OR
                    (source_type='map_item' AND source_id IN (SELECT CAST(i.id AS TEXT) FROM map_items i JOIN map_lines l ON l.id=i.line_id WHERE l.map_id=?)) OR
                    (target_type='map_item' AND target_id IN (SELECT CAST(i.id AS TEXT) FROM map_items i JOIN map_lines l ON l.id=i.line_id WHERE l.map_id=?))
                )""";args=(mid,mid,mid,mid,mid,mid,mid)
        query+=f" ORDER BY {pk}"
        rows=[dict(row) for row in con.execute(query,args)]
        payload[table]={"pk":pk,"columns":columns,"rows":rows}
    return {"version":1,"tables":payload}


def _session_undo_logical_row(row: dict|None) -> dict|None:
    if row is None: return None
    return {k:v for k,v in row.items() if k not in _SESSION_UNDO_VOLATILE_COLUMNS}


def _session_undo_delta(before: dict, after: dict) -> dict:
    changes={}
    before_tables=before.get('tables') or {};after_tables=after.get('tables') or {}
    if set(before_tables)!=set(after_tables):raise RuntimeError('Undo snapshot scope changed during action')
    for table in _SESSION_UNDO_TABLE_ORDER:
        if table not in before_tables:continue
        bspec=before_tables[table]; aspec=after_tables[table]
        if bspec["pk"]!=aspec["pk"]: raise RuntimeError(f"Undo schema changed for {table}")
        pk=bspec["pk"]
        brows={str(row[pk]):row for row in bspec["rows"]}
        arows={str(row[pk]):row for row in aspec["rows"]}
        rows=[]
        for key in sorted(set(brows)|set(arows)):
            b=brows.get(key); a=arows.get(key)
            if _session_undo_logical_row(b)==_session_undo_logical_row(a):
                continue
            rows.append({"pk":(b or a)[pk],"before":b,"after":a})
        if rows:
            changes[table]={"pk":pk,"columns":bspec["columns"],"rows":rows}
    return {"version":1,"changes":changes}


def _session_undo_row(con:sqlite3.Connection,table:str,pk_col:str,pk):
    row=con.execute(f"SELECT * FROM {table} WHERE {pk_col}=?",(pk,)).fetchone()
    return dict(row) if row else None


def _session_undo_parent_order(table:str, rows:list[dict], *, reverse:bool=False) -> list[dict]:
    if table not in {'document_tabs','canvases'} or len(rows)<2:
        return list(reversed(rows)) if reverse else rows
    pending=[dict(row) for row in rows]; ids={row['id'] for row in pending}; done=set(); ordered=[]
    while pending:
        ready=[row for row in pending if row.get('parent_id') is None or row.get('parent_id') not in ids or row.get('parent_id') in done]
        if not ready:
            # A cycle should never be manufacturable through the normal APIs; leave
            # SQLite to reject it rather than silently guessing an order.
            ready=[pending[0]]
        ready.sort(key=lambda row:(row.get('position',0),row.get('id')))
        for row in ready:
            ordered.append(row); done.add(row.get('id')); pending.remove(row)
    return list(reversed(ordered)) if reverse else ordered


def _session_undo_insert(con:sqlite3.Connection,table:str,row:dict) -> None:
    columns=list(row)
    marks=','.join('?' for _ in columns)
    con.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES({marks})",tuple(row[c] for c in columns))


def _session_undo_update(con:sqlite3.Connection,table:str,pk_col:str,row:dict) -> None:
    columns=[c for c in row if c!=pk_col]
    con.execute(f"UPDATE {table} SET {','.join(f'{c}=?' for c in columns)} WHERE {pk_col}=?",tuple(row[c] for c in columns)+(row[pk_col],))


def _session_undo_apply_delta(con:sqlite3.Connection,delta:dict,direction:str) -> dict:
    if direction not in {'undo','redo'}: raise ValueError('Undo direction must be undo or redo')
    if int(delta.get('version',0))!=1: raise ValueError('Unsupported session Undo format')
    from_key='after' if direction=='undo' else 'before'
    to_key='before' if direction=='undo' else 'after'
    changes=delta.get('changes') or {}

    # Preflight only rows this action actually owns. Unrelated project state is not
    # allowed to veto a Chapter/Cut/Canvas/Map Undo. updated_at is intentionally
    # ignored because background saves may legitimately refresh it.
    for table,spec in changes.items():
        pk_col=spec['pk']
        for change in spec['rows']:
            current=_session_undo_row(con,table,pk_col,change['pk'])
            expected=change.get(from_key)
            if _session_undo_logical_row(current)!=_session_undo_logical_row(expected):
                raise RuntimeError(f"Undo conflict in {table}: the affected item changed after this action")

    # 1) Recreate target-side parents, 2) move/update dependants, 3) remove rows
    # that only exist on the source side. This order avoids ON DELETE cascades from
    # destroying children that are themselves part of the operation delta.
    for table in _SESSION_UNDO_TABLE_ORDER:
        spec=changes.get(table)
        if not spec: continue
        inserts=[c[to_key] for c in spec['rows'] if c.get(from_key) is None and c.get(to_key) is not None]
        for row in _session_undo_parent_order(table,inserts): _session_undo_insert(con,table,row)
    for table in _SESSION_UNDO_TABLE_ORDER:
        spec=changes.get(table)
        if not spec: continue
        for change in spec['rows']:
            if change.get(from_key) is not None and change.get(to_key) is not None:
                _session_undo_update(con,table,spec['pk'],change[to_key])
    for table in reversed(_SESSION_UNDO_TABLE_ORDER):
        spec=changes.get(table)
        if not spec: continue
        deletes=[c[from_key] for c in spec['rows'] if c.get(from_key) is not None and c.get(to_key) is None]
        for row in _session_undo_parent_order(table,deletes,reverse=True):
            con.execute(f"DELETE FROM {table} WHERE {spec['pk']}=?",(row[spec['pk']],))

    # Verify exactly the rows we changed after SQLite has enforced all FK actions.
    for table,spec in changes.items():
        for change in spec['rows']:
            current=_session_undo_row(con,table,spec['pk'],change['pk'])
            if _session_undo_logical_row(current)!=_session_undo_logical_row(change.get(to_key)):
                raise RuntimeError(f"Undo verification failed in {table}")
    return {"tables":len(changes),"rows":sum(len(spec['rows']) for spec in changes.values())}


def _session_undo_cleanup() -> None:
    # Session tokens live exactly as long as this Breadmaker process (or until the
    # bounded journal must evict its oldest entries). A wall-clock TTL caused the
    # browser to display an Undo entry whose server token had silently expired.
    entries=sorted(_SESSION_UNDO_JOURNAL.items(),key=lambda item:item[1].get('created_ts',0))
    while len(entries)>_SESSION_UNDO_MAX_ENTRIES and len(entries)>_SESSION_UNDO_MIN_RETAIN:
        token,_=entries.pop(0); _SESSION_UNDO_JOURNAL.pop(token,None)


def clear_session_undo_journal(project_root:Path|None=None, *, all_projects:bool=False) -> None:
    """Invalidate structural Undo tokens without crossing project sessions.

    Project activation is process-global UI state, but request-scoped project sessions
    may coexist. Activating Project B must therefore never erase Project A's server
    Undo journal. Full workspace erasure may still explicitly clear every token.
    """
    with storage_coordination._LOCK:
        if all_projects:
            _SESSION_UNDO_JOURNAL.clear();return
        target=str(Path(project_root or storage_context.current_project_root()).resolve()) if (project_root or storage_context.current_project_root()) is not None else ''
        if not target:return
        doomed=[token for token,entry in _SESSION_UNDO_JOURNAL.items() if str(entry.get('project_root') or '')==target]
        for token in doomed:_SESSION_UNDO_JOURNAL.pop(token,None)


def session_undo_schema_coverage(con: sqlite3.Connection) -> dict:
    tables={str(row[0]) for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    managed=set(_SESSION_UNDO_TABLE_ORDER)
    excluded=set(_SESSION_UNDO_EXCLUDED_TABLES)
    return {
        'managed':sorted(managed),
        'excluded':sorted(excluded),
        'unclassified':sorted(tables-managed-excluded),
        'missing':sorted((managed|excluded)-tables),
    }


def begin_session_undo(project_id:int,label:str='Working action',scope:str='full',scope_id:int|None=None) -> dict:
    scope_tables=_session_undo_scope_tables(scope)
    with storage_mutations.tx(operation='session_undo_snapshot',track_generation=False) as con:
        snapshot=_session_undo_snapshot(con,scope_tables,scope=scope,scope_id=scope_id)
    token=storage_values.uid('u_'); created=datetime.now(timezone.utc).timestamp()
    with storage_coordination._LOCK:
        _session_undo_cleanup()
        _SESSION_UNDO_JOURNAL[token]={
            "project_root":str(storage_context.current_project_root() or ''),"project_id":int(project_id),"label":label[:120],
            "stage":"open","side":"before","payload":_session_undo_pack(snapshot),"created_ts":created,
            "scope":str(scope or 'full').lower(),"scope_tables":scope_tables,"scope_id":int(scope_id) if scope_id is not None else None,
        }
    return {"token":token,"scope":str(scope or 'full').lower(),"scope_id":int(scope_id) if scope_id is not None else None}


def finish_session_undo(token:str) -> dict:
    with storage_coordination._LOCK:
        entry=_SESSION_UNDO_JOURNAL.get(token)
        if not entry or entry.get('stage')!='open': raise ValueError('Session Undo action is no longer available')
        if entry.get('project_root')!=str(storage_context.current_project_root() or ''): raise ValueError('Session Undo belongs to another project')
        before=_session_undo_unpack(entry['payload']);scope_tables=tuple(entry.get('scope_tables') or before.get('tables',{}).keys());scope=entry.get('scope') or 'full';scope_id=entry.get('scope_id')
    with storage_mutations.tx(operation='session_undo_snapshot',track_generation=False) as con:
        after=_session_undo_snapshot(con,scope_tables,scope=scope,scope_id=scope_id)
    delta=_session_undo_delta(before,after); changed=sum(len(spec['rows']) for spec in delta['changes'].values())
    with storage_coordination._LOCK:
        entry=_SESSION_UNDO_JOURNAL.get(token)
        if not entry or entry.get('stage')!='open': raise ValueError('Session Undo action is no longer available')
        entry.update({"stage":"complete","side":"after","payload":_session_undo_pack(delta)})
    return {"token":token,"changed":changed,"tables":len(delta['changes'])}


def rollback_session_undo(token:str) -> dict:
    with storage_coordination._LOCK:
        entry=_SESSION_UNDO_JOURNAL.get(token)
        if not entry or entry.get('stage')!='open': raise ValueError('Session Undo action is no longer available')
        if entry.get('project_root')!=str(storage_context.current_project_root() or ''): raise ValueError('Session Undo belongs to another project')
        before=_session_undo_unpack(entry['payload']);scope_tables=tuple(entry.get('scope_tables') or before.get('tables',{}).keys());scope=entry.get('scope') or 'full';scope_id=entry.get('scope_id')
    with storage_mutations.tx(operation='session_undo_snapshot',track_generation=False) as con:
        current=_session_undo_snapshot(con,scope_tables,scope=scope,scope_id=scope_id)
        delta=_session_undo_delta(before,current)
        result=_session_undo_apply_delta(con,delta,'undo')
    with storage_coordination._LOCK: _SESSION_UNDO_JOURNAL.pop(token,None)
    return {"ok":True,**result}


def apply_session_undo(token:str,direction:str) -> dict:
    with storage_coordination._LOCK:
        entry=_SESSION_UNDO_JOURNAL.get(token)
        if not entry or entry.get('stage')!='complete': raise ValueError('Session Undo action is no longer available')
        if entry.get('project_root')!=str(storage_context.current_project_root() or ''): raise ValueError('Session Undo belongs to another project')
        required='after' if direction=='undo' else 'before'
        if entry.get('side')!=required: raise ValueError(f"Session Undo is already on the {entry.get('side')} side")
        delta=_session_undo_unpack(entry['payload'])
    with storage_mutations.tx() as con:
        result=_session_undo_apply_delta(con,delta,direction)
    with storage_coordination._LOCK:
        entry=_SESSION_UNDO_JOURNAL.get(token)
        if entry: entry['side']='before' if direction=='undo' else 'after'
    return {"ok":True,"direction":direction,**result}


def session_undo_count(project_id:int|None=None) -> int:
    with storage_coordination._LOCK:
        _session_undo_cleanup()
        root=str(storage_context.current_project_root() or '')
        return sum(1 for entry in _SESSION_UNDO_JOURNAL.values()
                   if entry.get('stage')=='complete' and entry.get('project_root')==root
                   and (project_id is None or int(entry.get('project_id') or 0)==int(project_id)))
