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

from . import developmental

_next_scene_position = feature_call('manuscript.scenes', '_next_scene_position')
_sync_scene_outline_to_manuscript = feature_call('manuscript.scenes', '_sync_scene_outline_to_manuscript')

router = APIRouter()

def _canvas_version_detail(con,pid:int,cid:int,vid:int):
    version=one(con,"SELECT v.* FROM canvas_versions v JOIN canvases c ON c.id=v.canvas_id WHERE v.id=? AND v.canvas_id=? AND c.project_id=?",(vid,cid,pid))
    if not version: raise HTTPException(404,"Canvas version not found")
    items=allrows(con,"SELECT * FROM canvas_version_items WHERE version_id=? ORDER BY id",(vid,))
    workspace=developmental._developmental_workspace_load(version.get('workspace_json'))
    return {'version':version,'items':items,'workspace':workspace}


@router.get("/api/canvases/{cid}/versions")
def canvas_versions(cid:int):
    with storage_mutations.tx() as con:
        p=project(con); cv=one(con,"SELECT * FROM canvases WHERE id=? AND project_id=?",(cid,p['id']))
        if not cv: raise HTTPException(404,"Canvas not found")
        if cv['canvas_type']!='developmental': return {'versions':[]}
        developmental._ensure_developmental_version_tx(con,cv)
        return {'versions':allrows(con,"SELECT * FROM canvas_versions WHERE canvas_id=? ORDER BY position,id",(cid,))}


@router.get("/api/canvases/{cid}/versions/{vid}")
def canvas_version_detail(cid:int,vid:int):
    with storage_database.read_tx() as con:
        p=project(con); return _canvas_version_detail(con,p['id'],cid,vid)


@router.post("/api/canvases/{cid}/versions")
def create_canvas_version(cid:int,body:CanvasVersionCreate):
    with storage_mutations.tx() as con:
        p=project(con); cv=one(con,"SELECT * FROM canvases WHERE id=? AND project_id=?",(cid,p['id']))
        if not cv or cv['canvas_type']!='developmental': raise HTTPException(400,"Versions belong to a Developmental Canvas")
        source=None
        if body.from_version_id is not None:
            source=one(con,"SELECT * FROM canvas_versions WHERE id=? AND canvas_id=?",(body.from_version_id,cid))
            if not source: raise HTTPException(404,"Source Canvas version not found")
        pos=int(con.execute("SELECT COALESCE(MAX(position),-1)+1 FROM canvas_versions WHERE canvas_id=?",(cid,)).fetchone()[0]); t=storage_values.now()
        name=(body.name or '').strip()[:180] or f'Version {pos+1}'
        vid=con.execute("INSERT INTO canvas_versions(canvas_id,position,name,created_at,updated_at) VALUES(?,?,?,?,?)",(cid,pos,name,t,t)).lastrowid
        developmental._developmental_version_snapshot_tx(con,cv,vid,retain_missing_from=int(source['id']) if source else None)
        return one(con,"SELECT * FROM canvas_versions WHERE id=?",(vid,))


@router.patch("/api/canvases/{cid}/versions/{vid}")
def rename_canvas_version(cid:int,vid:int,body:CanvasVersionUpdate):
    name=(body.name or '').strip()[:180]
    if not name: raise HTTPException(400,"Version name cannot be empty")
    with storage_mutations.tx() as con:
        p=project(con)
        if not one(con,"SELECT v.id FROM canvas_versions v JOIN canvases c ON c.id=v.canvas_id WHERE v.id=? AND v.canvas_id=? AND c.project_id=?",(vid,cid,p['id'])): raise HTTPException(404,"Canvas version not found")
        con.execute("UPDATE canvas_versions SET name=?,updated_at=? WHERE id=?",(name,storage_values.now(),vid)); return one(con,"SELECT * FROM canvas_versions WHERE id=?",(vid,))


