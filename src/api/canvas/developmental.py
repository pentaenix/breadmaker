from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.dependencies import feature_call
from security import content_security
import json
from storage import database as storage_database
from storage import invariants as storage_invariants
from storage import mutations as storage_mutations
from storage import values as storage_values
from api.canvas.contracts import CanvasCreate, CanvasNodeBulkDelete, CanvasNodePositionBatch, CanvasPopulateRequest, CanvasUpdate, CanvasVersionCreate, CanvasVersionUpdate, DevelopmentalApply, DevelopmentalLayout, DevelopmentalPromote, EdgeCreate, EdgeUpdate, NodeCreate, NodeUpdate
from api.shared_contracts import Reorder
from backend.content import (
    CONTENT_CARD_SHAPES,
    DEVELOPMENTAL_FREE_Y,
    DEVELOPMENTAL_LANE_START_X,
    FIGURE_NODE_SHAPES,
    VALID_CANVAS_TYPES,
    VALID_NODE_SHAPES,
    VALID_NODE_TYPES,
    normalize_color,
)
from backend.data_access import (
    _delete_polymorphic_references,
    allrows,
    one,
    project,
)
from backend.environment import STORY_TEMPLATES_CONFIG
from backend.history_runtime import commit

def _developmental_scope_nodes(con,cv:dict):
    scope=(cv.get('developmental_scope') or 'chapters').lower()
    node_type='scene' if scope=='scenes' else 'chapter'
    return allrows(con,"SELECT * FROM canvas_nodes WHERE canvas_id=? AND node_type=? ORDER BY CASE WHEN lane_position IS NULL THEN 1 ELSE 0 END,lane_position,x,y,id",(cv['id'],node_type))


def _developmental_entity_key(scope:str,row:dict):
    return int(row['scene_id']) if scope=='scenes' else int(row['chapter_id'])


def _developmental_workspace_load(raw):
    try:
        value=json.loads(raw or '{}') if isinstance(raw,(str,bytes,bytearray)) else (raw or {})
    except Exception:
        value={}
    return value if isinstance(value,dict) else {}


