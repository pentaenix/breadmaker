from __future__ import annotations

"""Canonical project-state invariants."""

import sqlite3
import re

from security import content_security
from storage import values as storage_values

def _scene_invariant_errors_from_rows(tabs:list[dict], chapters:list[dict], blocks:list[dict], scenes:list[dict]) -> list[str]:
    errors=[]
    tab_ids={int(x['id']) for x in tabs}; chapter_by={int(x['id']):x for x in chapters}; scene_by={int(x['id']):x for x in scenes}
    tab_pos={int(x['id']):(float(x.get('position') or 0),int(x['id'])) for x in tabs}
    ordered=sorted(blocks,key=lambda b:(tab_pos.get(int(b.get('tab_id') or 0),(10**9,0)),float((chapter_by.get(int(b.get('chapter_id') or 0)) or {}).get('position') or 0),float(b.get('position') or 0),str(b.get('id'))))
    by_tab={}
    for b in ordered: by_tab.setdefault(int(b.get('tab_id') or 0),[]).append(b)
    for sid,scene in scene_by.items():
        stab=int(scene.get('tab_id') or 0)
        if stab not in tab_ids: errors.append(f'Scene {sid} references a missing Tab'); continue
        owned=[b for b in ordered if int(b.get('scene_id') or 0)==sid]
        if not owned:
            errors.append(f'Scene {sid} has no manuscript prose'); continue
        if any(int(b.get('tab_id') or 0)!=stab for b in owned): errors.append(f'Scene {sid} crosses Tabs')
        seq=by_tab.get(stab,[]); positions=[i for i,b in enumerate(seq) if int(b.get('scene_id') or 0)==sid]
        if positions:
            segment=seq[min(positions):max(positions)+1]
            if any(int(b.get('scene_id') or 0)!=sid for b in segment): errors.append(f'Scene {sid} is not contiguous')
    return errors


