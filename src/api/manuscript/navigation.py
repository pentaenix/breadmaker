from __future__ import annotations

"""Manuscript navigation projections and bookmark payloads."""

from backend.data_access import allrows, one

def _manuscript_navigation_projection(con, pid:int) -> tuple[list[dict],list[dict]]:
    """Build Scene navigation metadata and unassigned gaps from one lightweight scan.

    Startup used to scan full manuscript payloads for chapter revisions *and* run six
    correlated manuscript queries per Scene, then scan the manuscript again for gaps.
    The interactive editor does not need chapter revision hashes until a chapter/book
    is actually hydrated.  Keep bootstrap proportional to navigation metadata instead:
    one ordered block projection supplies both Scene aggregates and gap rows.
    """
    scenes=allrows(con,"SELECT * FROM scenes WHERE project_id=? ORDER BY tab_id,position,id",(pid,))
    blocks=allrows(con,"""SELECT b.id,b.tab_id,b.chapter_id,b.scene_id,
            CASE WHEN b.scene_id IS NULL AND b.block_type='paragraph' THEN b.text_content ELSE '' END text_content,
            b.block_type,t.position tab_position,c.position chapter_position,b.position
        FROM manuscript_blocks b
        JOIN chapters c ON c.id=b.chapter_id
        JOIN document_tabs t ON t.id=b.tab_id
        WHERE b.project_id=?
        ORDER BY t.position,c.position,b.position,b.id""",(pid,))

    scene_stats={}
    for row in blocks:
        sid=row.get('scene_id')
        if sid is None:continue
        sid=int(sid);stats=scene_stats.get(sid)
        if stats is None:
            stats={'first':row,'last':row,'chapters':{int(row['chapter_id'])},'count':1};scene_stats[sid]=stats
        else:
            stats['last']=row;stats['chapters'].add(int(row['chapter_id']));stats['count']+=1
    for scene in scenes:
        stats=scene_stats.get(int(scene['id']));first=stats['first'] if stats else None;last=stats['last'] if stats else None
        scene.update({
            'first_block_id':first.get('id') if first else None,
            'first_chapter_id':first.get('chapter_id') if first else None,
            'first_tab_id':first.get('tab_id') if first else scene.get('tab_id'),
            'last_block_id':last.get('id') if last else None,
            'last_chapter_id':last.get('chapter_id') if last else None,
            'chapter_count':len(stats['chapters']) if stats else 0,
            'block_count':int(stats['count']) if stats else 0,
        })

    gaps=[];i=0
    while i<len(blocks):
        if blocks[i].get('scene_id') is not None:i+=1;continue
        start=i;tab_id=blocks[i]['tab_id']
        while i+1<len(blocks) and blocks[i+1]['tab_id']==tab_id and blocks[i+1].get('scene_id') is None:i+=1
        end=i;rows=blocks[start:end+1]
        before=None;j=start-1
        while j>=0 and blocks[j]['tab_id']==tab_id:
            if blocks[j].get('scene_id') is not None:before=int(blocks[j]['scene_id']);break
            j-=1
        after=None;j=end+1
        while j<len(blocks) and blocks[j]['tab_id']==tab_id:
            if blocks[j].get('scene_id') is not None:after=int(blocks[j]['scene_id']);break
            j+=1
        text=' '.join((row.get('text_content') or '') for row in rows if row.get('block_type')=='paragraph').strip()
        gaps.append({'tab_id':tab_id,'first_block_id':rows[0]['id'],'last_block_id':rows[-1]['id'],'block_ids':[row['id'] for row in rows],'first_chapter_id':rows[0]['chapter_id'],'before_scene_id':before,'after_scene_id':after,'block_count':len(rows),'word_count':len(text.split()) if text else 0})
        i+=1
    return scenes,gaps


def _scene_gap_rows(con, pid:int) -> list[dict]:
    """Compatibility helper for callers that only need derived Scene gaps."""
    return _manuscript_navigation_projection(con,pid)[1]

def _bookmark_payload(con, pid:int):
    row=one(con,"SELECT * FROM bookmarks WHERE project_id=?",(pid,))
    if not row: return None
    kind=row.get('target_type')
    if kind=='chapter':
        ch=one(con,"SELECT id,tab_id,title,is_implicit FROM chapters WHERE id=? AND project_id=?",(row.get('chapter_id'),pid))
        if not ch or ch.get('is_implicit'): return None
        # A structural bookmark still has a concrete manuscript location. Project
        # that location into the orthogonal Scene outline when the Chapter begins
        # inside a Scene, so the single ribbon stays discoverable from either
        # navigation mode.
        first=one(con,"SELECT id,scene_id FROM manuscript_blocks WHERE chapter_id=? AND project_id=? ORDER BY position,id LIMIT 1",(ch['id'],pid))
        row.update({'tab_id':ch['tab_id'],'owner_chapter_id':ch['id'],'owner_scene_id':first.get('scene_id') if first else None,'location_block_id':first.get('id') if first else None,'target_title':ch['title']})
    elif kind=='scene':
        sc=one(con,"SELECT id,tab_id,title FROM scenes WHERE id=? AND project_id=?",(row.get('scene_id'),pid))
        if not sc: return None
        first=one(con,"SELECT id,chapter_id FROM manuscript_blocks WHERE scene_id=? AND project_id=? ORDER BY position,id LIMIT 1",(sc['id'],pid))
        row.update({'tab_id':sc['tab_id'],'owner_chapter_id':first.get('chapter_id') if first else None,'owner_scene_id':sc['id'],'location_block_id':first.get('id') if first else None,'target_title':sc['title']})
    elif kind=='range':
        block=one(con,"SELECT id,tab_id,chapter_id,scene_id,text_content FROM manuscript_blocks WHERE id=? AND project_id=?",(row.get('start_block_id'),pid))
        end=one(con,"SELECT id,tab_id,chapter_id FROM manuscript_blocks WHERE id=? AND project_id=?",(row.get('end_block_id'),pid))
        if not block or not end or int(block.get('tab_id') or 0)!=int(end.get('tab_id') or 0): return None
        row.update({'tab_id':block['tab_id'],'owner_chapter_id':block['chapter_id'],'owner_scene_id':block['scene_id'],'location_block_id':block['id'],'target_title':''})
    else: return None
    return row