@router.post("/api/canvases/{cid}/versions/{vid}/update")
def update_canvas_version(cid:int,vid:int):
    with storage_mutations.tx() as con:
        p=project(con); cv=one(con,"SELECT * FROM canvases WHERE id=? AND project_id=?",(cid,p['id']))
        if not cv or cv['canvas_type']!='developmental': raise HTTPException(400,"Versions belong to a Developmental Canvas")
        if not one(con,"SELECT id FROM canvas_versions WHERE id=? AND canvas_id=?",(vid,cid)): raise HTTPException(404,"Canvas version not found")
        developmental._developmental_version_snapshot_tx(con,cv,vid,retain_missing_from=vid)
        return _canvas_version_detail(con,p['id'],cid,vid)


@router.post("/api/canvases/{cid}/versions/{vid}/activate")
def activate_canvas_version(cid:int,vid:int):
    """Load a saved Structure Version into the live developmental workspace only."""
    with storage_mutations.tx() as con:
        p=project(con); cv=one(con,"SELECT * FROM canvases WHERE id=? AND project_id=?",(cid,p['id']))
        if not cv or cv['canvas_type']!='developmental': raise HTTPException(400,"Versions belong to a Developmental Canvas")
        detail=_canvas_version_detail(con,p['id'],cid,vid); scope=(cv.get('developmental_scope') or 'chapters').lower(); et='scene' if scope=='scenes' else 'chapter'; key='scene_id' if scope=='scenes' else 'chapter_id'
        nodes=developmental._developmental_scope_nodes(con,cv); by_entity={developmental._developmental_entity_key(scope,n):n for n in nodes}; items={int(i['entity_id']):i for i in detail['items'] if i['entity_type']==et}; t=storage_values.now()
        workspace=detail.get('workspace') or {}; has_workspace=True
        styles=workspace.get('structural_styles') if isinstance(workspace.get('structural_styles'),dict) else {}
        free_slot=0
        for eid,node in by_entity.items():
            item=items.get(eid)
            if item:
                placed=bool(int(item.get('placed') or 0)); lane_position=int(item['lane_position']) if placed and item.get('lane_position') is not None else (0 if placed else None)
                x=float(item.get('x') or DEVELOPMENTAL_LANE_START_X); y=float(item.get('y') or DEVELOPMENTAL_FREE_Y)
            else:
                lane_position=None; x=DEVELOPMENTAL_LANE_START_X+(free_slot%5)*250; y=DEVELOPMENTAL_FREE_Y+(free_slot//5)*92; free_slot+=1
            style=styles.get(f'{et}:{eid}') if f'{et}:{eid}' in styles else None
            if style is not None:
                label_id=style.get('label_id');
                if label_id is not None and not one(con,"SELECT id FROM markup_labels WHERE id=? AND project_id=?",(label_id,p['id'])): label_id=None
                con.execute("UPDATE canvas_nodes SET x=?,y=?,lane_position=?,w=143,h=52,color=?,label_id=?,text_color='',updated_at=? WHERE id=?",(x,y,lane_position,style.get('color') or '',label_id,t,node['id']))
            else:
                con.execute("UPDATE canvas_nodes SET x=?,y=?,lane_position=?,w=143,h=52,updated_at=? WHERE id=?",(x,y,lane_position,t,node['id']))
        if has_workspace:
            con.execute("UPDATE canvas_nodes SET developmental_hidden=1,lane_position=NULL WHERE canvas_id=? AND node_type NOT IN ('chapter','scene')",(cid,))
            valid_labels={int(r['id']) for r in allrows(con,"SELECT id FROM markup_labels WHERE project_id=?",(p['id'],))}
            valid_icons={int(r['id']) for r in allrows(con,"SELECT id FROM icon_markers WHERE project_id=?",(p['id'],))}
            valid_chapters={int(r['id']) for r in allrows(con,"SELECT id FROM chapters WHERE project_id=?",(p['id'],))}
            valid_scenes={int(r['id']) for r in allrows(con,"SELECT id FROM scenes WHERE project_id=?",(p['id'],))}
            full_workspace=int(workspace.get('schema') or 0)>=2
            for snap in workspace.get('nodes',[]) if isinstance(workspace.get('nodes'),list) else []:
                if not isinstance(snap,dict): continue
                try:nid=int(snap.get('id'))
                except Exception:continue
                row=one(con,"SELECT id,node_type FROM canvas_nodes WHERE id=? AND canvas_id=? AND node_type NOT IN ('chapter','scene')",(nid,cid))
                if not row: continue
                label_id=snap.get('label_id'); label_id=int(label_id) if label_id is not None and int(label_id) in valid_labels else None
                icon_marker_id=snap.get('icon_marker_id'); icon_marker_id=int(icon_marker_id) if icon_marker_id is not None and int(icon_marker_id) in valid_icons else None
                chapter_id=snap.get('chapter_id'); chapter_id=int(chapter_id) if chapter_id is not None and int(chapter_id) in valid_chapters else None
                scene_id=snap.get('scene_id'); scene_id=int(scene_id) if scene_id is not None and int(scene_id) in valid_scenes else None
                lane=snap.get('lane_position'); lane=int(lane) if lane is not None else None
                snap_shape=str(snap.get('shape') or 'rect')[:24]
                if row['node_type']=='free' and snap_shape not in CONTENT_CARD_SHAPES: snap_shape='rect'
                elif row['node_type']=='figure' and snap_shape not in FIGURE_NODE_SHAPES: snap_shape='rect'
                if full_workspace:
                    image_path=str(snap.get('image_path') or '')
                    if row['node_type']!='image' or not content_security.is_safe_project_asset_path(image_path): image_path=''
                    text_align=str(snap.get('text_align') or 'left'); text_align=text_align if text_align in {'left','center','right'} else 'left'
                    figure_layer=str(snap.get('figure_layer') or 'back'); figure_layer=figure_layer if figure_layer in {'back','front'} else 'back'
                    figure_width=max(.5,min(12.0,float(snap.get('figure_border_width') or 1.0)))
                    figure_fill=normalize_color(str(snap.get('figure_fill_color') or ''),True); figure_stroke=normalize_color(str(snap.get('figure_stroke_color') or ''),True)
                    con.execute("""UPDATE canvas_nodes SET developmental_hidden=0,x=?,y=?,lane_position=?,w=?,h=?,shape=?,icon=?,title=?,body=?,display_mode=?,text_align=?,color=?,text_color=?,figure_fill_color=?,figure_stroke_color=?,figure_border_width=?,figure_layer=?,figure_line_x1=?,figure_line_y1=?,figure_line_x2=?,figure_line_y2=?,label_id=?,chapter_id=?,scene_id=?,image_path=?,icon_marker_id=?,updated_at=? WHERE id=?""",
                        (float(snap.get('x') or 0),float(snap.get('y') or 0),lane,max(18,float(snap.get('w') or 220)),max(18,float(snap.get('h') or 125)),snap_shape,str(snap.get('icon') or '')[:16],str(snap.get('title') or ''),str(snap.get('body') or ''),str(snap.get('display_mode') or 'full') if str(snap.get('display_mode') or 'full') in {'full','header','body'} else 'full',text_align,str(snap.get('color') or '')[:16],str(snap.get('text_color') or '')[:16],figure_fill,figure_stroke,figure_width,figure_layer,float(snap.get('figure_line_x1') if snap.get('figure_line_x1') is not None else .02),float(snap.get('figure_line_y1') if snap.get('figure_line_y1') is not None else .5),float(snap.get('figure_line_x2') if snap.get('figure_line_x2') is not None else .98),float(snap.get('figure_line_y2') if snap.get('figure_line_y2') is not None else .5),label_id,chapter_id,scene_id,image_path,icon_marker_id,t,nid))
                else:
                    con.execute("""UPDATE canvas_nodes SET developmental_hidden=0,x=?,y=?,lane_position=?,w=?,h=?,shape=?,icon=?,color=?,text_color=?,label_id=?,icon_marker_id=?,updated_at=? WHERE id=?""",
                        (float(snap.get('x') or 0),float(snap.get('y') or 0),lane,max(18,float(snap.get('w') or 220)),max(18,float(snap.get('h') or 125)),snap_shape,str(snap.get('icon') or '')[:16],str(snap.get('color') or '')[:16],str(snap.get('text_color') or '')[:16],label_id,icon_marker_id,t,nid))
            con.execute("UPDATE canvas_edges SET developmental_hidden=1 WHERE canvas_id=?",(cid,))
            edge_snaps=workspace.get('edges',[]) if isinstance(workspace.get('edges'),list) else []
            if full_workspace:
                for snap in edge_snaps:
                    if not isinstance(snap,dict): continue
                    try:eid=int(snap.get('id')); from_id=int(snap.get('from_node_id')); to_id=int(snap.get('to_node_id'))
                    except Exception:continue
                    row=one(con,"SELECT id FROM canvas_edges WHERE id=? AND canvas_id=?",(eid,cid))
                    endpoints=allrows(con,"SELECT id FROM canvas_nodes WHERE canvas_id=? AND id IN (?,?)",(cid,from_id,to_id))
                    if not row or len(endpoints)!=2: continue
                    marker_id=snap.get('marker_id'); marker_id=int(marker_id) if marker_id is not None and int(marker_id) in valid_labels else None
                    color=str(snap.get('color') or '')[:16]
                    if marker_id is not None:
                        marker=one(con,"SELECT color FROM markup_labels WHERE id=? AND project_id=?",(marker_id,p['id']))
                        if marker: color=marker['color']
                    con.execute("""UPDATE canvas_edges SET developmental_hidden=0,from_node_id=?,to_node_id=?,from_port=?,to_port=?,label=?,color=?,marker_id=?,line_style=?,end_style=?,line_width=?,route_style=? WHERE id=?""",
                        (from_id,to_id,str(snap.get('from_port') or 'right'),str(snap.get('to_port') or 'left'),str(snap.get('label') or '')[:180],color,marker_id,str(snap.get('line_style') or 'solid'),str(snap.get('end_style') or 'arrow'),float(snap.get('line_width') or 1.5),str(snap.get('route_style') or 'curved'),eid))
            else:
                edge_ids=[]
                for value in edge_snaps:
                    try:edge_ids.append(int(value))
                    except Exception:pass
                if edge_ids:
                    q=','.join('?' for _ in edge_ids);con.execute(f"UPDATE canvas_edges SET developmental_hidden=0 WHERE canvas_id=? AND id IN ({q})",(cid,*edge_ids))
            viewport=workspace.get('viewport') if isinstance(workspace.get('viewport'),dict) else {}
            if viewport:
                row=one(con,"SELECT value_json FROM ui_state WHERE project_id=? AND key=?",(p['id'],f'canvas-viewport-{cid}')); current=developmental._developmental_workspace_load(row.get('value_json')) if row else {}
                for k in ('zoom','left','top','laneLeft'):
                    if k in viewport and isinstance(viewport[k],(int,float)): current[k]=viewport[k]
                raw=json.dumps(current,ensure_ascii=False,separators=(',',':'))
                con.execute("INSERT INTO ui_state(project_id,key,value_json,updated_at) VALUES(?,?,?,?) ON CONFLICT(project_id,key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",(p['id'],f'canvas-viewport-{cid}',raw,t))
        reserved={int(item['lane_position']) for eid,item in items.items() if bool(int(item.get('placed') or 0)) and item.get('lane_position') is not None and eid not in by_entity}
        lane_rows=allrows(con,"SELECT id,lane_position,node_type FROM canvas_nodes WHERE canvas_id=? AND COALESCE(developmental_hidden,0)=0 AND lane_position IS NOT NULL AND (node_type=? OR node_type='free') ORDER BY lane_position,CASE WHEN node_type=? THEN 0 ELSE 1 END,id",(cid,et,et))
        pos=0
        for row in lane_rows:
            while pos in reserved: pos+=1
            con.execute("UPDATE canvas_nodes SET lane_position=?,updated_at=? WHERE id=?",(pos,t,row['id']));pos+=1
        return _canvas_version_detail(con,p['id'],cid,vid)


@router.delete("/api/canvases/{cid}/versions/{vid}")
def delete_canvas_version(cid:int,vid:int):
    with storage_mutations.tx() as con:
        p=project(con); rows=allrows(con,"SELECT v.* FROM canvas_versions v JOIN canvases c ON c.id=v.canvas_id WHERE v.canvas_id=? AND c.project_id=? ORDER BY v.position,v.id",(cid,p['id']))
        if not any(int(r['id'])==vid for r in rows): raise HTTPException(404,"Canvas version not found")
        if len(rows)<=1: raise HTTPException(400,"A Developmental Canvas must keep at least one version")
        con.execute("DELETE FROM canvas_versions WHERE id=?",(vid,)); remaining=allrows(con,"SELECT id FROM canvas_versions WHERE canvas_id=? ORDER BY position,id",(cid,))
        for pos,row in enumerate(remaining): con.execute("UPDATE canvas_versions SET position=? WHERE id=?",(pos,row['id']))
        return {'ok':True}


@router.delete("/api/canvases/{cid}/versions/{vid}/items/{item_id}")
def delete_canvas_version_item(cid:int,vid:int,item_id:int):
    with storage_mutations.tx() as con:
        p=project(con); detail=_canvas_version_detail(con,p['id'],cid,vid)
        if not any(int(i['id'])==item_id for i in detail['items']): raise HTTPException(404,"Version item not found")
        con.execute("DELETE FROM canvas_version_items WHERE id=? AND version_id=?",(item_id,vid)); return {'ok':True}


@router.post("/api/canvases/{cid}/developmental-layout")
def update_developmental_layout(cid:int,body:DevelopmentalLayout):
    """Atomically persist Developmental lane membership/order and desk positions.

    Structural Chapter/Scene tiles and ordinary free Cards may occupy the lane.
    Only the structural tiles participate in Apply to Manuscript.
    """
    with storage_mutations.tx() as con:
        p=project(con); cv=one(con,"SELECT * FROM canvases WHERE id=? AND project_id=?",(cid,p['id']))
        if not cv or cv['canvas_type']!='developmental': raise HTTPException(400,"Structure Lane belongs to a Developmental Canvas")
        scope=(cv.get('developmental_scope') or 'chapters').lower(); wanted='scene' if scope=='scenes' else 'chapter'; t=storage_values.now()
        ids=[int(i.node_id) for i in body.items]
        if len(ids)!=len(set(ids)): raise HTTPException(400,"Duplicate Canvas Cards")
        rows_by_id={}
        if ids:
            q=','.join('?' for _ in ids); rows=allrows(con,f"SELECT * FROM canvas_nodes WHERE canvas_id=? AND id IN ({q})",(cid,*ids)); rows_by_id={int(r['id']):r for r in rows}
            if len(rows)!=len(ids): raise HTTPException(400,"Canvas Card was not found")
            for r in rows:
                if r['node_type'] not in {wanted,'free'}: raise HTTPException(400,"Only structural tiles and ordinary Cards may enter the Structure Lane")
        lane_values=[int(i.lane_position) for i in body.items if i.lane_position is not None]
        if any(v<0 for v in lane_values) or len(lane_values)!=len(set(lane_values)): raise HTTPException(400,"Structure Lane positions must be unique non-negative integers")
        for item in body.items:
            row=rows_by_id[int(item.node_id)]; lane=int(item.lane_position) if item.lane_position is not None else None
            x=float(item.x) if item.x is not None else float(row.get('x') or DEVELOPMENTAL_LANE_START_X)
            y=float(item.y) if item.y is not None else float(row.get('y') or DEVELOPMENTAL_FREE_Y)
            if row['node_type']==wanted:
                con.execute("UPDATE canvas_nodes SET x=?,y=?,lane_position=?,w=143,h=52,updated_at=? WHERE id=? AND canvas_id=?",(x,y,lane,t,int(item.node_id),cid))
            else:
                con.execute("UPDATE canvas_nodes SET x=?,y=?,lane_position=?,updated_at=? WHERE id=? AND canvas_id=?",(x,y,lane,t,int(item.node_id),cid))
        # The client normally submits the whole visual lane, but stale/partial clients
        # must not be able to collide with a dock position they omitted from the body.
        docked=allrows(con,"SELECT id,lane_position FROM canvas_nodes WHERE canvas_id=? AND lane_position IS NOT NULL AND (node_type=? OR node_type='free') ORDER BY lane_position,id",(cid,wanted))
        occupied={}
        for row in docked:
            pos=int(row['lane_position'])
            if pos<0 or pos in occupied: raise HTTPException(400,"Structure Lane positions must be unique non-negative integers")
            occupied[pos]=int(row['id'])
        storage_invariants.validate_project_invariants(con,p['id'])
        return {'ok':True}


@router.post("/api/canvases/{cid}/developmental-promote/{node_id}")
def promote_developmental_planning_card(cid:int,node_id:int,body:DevelopmentalPromote):
    """Turn one ordinary lane Card into a real Chapter/Scene at that proposal point."""
    with storage_mutations.tx() as con:
        p=project(con); pid=int(p['id']); cv=one(con,"SELECT * FROM canvases WHERE id=? AND project_id=?",(cid,pid))
        if not cv or cv['canvas_type']!='developmental': raise HTTPException(400,"Promotion belongs to a Developmental Canvas")
        node=one(con,"SELECT * FROM canvas_nodes WHERE id=? AND canvas_id=?",(node_id,cid))
        if not node or node['node_type']!='free' or node.get('lane_position') is None: raise HTTPException(400,"Only an ordinary Card already in the Structure Lane can be promoted")
        scope=(cv.get('developmental_scope') or 'chapters').lower(); lane=int(node['lane_position']); title=(body.title or node.get('title') or '').strip()[:300] or ('New Scene' if scope=='scenes' else 'New Chapter'); t=storage_values.now()
        structural_type='scene' if scope=='scenes' else 'chapter'
        neighbors=allrows(con,"SELECT * FROM canvas_nodes WHERE canvas_id=? AND node_type=? AND lane_position IS NOT NULL AND id<>? ORDER BY lane_position,id",(cid,structural_type,node_id))
        before=[r for r in neighbors if int(r['lane_position'])<lane]; after=[r for r in neighbors if int(r['lane_position'])>lane]
        prev=before[-1] if before else None; nxt=after[0] if after else None
        if scope=='chapters':
            tab=one(con,"SELECT * FROM document_tabs WHERE project_id=? AND is_manuscript=1 ORDER BY position,id LIMIT 1",(pid,))
            if not tab: raise HTTPException(409,"Create a Main Manuscript before creating a Chapter")
            if prev and prev.get('chapter_id'):
                anchor=one(con,"SELECT * FROM chapters WHERE id=? AND project_id=?",(prev['chapter_id'],pid)); pos=float(anchor['position'])+1 if anchor else 0
            elif nxt and nxt.get('chapter_id'):
                anchor=one(con,"SELECT * FROM chapters WHERE id=? AND project_id=?",(nxt['chapter_id'],pid)); pos=float(anchor['position']) if anchor else 0
            else: pos=float(con.execute("SELECT COALESCE(MAX(position),-1)+1 FROM chapters WHERE project_id=? AND tab_id=?",(pid,tab['id'])).fetchone()[0])
            con.execute("UPDATE chapters SET position=position+1,updated_at=? WHERE project_id=? AND tab_id=? AND position>=?",(t,pid,tab['id'],pos))
            entity_id=con.execute("INSERT INTO chapters(project_id,tab_id,position,title,description,content_html,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",(pid,tab['id'],pos,title,'','','',t,t)).lastrowid
            bid=storage_values.uid('b_'); con.execute("INSERT INTO manuscript_blocks(id,project_id,tab_id,chapter_id,position,block_type,semantic_style,content_html,text_content,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(bid,pid,tab['id'],entity_id,0,'paragraph','body','','','',t,t))
            con.execute("UPDATE canvas_nodes SET node_type='chapter',shape='rect',body='',chapter_id=?,scene_id=NULL,w=143,h=52,updated_at=? WHERE id=?",(entity_id,t,node_id))
        else:
            candidate_ids=[]
            for r in (prev,nxt):
                if r and r.get('chapter_id') is not None and int(r['chapter_id']) not in candidate_ids: candidate_ids.append(int(r['chapter_id']))
            chapter_id=int(body.chapter_id) if body.chapter_id is not None else (candidate_ids[0] if len(candidate_ids)==1 else None)
            if chapter_id is None: raise HTTPException(409,"Choose which Chapter should contain this new Scene")
            chapter=one(con,"SELECT c.* FROM chapters c JOIN document_tabs t2 ON t2.id=c.tab_id WHERE c.id=? AND c.project_id=? AND t2.is_manuscript=1",(chapter_id,pid))
            if not chapter: raise HTTPException(400,"Scene Chapter was not found in the Main Manuscript")
            if candidate_ids and chapter_id not in candidate_ids: raise HTTPException(400,"Choose one of the neighboring Chapters for this Scene")
            insert_pos=None
            if prev and int(prev.get('chapter_id') or -1)==chapter_id and prev.get('scene_id'):
                last=one(con,"SELECT * FROM manuscript_blocks WHERE chapter_id=? AND scene_id=? ORDER BY position DESC,id DESC LIMIT 1",(chapter_id,prev['scene_id']))
                if last: insert_pos=float(last['position'])+1
            if insert_pos is None and nxt and int(nxt.get('chapter_id') or -1)==chapter_id and nxt.get('scene_id'):
                first=one(con,"SELECT * FROM manuscript_blocks WHERE chapter_id=? AND scene_id=? ORDER BY position,id LIMIT 1",(chapter_id,nxt['scene_id']))
                if first: insert_pos=float(first['position'])
            if insert_pos is None: insert_pos=float(con.execute("SELECT COALESCE(MAX(position),-1)+1 FROM manuscript_blocks WHERE chapter_id=?",(chapter_id,)).fetchone()[0])
            con.execute("UPDATE manuscript_blocks SET position=position+1,updated_at=? WHERE chapter_id=? AND position>=?",(t,chapter_id,insert_pos))
            scene_pos=_next_scene_position(con,pid,chapter['tab_id'])
            entity_id=con.execute("INSERT INTO scenes(project_id,tab_id,position,title,description,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(pid,chapter['tab_id'],scene_pos,title,'',t,t)).lastrowid
            bid=storage_values.uid('b_'); con.execute("INSERT INTO manuscript_blocks(id,project_id,tab_id,chapter_id,scene_id,position,block_type,semantic_style,content_html,text_content,image_path,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(bid,pid,chapter['tab_id'],chapter_id,entity_id,insert_pos,'paragraph','body','','','','',t,t))
            _sync_scene_outline_to_manuscript(con,pid,chapter['tab_id'])
            con.execute("UPDATE canvas_nodes SET node_type='scene',shape='rounded',body='',scene_id=?,chapter_id=?,w=143,h=52,updated_at=? WHERE id=?",(entity_id,chapter_id,t,node_id))
        out=one(con,"SELECT * FROM canvas_nodes WHERE id=?",(node_id,))
        storage_invariants.validate_project_invariants(con,pid)
    commit(f"Created {title} from Developmental Canvas","structure"); developmental.sync_developmental_canvases(pid); return out