def project_invariant_errors(con: sqlite3.Connection, project_id:int) -> list[str]:
    """Return structural errors for one Current project using a bounded query set.

    This is intentionally a trust-boundary validator, not a typing-path hook.
    """
    pid=int(project_id); errors=[]
    project_rows=[dict(r) for r in con.execute("SELECT id,project_uid FROM projects ORDER BY id")]
    if len(project_rows)!=1:
        errors.append('Project database must contain exactly one project identity row')
    elif int(project_rows[0].get('id') or 0)!=pid:
        errors.append('Project identity row does not match the active project row')
    elif not re.fullmatch(r'[0-9a-f]{32}',str(project_rows[0].get('project_uid') or '')):
        errors.append('Project identity is invalid')
    tabs=[dict(r) for r in con.execute("SELECT id,position,is_manuscript FROM document_tabs WHERE project_id=? ORDER BY position,id",(pid,))]
    chapters=[dict(r) for r in con.execute("SELECT id,tab_id,position,is_implicit,title FROM chapters WHERE project_id=? ORDER BY tab_id,position,id",(pid,))]
    blocks=[dict(r) for r in con.execute("SELECT id,tab_id,chapter_id,scene_id,position,block_type,text_content,image_width,image_row_id FROM manuscript_blocks WHERE project_id=?",(pid,))]
    scenes=[dict(r) for r in con.execute("SELECT id,tab_id,position FROM scenes WHERE project_id=?",(pid,))]
    bookmarks=[dict(r) for r in con.execute("SELECT * FROM bookmarks WHERE project_id=?",(pid,))]
    annotations=[dict(r) for r in con.execute("SELECT id,block_id,chapter_id,label_id,thread_id,start_offset,end_offset,stale FROM annotations WHERE project_id=?",(pid,))]
    canvas_versions=[dict(r) for r in con.execute("SELECT v.id,v.canvas_id,c.canvas_type,c.developmental_scope FROM canvas_versions v JOIN canvases c ON c.id=v.canvas_id WHERE c.project_id=?",(pid,))]
    canvas_version_items=[dict(r) for r in con.execute("SELECT i.id,i.version_id,i.entity_type,i.lane_position,i.placed FROM canvas_version_items i JOIN canvas_versions v ON v.id=i.version_id JOIN canvases c ON c.id=v.canvas_id WHERE c.project_id=?",(pid,))]
    canvas_nodes=[dict(r) for r in con.execute("SELECT n.id,n.canvas_id,n.node_type,n.lane_position,c.canvas_type,c.developmental_scope FROM canvas_nodes n JOIN canvases c ON c.id=n.canvas_id WHERE c.project_id=?",(pid,))]
    reviews=[dict(r) for r in con.execute("SELECT * FROM reviews WHERE project_id=? ORDER BY position,id",(pid,))]
    review_participants=[dict(r) for r in con.execute("SELECT rp.id,rp.review_id,rp.display_name,rp.status,rp.started_at,rp.completed_at FROM review_participants rp JOIN reviews r ON r.id=rp.review_id WHERE r.project_id=? ORDER BY rp.review_id,rp.display_name,rp.id",(pid,))]
    review_scope_chapters=[dict(r) for r in con.execute("SELECT rs.review_id,rs.chapter_id FROM review_scope_chapters rs JOIN reviews r ON r.id=rs.review_id WHERE r.project_id=?",(pid,))]
    review_scope_scenes=[dict(r) for r in con.execute("SELECT rs.review_id,rs.scene_id FROM review_scope_scenes rs JOIN reviews r ON r.id=rs.review_id WHERE r.project_id=?",(pid,))]
    review_marker_labels=[dict(r) for r in con.execute("SELECT * FROM review_marker_labels WHERE project_id=? ORDER BY created_at,id",(pid,))]
    review_markers=[dict(r) for r in con.execute("SELECT rm.* FROM review_markers rm JOIN reviews r ON r.id=rm.review_id WHERE r.project_id=? ORDER BY rm.review_id,rm.position,rm.id",(pid,))]
    review_snapshots=[dict(r) for r in con.execute("SELECT rs.* FROM review_snapshots rs JOIN reviews r ON r.id=rs.review_id WHERE r.project_id=?",(pid,))]
    review_snapshot_checkpoints=[dict(r) for r in con.execute("SELECT rc.* FROM review_snapshot_checkpoints rc JOIN review_snapshots rs ON rs.id=rc.snapshot_id JOIN reviews r ON r.id=rs.review_id WHERE r.project_id=? ORDER BY rc.snapshot_id,rc.ordinal,rc.id",(pid,))]
    review_snapshot_markers=[dict(r) for r in con.execute("SELECT rm.* FROM review_snapshot_markers rm JOIN review_snapshots rs ON rs.id=rm.snapshot_id JOIN reviews r ON r.id=rs.review_id WHERE r.project_id=? ORDER BY rm.snapshot_id,rm.position,rm.id",(pid,))]
    review_snapshot_chapters=[dict(r) for r in con.execute("SELECT c.* FROM review_snapshot_chapters c JOIN review_snapshots rs ON rs.id=c.snapshot_id JOIN reviews r ON r.id=rs.review_id WHERE r.project_id=? ORDER BY c.snapshot_id,c.ordinal,c.id",(pid,))]
    review_snapshot_scenes=[dict(r) for r in con.execute("SELECT s.* FROM review_snapshot_scenes s JOIN review_snapshots rs ON rs.id=s.snapshot_id JOIN reviews r ON r.id=rs.review_id WHERE r.project_id=? ORDER BY s.snapshot_id,s.ordinal,s.id",(pid,))]
    review_snapshot_blocks=[dict(r) for r in con.execute("SELECT b.* FROM review_snapshot_blocks b JOIN review_snapshots rs ON rs.id=b.snapshot_id JOIN reviews r ON r.id=rs.review_id WHERE r.project_id=? ORDER BY b.snapshot_id,b.ordinal,b.id",(pid,))]
    questionnaires=[dict(r) for r in con.execute("SELECT * FROM questionnaires WHERE project_id=? ORDER BY name,id",(pid,))]
    questionnaire_questions=[dict(r) for r in con.execute("SELECT qq.* FROM questionnaire_questions qq JOIN questionnaires q ON q.id=qq.questionnaire_id WHERE q.project_id=? ORDER BY qq.questionnaire_id,qq.position,qq.id",(pid,))]
    review_questionnaires=[dict(r) for r in con.execute("SELECT rq.* FROM review_questionnaires rq JOIN reviews r ON r.id=rq.review_id WHERE r.project_id=? ORDER BY rq.review_id,rq.position,rq.id",(pid,))]
    review_snapshot_questions=[dict(r) for r in con.execute("SELECT q.* FROM review_snapshot_questions q JOIN review_snapshots rs ON rs.id=q.snapshot_id JOIN reviews r ON r.id=rs.review_id WHERE r.project_id=? ORDER BY q.snapshot_id,q.position,q.id",(pid,))]
    labels={int(r['id']) for r in con.execute("SELECT id FROM markup_labels WHERE project_id=?",(pid,))}
    review_marker_label_ids={int(r['id']) for r in review_marker_labels}
    threads={int(r['id']) for r in con.execute("SELECT id FROM comment_threads WHERE project_id=?",(pid,))}
    tab_ids={int(x['id']) for x in tabs}; chapter_by={int(x['id']):x for x in chapters}; block_by={str(x['id']):x for x in blocks}; scene_ids={int(x['id']) for x in scenes}
    # Image rows are deliberately constrained layout: 2-4 adjacent image blocks
    # in one Chapter/Scene with one shared row width.
    image_rows={}
    by_chapter_blocks={}
    for block in sorted(blocks,key=lambda row:(int(row.get('chapter_id') or 0),float(row.get('position') or 0),str(row.get('id') or ''))):
        by_chapter_blocks.setdefault(int(block.get('chapter_id') or 0),[]).append(block)
        rid=str(block.get('image_row_id') or '').strip()
        if rid:image_rows.setdefault(rid,[]).append(block)
    for rid,members in image_rows.items():
        if not 2<=len(members)<=4: errors.append(f'Image row {rid} must contain 2 to 4 images')
        if any(str(row.get('block_type') or '')!='image' for row in members): errors.append(f'Image row {rid} contains non-image content')
        chapter_ids={int(row.get('chapter_id') or 0) for row in members}
        if len(chapter_ids)!=1: errors.append(f'Image row {rid} crosses Chapters')
        row_scene_ids={row.get('scene_id') for row in members}
        if len(row_scene_ids)!=1: errors.append(f'Image row {rid} crosses Scenes')
        widths={int(row.get('image_width') or 100) for row in members}
        if len(widths)!=1: errors.append(f'Image row {rid} has inconsistent width')
        if len(chapter_ids)==1:
            cid=next(iter(chapter_ids)); ordered=by_chapter_blocks.get(cid,[]); indexes=[i for i,row in enumerate(ordered) if str(row.get('image_row_id') or '')==rid]
            if indexes and indexes!=list(range(indexes[0],indexes[0]+len(indexes))): errors.append(f'Image row {rid} is not contiguous')
    review_ids={str(review.get('id') or '') for review in reviews}
    participant_names_by_review={}
    for participant in review_participants:
        participant_id=str(participant.get('id') or '')
        rid=str(participant.get('review_id') or '')
        name=str(participant.get('display_name') or '').strip()
        if not re.fullmatch(r'review_participant_[0-9a-f]{32}',participant_id): errors.append(f'Review Participant {participant_id or "<missing>"} has an invalid identity')
        if rid not in review_ids: errors.append(f'Review Participant {participant_id or "<missing>"} references a missing Review')
        if not name or len(name)>120: errors.append(f'Review Participant {participant_id or "<missing>"} has an invalid name')
        if str(participant.get('status') or '') not in {'not_started','in_progress','completed','revoked'}: errors.append(f'Review Participant {participant_id or "<missing>"} has invalid status')
        folded=name.casefold(); names=participant_names_by_review.setdefault(rid,set())
        if folded in names: errors.append(f'Review {rid or "<missing>"} has duplicate participant name {name or "<missing>"}')
        names.add(folded)
    scope_chapters_by_review={}
    for item in review_scope_chapters: scope_chapters_by_review.setdefault(str(item['review_id']),set()).add(int(item['chapter_id']))
    scope_scenes_by_review={}
    for item in review_scope_scenes: scope_scenes_by_review.setdefault(str(item['review_id']),set()).add(int(item['scene_id']))
    review_marker_names=set()
    for marker in review_marker_labels:
        mid=int(marker.get('id') or 0);name=str(marker.get('name') or '').strip();description=str(marker.get('description') or '');color=str(marker.get('color') or '')
        if not name or len(name)>120: errors.append(f'Review Marker {mid or "<missing>"} has an invalid name')
        if len(description)>4000: errors.append(f'Review Marker {mid or "<missing>"} has an invalid description')
        if not re.fullmatch(r'#[0-9a-fA-F]{6}',color): errors.append(f'Review Marker {mid or "<missing>"} has an invalid color')
        folded=name.casefold()
        if folded in review_marker_names: errors.append(f'Review Marker name {name or "<missing>"} is duplicated')
        review_marker_names.add(folded)
    marker_ids_by_review={}
    for item in review_markers:
        rid=str(item.get('review_id') or ''); marker_id=int(item.get('marker_id') or 0)
        if rid not in review_ids: errors.append(f'Review Marker link references a missing Review {rid or "<missing>"}')
        if marker_id not in review_marker_label_ids: errors.append(f'Review {rid or "<missing>"} references a missing Review Marker {marker_id or "<missing>"}')
        marker_ids_by_review.setdefault(rid,set()).add(marker_id)
    for review in reviews:
        rid=str(review.get('id') or '')
        if not re.fullmatch(r'review_[0-9a-f]{32}',rid): errors.append(f'Review {rid or "<missing>"} has an invalid identity')
        name=str(review.get('name') or '')
        if not name.strip() or len(name)>120: errors.append(f'Review {rid or "<missing>"} has an invalid name')
        description=str(review.get('description') or '')
        if len(description)>4000: errors.append(f'Review {rid or "<missing>"} has an invalid description')
        if str(review.get('status') or '') not in {'draft','open','paused','closed','archived'}: errors.append(f'Review {rid or "<missing>"} has an invalid status')
        if str(review.get('reviewer_visibility') or '') not in {'private','shared'}: errors.append(f'Review {rid or "<missing>"} has invalid reviewer visibility')
        permissions={field:int(review.get(field) or 0) for field in ('permission_read','permission_comment','permission_suggest','permission_feedback')}
        if any(value not in (0,1) for value in permissions.values()): errors.append(f'Review {rid or "<missing>"} has invalid permissions')
        if (permissions['permission_comment'] or permissions['permission_suggest']) and not permissions['permission_read']: errors.append(f'Review {rid or "<missing>"} has text permissions without Read')
        scope_type=str(review.get('scope_type') or '')
        chapter_scope=scope_chapters_by_review.get(rid,set()); scene_scope=scope_scenes_by_review.get(rid,set())
        if scope_type not in {'manuscript','chapters','scenes'}: errors.append(f'Review {rid or "<missing>"} has an invalid scope type')
        elif scope_type=='manuscript' and (chapter_scope or scene_scope): errors.append(f'Review {rid or "<missing>"} has selected items in manuscript scope')
        elif scope_type=='chapters' and scene_scope: errors.append(f'Review {rid or "<missing>"} mixes Chapter and Scene scope')
        elif scope_type=='scenes' and chapter_scope: errors.append(f'Review {rid or "<missing>"} mixes Chapter and Scene scope')
        if any(chapter_id not in chapter_by or int(chapter_by[chapter_id].get('is_implicit') or 0) for chapter_id in chapter_scope): errors.append(f'Review {rid or "<missing>"} references an unavailable Chapter')
        if any(scene_id not in scene_ids for scene_id in scene_scope): errors.append(f'Review {rid or "<missing>"} references an unavailable Scene')
    questionnaire_ids=set(); questionnaire_names=set(); questions_by_questionnaire={}
    for questionnaire in questionnaires:
        qid=str(questionnaire.get('id') or ''); name=str(questionnaire.get('name') or '').strip(); description=str(questionnaire.get('description') or '')
        if not re.fullmatch(r'questionnaire_[0-9a-f]{32}',qid): errors.append(f'Questionnaire {qid or "<missing>"} has an invalid identity')
        if not name or len(name)>120: errors.append(f'Questionnaire {qid or "<missing>"} has an invalid name')
        if len(description)>4000: errors.append(f'Questionnaire {qid or "<missing>"} has an invalid description')
        folded=name.casefold()
        if folded in questionnaire_names: errors.append(f'Questionnaire name {name or "<missing>"} is duplicated')
        questionnaire_names.add(folded); questionnaire_ids.add(qid)
    for question in questionnaire_questions:
        qid=str(question.get('id') or ''); parent=str(question.get('questionnaire_id') or ''); prompt=str(question.get('prompt') or '').strip()
        if not re.fullmatch(r'questionnaire_question_[0-9a-f]{32}',qid): errors.append(f'Questionnaire Question {qid or "<missing>"} has an invalid identity')
        if parent not in questionnaire_ids: errors.append(f'Questionnaire Question {qid or "<missing>"} references a missing Questionnaire')
        if not prompt or len(prompt)>12000: errors.append(f'Questionnaire Question {qid or "<missing>"} has an invalid prompt')
        if int(question.get('required') or 0) not in (0,1): errors.append(f'Questionnaire Question {qid or "<missing>"} has invalid required state')
        questions_by_questionnaire.setdefault(parent,[]).append(question)
    attached_pairs=set()
    for link in review_questionnaires:
        lid=str(link.get('id') or ''); rid=str(link.get('review_id') or ''); qid=str(link.get('questionnaire_id') or '')
        if not re.fullmatch(r'review_questionnaire_[0-9a-f]{32}',lid): errors.append(f'Review Questionnaire link {lid or "<missing>"} has an invalid identity')
        if rid not in review_ids: errors.append(f'Review Questionnaire link {lid or "<missing>"} references a missing Review')
        if qid not in questionnaire_ids: errors.append(f'Review Questionnaire link {lid or "<missing>"} references a missing Questionnaire')
        pair=(rid,qid)
        if pair in attached_pairs: errors.append(f'Review {rid or "<missing>"} attaches Questionnaire {qid or "<missing>"} more than once')
        attached_pairs.add(pair)

    chapters_by_snapshot={}; scenes_by_snapshot={}; blocks_by_snapshot={}; questions_by_snapshot={}; markers_by_snapshot={}; checkpoints_by_snapshot={}; snapshots_by_review={}
    for row in review_snapshot_checkpoints: checkpoints_by_snapshot.setdefault(str(row['snapshot_id']),[]).append(row)
    for row in review_snapshot_markers: markers_by_snapshot.setdefault(str(row['snapshot_id']),[]).append(row)
    for row in review_snapshot_chapters: chapters_by_snapshot.setdefault(str(row['snapshot_id']),[]).append(row)
    for row in review_snapshot_scenes: scenes_by_snapshot.setdefault(str(row['snapshot_id']),[]).append(row)
    for row in review_snapshot_blocks: blocks_by_snapshot.setdefault(str(row['snapshot_id']),[]).append(row)
    for row in review_snapshot_questions: questions_by_snapshot.setdefault(str(row['snapshot_id']),[]).append(row)
    for row in review_snapshots: snapshots_by_review.setdefault(str(row['review_id']),[]).append(row)
    for rid,items in snapshots_by_review.items():
        ordered=sorted(items,key=lambda row:(int(row.get('round_number') or 0),str(row.get('id') or '')))
        numbers=[int(row.get('round_number') or 0) for row in ordered]
        if numbers != list(range(1,len(numbers)+1)): errors.append(f'Review {rid} round numbers are not contiguous from 1')
        for snap in ordered:
            sid=str(snap.get('id') or '')
            if not re.fullmatch(r'review_snapshot_[0-9a-f]{32}',sid): errors.append(f'Review {rid} has an invalid snapshot identity')
            if str(snap.get('status') or '') not in {'open','paused','closed'}: errors.append(f'Review {rid} round {snap.get("round_number")} has invalid status')
            ch=chapters_by_snapshot.get(sid,[]); sc=scenes_by_snapshot.get(sid,[]); bl=blocks_by_snapshot.get(sid,[]); qs=questions_by_snapshot.get(sid,[]); mks=markers_by_snapshot.get(sid,[]); cps=checkpoints_by_snapshot.get(sid,[])
            checkpoint_ordinals=[int(row.get('ordinal') or 0) for row in cps]
            if checkpoint_ordinals!=list(range(len(cps))): errors.append(f'Review {rid} round {snap.get("round_number")} checkpoint links are not contiguous from 0')
            if not cps or len(cps)>3: errors.append(f'Review {rid} round {snap.get("round_number")} must link between one and three Checkpoints')
            if any(row.get('checkpoint_id') is None for row in cps): errors.append(f'Review {rid} round {snap.get("round_number")} references a deleted Checkpoint')
            perms={field:bool(snap.get(field)) for field in ('permission_read','permission_comment','permission_suggest','permission_feedback')}
            expected=storage_values.review_snapshot_content_hash(rid,int(snap.get('source_generation') or 0),str(snap.get('scope_type') or ''),perms,ch,sc,bl,qs,mks)
            if str(snap.get('content_hash') or '')!=expected: errors.append(f'Review {rid} round {snap.get("round_number")} content hash does not match its frozen content')
            if not perms['permission_feedback'] and qs: errors.append(f'Review {rid} round {snap.get("round_number")} freezes questions without Feedback permission')
    for review in reviews:
        rid=str(review.get('id') or ''); rounds=sorted(snapshots_by_review.get(rid,[]),key=lambda row:int(row.get('round_number') or 0)); status=str(review.get('status') or '')
        open_rounds=[row for row in rounds if str(row.get('status') or '')=='open']
        paused_rounds=[row for row in rounds if str(row.get('status') or '')=='paused']
        if len(open_rounds)>1: errors.append(f'Review {rid} has more than one live Open round')
        if status=='draft' and rounds: errors.append(f'Review {rid} is Draft but already has Review rounds')
        if status in {'open','paused','closed','archived'} and not rounds: errors.append(f'Review {rid} is {status.title()} without a Review round')
        if status=='open' and not open_rounds: errors.append(f'Review {rid} is Open without an Open round')
        if status=='paused' and (open_rounds or not paused_rounds): errors.append(f'Review {rid} is Paused without a paused round')
        if status=='closed' and (open_rounds or paused_rounds): errors.append(f'Review {rid} is Closed while a round is still Open or Paused')
        if status=='archived' and (open_rounds or paused_rounds): errors.append(f'Review {rid} is Archived while a round is still Open or Paused')
    if tabs:
        mains=[x for x in tabs if int(x.get('is_manuscript') or 0)==1]
        if len(mains)!=1: errors.append(f'Project must have exactly one Main Manuscript Tab; found {len(mains)}')
    else: errors.append('Project has no Tabs')
    for ch in chapters:
        if int(ch.get('tab_id') or 0) not in tab_ids: errors.append(f"Chapter {ch['id']} references a missing Tab")
    for b in blocks:
        ch=chapter_by.get(int(b.get('chapter_id') or 0))
        if not ch: errors.append(f"Block {b['id']} references a missing Chapter"); continue
        if int(b.get('tab_id') or 0)!=int(ch.get('tab_id') or 0): errors.append(f"Block {b['id']} Tab does not match its Chapter")
        if b.get('scene_id') is not None and int(b.get('scene_id')) not in scene_ids: errors.append(f"Block {b['id']} references a missing Scene")
    errors.extend(_scene_invariant_errors_from_rows(tabs,chapters,blocks,scenes))
    for bm in bookmarks:
        kind=str(bm.get('target_type') or '')
        if kind=='chapter':
            ch=chapter_by.get(int(bm.get('chapter_id') or 0))
            if not ch or int(ch.get('is_implicit') or 0): errors.append('Bookmark references a missing/implicit Chapter')
        elif kind=='scene':
            if int(bm.get('scene_id') or 0) not in scene_ids: errors.append('Bookmark references a missing Scene')
        elif kind=='range':
            a=block_by.get(str(bm.get('start_block_id') or '')); b=block_by.get(str(bm.get('end_block_id') or ''))
            if not a or not b: errors.append('Text Bookmark references missing prose')
            else:
                if int(a.get('tab_id') or 0)!=int(b.get('tab_id') or 0): errors.append('Text Bookmark crosses Tabs')
                so=int(bm.get('start_offset') or 0); eo=int(bm.get('end_offset') or 0)
                for edge,row,offset in (('start',a,so),('end',b,eo)):
                    if offset<0 or offset>len(str(row.get('text_content') or '')): errors.append(f'Text Bookmark {edge} offset is invalid')
                if a['id']==b['id'] and eo<so: errors.append('Text Bookmark range is reversed')
        else: errors.append('Bookmark target type is invalid')
    version_ids=set(); version_by={}
    for version in canvas_versions:
        if version.get('canvas_type')!='developmental': errors.append(f"Canvas version {version['id']} belongs to a non-Developmental Canvas")
        vid=int(version['id']); version_ids.add(vid); version_by[vid]=version
    version_lane_seen={}
    for item in canvas_version_items:
        vid=int(item.get('version_id') or 0)
        if vid not in version_ids: errors.append(f"Canvas version item {item['id']} references a missing version")
        if item.get('entity_type') not in {'chapter','scene'}: errors.append(f"Canvas version item {item['id']} type is invalid")
        version=version_by.get(vid)
        if version:
            wanted='scene' if str(version.get('developmental_scope') or 'chapters').lower()=='scenes' else 'chapter'
            if item.get('entity_type')!=wanted: errors.append(f"Canvas version item {item['id']} does not match its Developmental scope")
        placed=bool(int(item.get('placed') or 0)); lane=item.get('lane_position')
        if placed:
            try: pos=int(lane)
            except (TypeError,ValueError): errors.append(f"Canvas version item {item['id']} has no valid lane position"); continue
            if pos<0: errors.append(f"Canvas version item {item['id']} has a negative lane position")
            key=(vid,pos)
            if key in version_lane_seen: errors.append(f"Canvas version {vid} has duplicate lane position {pos}")
            else: version_lane_seen[key]=int(item['id'])
        elif lane is not None:
            errors.append(f"Canvas version item {item['id']} is Unplaced but still has a lane position")
    live_lane_seen={}
    for node in canvas_nodes:
        lane=node.get('lane_position')
        if lane is None: continue
        if node.get('canvas_type')!='developmental':
            errors.append(f"Canvas node {node['id']} has a Structure Lane position outside Developmental Canvas"); continue
        wanted='scene' if str(node.get('developmental_scope') or 'chapters').lower()=='scenes' else 'chapter'
        if node.get('node_type') not in {wanted,'free'}:
            errors.append(f"Canvas node {node['id']} type cannot occupy this Developmental Structure Lane"); continue
        try: pos=int(lane)
        except (TypeError,ValueError): errors.append(f"Canvas node {node['id']} has an invalid Structure Lane position"); continue
        if pos<0:
            errors.append(f"Canvas node {node['id']} has a negative Structure Lane position"); continue
        key=(int(node['canvas_id']),pos)
        if key in live_lane_seen: errors.append(f"Developmental Canvas {node['canvas_id']} has duplicate lane position {pos}")
        else: live_lane_seen[key]=int(node['id'])
    for ann in annotations:
        block_id=str(ann.get('block_id') or '')
        chapter_id=int(ann.get('chapter_id') or 0)
        if bool(block_id)==bool(chapter_id):
            errors.append(f"Annotation {ann['id']} must anchor to exactly one manuscript block or Chapter title"); continue
        if block_id:
            b=block_by.get(block_id)
            if not b: errors.append(f"Annotation {ann['id']} references missing prose"); continue
            text=str(b.get('text_content') or '')
        else:
            ch=chapter_by.get(chapter_id)
            if not ch or int(ch.get('is_implicit') or 0): errors.append(f"Annotation {ann['id']} references a missing/implicit Chapter title"); continue
            text=str(ch.get('title') or '')
        n=len(text); start=int(ann.get('start_offset') or 0); end=int(ann.get('end_offset') or 0)
        if start<0 or end<start or end>n: errors.append(f"Annotation {ann['id']} offsets are invalid")
        if ann.get('label_id') is not None and int(ann['label_id']) not in labels: errors.append(f"Annotation {ann['id']} references a missing Marker")
        if ann.get('thread_id') is not None and int(ann['thread_id']) not in threads: errors.append(f"Annotation {ann['id']} references a missing Note thread")
    return errors


def validate_project_invariants(con: sqlite3.Connection, project_id:int) -> dict:
    errors=project_invariant_errors(con,project_id)
    if errors: raise ValueError('Project integrity check failed: '+errors[0])
    return {'ok':True,'errors':[]}


def assert_project_invariants(con: sqlite3.Connection, project_id:int) -> None:
    """Test/debug assertion wrapper around the production validator."""
    result=validate_project_invariants(con,project_id)
    assert result['ok'] and not result['errors']
