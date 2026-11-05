from __future__ import annotations

"""Readable views of normal hosted-v3 snapshots, without native conversion.

The unchanged hosted package remains the recovery source. A narrow reference
allowlist does not turn account, participant or member state into readable data.
"""

import hashlib
import json
import math
import re
import zipfile

from formats.readable_archive import write_snapshot_copies
from hosted.domain_store import _ID_FIELDS

_MAX_BYTES=256*1024*1024
_REFERENCE_ENTITIES={
    'document_tab':'document_tabs','chapter':'chapters','scene':'scenes',
    'manuscript_block':'manuscript_blocks','markup_label':'markup_labels',
    'annotation':'annotations','canvas':'canvases','canvas_node':'canvas_nodes',
    'canvas_edge':'canvas_edges','story_map':'story_maps','map_line':'map_lines',
    'map_item':'map_items','map_junction':'map_junctions','task':'tasks',
    'task_milestone':'task_milestones','cross_feature_reference':'references_map',
    'icon_marker':'icon_markers',
}


def _rows(entities,logical,pid):
    entries=entities.get(logical,[])
    if not isinstance(entries,list):raise ValueError('HOSTED_READABLE_RECORD_INVALID')
    rows=[];seen=set();identity=_ID_FIELDS[logical][0]
    for entry in entries:
        if not isinstance(entry,dict) or not isinstance(entry.get('ids'),dict) or not isinstance(entry.get('data'),dict):
            raise ValueError('HOSTED_READABLE_RECORD_INVALID')
        ids=entry['ids'];row=dict(entry['data']);eid=ids.get(identity)
        if ids.get('project_id')!=pid or not isinstance(eid,str) or not eid or eid in seen:
            raise ValueError('HOSTED_READABLE_IDENTITY_INVALID')
        for field,value in {**ids,'id':eid}.items():
            if field in row and row[field]!=value:raise ValueError('HOSTED_READABLE_IDENTITY_INVALID')
            row[field]=value
        seen.add(eid);rows.append(row)
    return rows


def _order(row):
    try:position=float(row.get('position') or 0)
    except (TypeError,ValueError):raise ValueError('HOSTED_READABLE_POSITION_INVALID') from None
    if not math.isfinite(position):raise ValueError('HOSTED_READABLE_POSITION_INVALID')
    return position,row['id']


def _documents(content):
    tabs=sorted(content['document_tabs'],key=_order)
    chapters=sorted(content['chapters'],key=_order)
    blocks=sorted(content['manuscript_blocks'],key=_order)
    tab_ids={tab['id'] for tab in tabs};by_chapter={ch['id']:ch for ch in chapters}
    if not tabs or any(ch.get('tab_id') not in tab_ids for ch in chapters):
        raise ValueError('HOSTED_READABLE_DOCUMENT_LINK_INVALID')
    for block in blocks:
        chapter=by_chapter.get(block.get('chapter_id'))
        if chapter is None or block.get('tab_id',chapter['tab_id'])!=chapter['tab_id']:
            raise ValueError('HOSTED_READABLE_DOCUMENT_LINK_INVALID')
    return tabs,chapters,blocks


def write_hosted_readable_archive(package,destination):
    """Render only a frozen normal-export package, retaining its original bytes."""
    if package.stat().st_size>_MAX_BYTES:raise ValueError('HOSTED_READABLE_SNAPSHOT_TOO_LARGE')
    with zipfile.ZipFile(package) as source:
        infos=source.infolist();names=[info.filename for info in infos]
        assets=[name for name in names if re.fullmatch(r'assets/[a-f0-9]{64}\.(?:png|jpg|webp)',name)]
        if len(names)!=len(set(names)) or len(names)>50000 or set(names)!={'manifest.json','project.json',*assets}:
            raise ValueError('HOSTED_READABLE_SNAPSHOT_INVALID')
        if sum(info.file_size for info in infos)>_MAX_BYTES or any(info.file_size>64*1024*1024 for info in infos):
            raise ValueError('HOSTED_READABLE_SNAPSHOT_TOO_LARGE')
        if source.getinfo('manifest.json').file_size>2*1024*1024:
            raise ValueError('HOSTED_READABLE_SNAPSHOT_TOO_LARGE')
        manifest=json.loads(source.read('manifest.json'));document=json.loads(source.read('project.json'))
        if not isinstance(manifest,dict) or manifest.get('format')!='breadmaker-hosted-project' or manifest.get('format_version')!=3 or manifest.get('authorization_portable') is not False:
            raise ValueError('HOSTED_READABLE_SNAPSHOT_UNSUPPORTED')
        if not isinstance(document,dict) or not isinstance(document.get('entities'),dict):
            raise ValueError('HOSTED_READABLE_SNAPSHOT_INVALID')
        for name in assets:
            if source.getinfo(name).file_size>12*1024*1024:raise ValueError('HOSTED_READABLE_SNAPSHOT_TOO_LARGE')
            if hashlib.sha256(source.read(name)).hexdigest()!=name.split('/')[1].split('.')[0]:
                raise ValueError('HOSTED_READABLE_ASSET_HASH_MISMATCH')
        content={table:_rows(document['entities'],logical,manifest.get('source_project_id')) for logical,table in _REFERENCE_ENTITIES.items()}
        tabs,chapters,blocks=_documents(content)
        return write_snapshot_copies(package,destination,source,str(manifest.get('title') or 'Untitled'),
            tabs,chapters,blocks,content,restore_in_web=True)