def _developmental_workspace_snapshot_tx(con,cv:dict):
    """Capture one complete Developmental workspace below Structure Order.

    Manuscript-backed Chapter/Scene prose remains live, but every free Canvas object
    and connector belongs to the named Structure Version: content, media, geometry,
    styling, participation, and connections are restored together when switching.
    """
    cid=int(cv['id']); pid=int(cv['project_id'])
    nodes=allrows(con,"""SELECT id,node_type,shape,icon,x,y,lane_position,w,h,title,body,display_mode,text_align,color,text_color,figure_fill_color,figure_stroke_color,figure_border_width,figure_layer,figure_line_x1,figure_line_y1,figure_line_x2,figure_line_y2,label_id,chapter_id,scene_id,image_path,icon_marker_id
        FROM canvas_nodes WHERE canvas_id=? AND node_type NOT IN ('chapter','scene') AND COALESCE(developmental_hidden,0)=0 ORDER BY id""",(cid,))
    node_items=[]
    for n in nodes:
        node_items.append({
            'id':int(n['id']),'node_type':n.get('node_type') or 'free','shape':n.get('shape') or 'rect','icon':n.get('icon') or '',
            'x':float(n.get('x') or 0),'y':float(n.get('y') or 0),'lane_position':int(n['lane_position']) if n.get('lane_position') is not None else None,
            'w':float(n.get('w') or 220),'h':float(n.get('h') or 125),'title':n.get('title') or '','body':n.get('body') or '',
            'display_mode':n.get('display_mode') if n.get('display_mode') in {'full','header','body'} else 'full',
            'text_align':n.get('text_align') if n.get('text_align') in {'left','center','right'} else 'left',
            'color':n.get('color') or '','text_color':n.get('text_color') or '',
            'figure_fill_color':n.get('figure_fill_color') or '','figure_stroke_color':n.get('figure_stroke_color') or '',
            'figure_border_width':float(n.get('figure_border_width') or 1.0),'figure_layer':n.get('figure_layer') if n.get('figure_layer') in {'back','front'} else 'back',
            'figure_line_x1':float(n.get('figure_line_x1') if n.get('figure_line_x1') is not None else .02),'figure_line_y1':float(n.get('figure_line_y1') if n.get('figure_line_y1') is not None else .5),
            'figure_line_x2':float(n.get('figure_line_x2') if n.get('figure_line_x2') is not None else .98),'figure_line_y2':float(n.get('figure_line_y2') if n.get('figure_line_y2') is not None else .5),
            'label_id':int(n['label_id']) if n.get('label_id') is not None else None,
            'chapter_id':int(n['chapter_id']) if n.get('chapter_id') is not None else None,
            'scene_id':int(n['scene_id']) if n.get('scene_id') is not None else None,
            'image_path':n.get('image_path') or '',
            'icon_marker_id':int(n['icon_marker_id']) if n.get('icon_marker_id') is not None else None,
        })
    edges=[]
    for e in allrows(con,"""SELECT id,from_node_id,to_node_id,from_port,to_port,label,color,marker_id,line_style,end_style,line_width,route_style
        FROM canvas_edges WHERE canvas_id=? AND COALESCE(developmental_hidden,0)=0 ORDER BY id""",(cid,)):
        edges.append({
            'id':int(e['id']),'from_node_id':int(e['from_node_id']),'to_node_id':int(e['to_node_id']),
            'from_port':e.get('from_port') or 'right','to_port':e.get('to_port') or 'left','label':e.get('label') or '',
            'color':e.get('color') or '','marker_id':int(e['marker_id']) if e.get('marker_id') is not None else None,
            'line_style':e.get('line_style') or 'solid','end_style':e.get('end_style') or 'arrow',
            'line_width':float(e.get('line_width') or 1.5),'route_style':e.get('route_style') or 'curved',
        })
    styles={}
    scope=(cv.get('developmental_scope') or 'chapters').lower(); entity_type='scene' if scope=='scenes' else 'chapter'; key='scene_id' if scope=='scenes' else 'chapter_id'
    for n in _developmental_scope_nodes(con,cv):
        eid=n.get(key)
        if eid is None: continue
        styles[f'{entity_type}:{int(eid)}']={'color':n.get('color') or '','label_id':int(n['label_id']) if n.get('label_id') is not None else None}
    viewport={}
    row=one(con,"SELECT value_json FROM ui_state WHERE project_id=? AND key=?",(pid,f'canvas-viewport-{cid}'))
    if row:
        raw=_developmental_workspace_load(row.get('value_json'))
        for k in ('zoom','left','top','laneLeft'):
            if k in raw and isinstance(raw[k],(int,float)): viewport[k]=raw[k]
    return {'schema':3,'nodes':node_items,'edges':edges,'structural_styles':styles,'viewport':viewport}


def _scrub_developmental_workspace_refs(con,canvas_id:int,*,node_ids=(),edge_ids=()):
    node_ids={int(x) for x in node_ids}; edge_ids={int(x) for x in edge_ids}
    if not node_ids and not edge_ids:return
    for version in allrows(con,"SELECT id,workspace_json FROM canvas_versions WHERE canvas_id=?",(canvas_id,)):
        workspace=_developmental_workspace_load(version.get('workspace_json'))
        if not workspace: continue
        changed=False
        if node_ids and isinstance(workspace.get('nodes'),list):
            before=len(workspace['nodes']);workspace['nodes']=[n for n in workspace['nodes'] if int(n.get('id') or -1) not in node_ids];changed|=len(workspace['nodes'])!=before
        if edge_ids and isinstance(workspace.get('edges'),list):
            before=len(workspace['edges']);workspace['edges']=[edge for edge in workspace['edges'] if int((edge.get('id') if isinstance(edge,dict) else edge) or -1) not in edge_ids];changed|=len(workspace['edges'])!=before
        if changed: con.execute("UPDATE canvas_versions SET workspace_json=?,updated_at=? WHERE id=?",(json.dumps(workspace,ensure_ascii=False,separators=(',',':')),storage_values.now(),version['id']))


def _developmental_version_snapshot_tx(con,cv:dict,version_id:int,*,retain_missing_from:int|None=None):
    """Write one named Structure Version from the live developmental workspace."""
    scope=(cv.get('developmental_scope') or 'chapters').lower(); entity_type='scene' if scope=='scenes' else 'chapter'
    nodes=_developmental_scope_nodes(con,cv); t=storage_values.now()
    missing=[]
    if retain_missing_from:
        current_ids={_developmental_entity_key(scope,n) for n in nodes if (n.get('scene_id') if scope=='scenes' else n.get('chapter_id'))}
        missing=[r for r in allrows(con,"SELECT * FROM canvas_version_items WHERE version_id=? AND entity_type=? ORDER BY id",(retain_missing_from,entity_type)) if int(r['entity_id']) not in current_ids]
    con.execute("DELETE FROM canvas_version_items WHERE version_id=?",(version_id,))
    for n in nodes:
        eid=_developmental_entity_key(scope,n)
        lane=n.get('lane_position'); placed=lane is not None
        con.execute("INSERT INTO canvas_version_items(version_id,entity_type,entity_id,title,x,y,lane_position,placed,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(version_id,entity_type,eid,n.get('title') or '',float(n.get('x') or 0),float(n.get('y') or 0),int(lane) if placed else None,1 if placed else 0,t))
    for r in missing:
        con.execute("INSERT INTO canvas_version_items(version_id,entity_type,entity_id,title,x,y,lane_position,placed,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(version_id,entity_type,int(r['entity_id']),r.get('title') or '',float(r.get('x') or 0),float(r.get('y') or DEVELOPMENTAL_FREE_Y),int(r['lane_position']) if r.get('lane_position') is not None else None,1 if int(r.get('placed') or 0) else 0,t))
    workspace=_developmental_workspace_snapshot_tx(con,cv)
    con.execute("UPDATE canvas_versions SET workspace_json=?,updated_at=? WHERE id=?",(json.dumps(workspace,ensure_ascii=False,separators=(',',':')),t,version_id))


def _ensure_developmental_version_tx(con,cv:dict):
    existing=one(con,"SELECT * FROM canvas_versions WHERE canvas_id=? ORDER BY position,id LIMIT 1",(cv['id'],))
    if existing: return existing
    t=storage_values.now(); vid=con.execute("INSERT INTO canvas_versions(canvas_id,position,name,created_at,updated_at) VALUES(?,?,?,?,?)",(cv['id'],0,'Version 1',t,t)).lastrowid
    _developmental_version_snapshot_tx(con,cv,vid)
    return one(con,"SELECT * FROM canvas_versions WHERE id=?",(vid,))


def _developmental_free_slot(existing_rows:list[dict]):
    return sum(1 for r in existing_rows if r.get('lane_position') is None)


def sync_developmental_canvases(pid:int):
    """Reconcile live manuscript identities into Developmental Canvases read-only."""
    with storage_mutations.tx() as con:
        _sync_developmental_canvases_tx(con,pid)

def _sync_developmental_canvases_tx(con,pid:int):
    """Transaction-aware Developmental reconciliation used by atomic imports too.

    Existing structural Cards keep their proposal positions. New manuscript entities
    discovered after versioning begins land on the free desk as Unplaced rather than
    being guessed into an old outline. Deleted identities disappear from live nodes;
    named versions retain them as historical missing ghosts.
    """
    main=one(con,"SELECT id FROM document_tabs WHERE project_id=? AND is_manuscript=1 ORDER BY position,id LIMIT 1",(pid,))
    if not main: return
    main_id=int(main['id'])
    chapters=allrows(con,"SELECT c.* FROM chapters c WHERE c.project_id=? AND c.tab_id=? AND c.is_implicit=0 ORDER BY c.position,c.id",(pid,main_id))
    scenes=allrows(con,"""SELECT s.*,(
            SELECT b.chapter_id FROM manuscript_blocks b
            JOIN chapters c ON c.id=b.chapter_id
            WHERE b.scene_id=s.id AND b.tab_id=? AND c.tab_id=?
            ORDER BY c.position,b.position,b.id LIMIT 1
        ) chapter_id
        FROM scenes s
        WHERE s.project_id=? AND s.tab_id=?
          AND EXISTS(SELECT 1 FROM manuscript_blocks bx WHERE bx.scene_id=s.id AND bx.tab_id=?)
        ORDER BY s.position,s.id""",(main_id,main_id,pid,main_id,main_id))
    cvs=allrows(con,"SELECT * FROM canvases WHERE project_id=? AND canvas_type='developmental'",(pid,)); t=storage_values.now()
    valid_ch={int(c['id']) for c in chapters}; valid_sc={int(s['id']) for s in scenes}
    for cv in cvs:
        scope=(cv.get('developmental_scope') or 'chapters').lower(); wanted_type='scene' if scope=='scenes' else 'chapter'
        con.execute("DELETE FROM canvas_nodes WHERE canvas_id=? AND node_type IN ('chapter','scene') AND node_type<>?",(cv['id'],wanted_type))
        rows=allrows(con,"SELECT * FROM canvas_nodes WHERE canvas_id=? AND node_type=?",(cv['id'],wanted_type))
        for r in rows:
            eid=r.get('scene_id') if scope=='scenes' else r.get('chapter_id')
            if not eid or int(eid) not in (valid_sc if scope=='scenes' else valid_ch):
                con.execute("DELETE FROM canvas_nodes WHERE id=?",(r['id'],))
        rows=allrows(con,"SELECT * FROM canvas_nodes WHERE canvas_id=? AND node_type=?",(cv['id'],wanted_type))
        existing={(int(r['scene_id']) if scope=='scenes' else int(r['chapter_id'])):r for r in rows if (r.get('scene_id') if scope=='scenes' else r.get('chapter_id'))}
        has_versions=bool(one(con,"SELECT id FROM canvas_versions WHERE canvas_id=? LIMIT 1",(cv['id'],)))
        free_slot=_developmental_free_slot(rows)
        source=scenes if scope=='scenes' else chapters
        for item in source:
            eid=int(item['id'])
            if eid in existing:
                current=existing[eid]
                if scope=='scenes':
                    desired_chapter=item.get('chapter_id')
                    if (str(current.get('title') or '')!=str(item.get('title') or '')
                        or (current.get('chapter_id') if current.get('chapter_id') is not None else None)!=(desired_chapter if desired_chapter is not None else None)
                        or abs(float(current.get('w') or 0)-143)>1e-9 or abs(float(current.get('h') or 0)-52)>1e-9):
                        con.execute("UPDATE canvas_nodes SET title=?,chapter_id=?,w=143,h=52,updated_at=? WHERE id=?",(item['title'],desired_chapter,t,current['id']))
                else:
                    if (str(current.get('title') or '')!=str(item.get('title') or '')
                        or abs(float(current.get('w') or 0)-143)>1e-9 or abs(float(current.get('h') or 0)-52)>1e-9):
                        con.execute("UPDATE canvas_nodes SET title=?,w=143,h=52,updated_at=? WHERE id=?",(item['title'],t,current['id']))
                continue
            if has_versions:
                idx=free_slot; free_slot+=1; x=DEVELOPMENTAL_LANE_START_X+(idx%5)*250; y=DEVELOPMENTAL_FREE_Y+(idx//5)*92; lane_position=None
            else:
                idx=len(existing); x=DEVELOPMENTAL_LANE_START_X; y=DEVELOPMENTAL_FREE_Y; lane_position=idx
            if scope=='scenes':
                con.execute("INSERT INTO canvas_nodes(canvas_id,node_type,shape,x,y,lane_position,w,h,title,scene_id,chapter_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(cv['id'],'scene','rounded',x,y,lane_position,143,52,item['title'],eid,item.get('chapter_id'),t,t))
            else:
                con.execute("INSERT INTO canvas_nodes(canvas_id,node_type,shape,x,y,lane_position,w,h,title,chapter_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(cv['id'],'chapter','rect',x,y,lane_position,143,52,item['title'],eid,t,t))
            existing[eid]=one(con,"SELECT * FROM canvas_nodes WHERE id=last_insert_rowid()")
        # The Structure Lane, not connector topology, defines manuscript order.
        # Structural Cards remain ordinary Canvas connection endpoints: writer-created
        # relationship lines are Canvas state and must survive reconciliation.
        _ensure_developmental_version_tx(con,cv)
