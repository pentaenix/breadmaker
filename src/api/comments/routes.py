from __future__ import annotations

"""Comments API/domain routes.

Extracted mechanically from the pre-10.1 composition module. Route behavior and
function names are intentionally unchanged; dependencies are declared explicitly below; cross-feature calls resolve to their
canonical owner lazily to avoid import cycles.
"""

from fastapi import APIRouter

from backend.dependencies import feature_call
from fastapi import HTTPException
from storage import mutations as storage_mutations
from storage import values as storage_values
from .contracts import CommentCreate, CommentMessageCreate, CommentMessageUpdate, CommentThreadUpdate
from backend.data_access import (
    allrows,
    one,
    project,
)

# Explicit composition-root dependencies.

# Explicit cycle-safe calls to canonical feature owners.
_clean_comment_body = feature_call('annotations.routes', '_clean_comment_body')
_comment_payload = feature_call('annotations.routes', '_comment_payload')
_create_comment_thread = feature_call('annotations.routes', '_create_comment_thread')

router = APIRouter()

# ---------------- Comments ----------------
@router.post('/api/comments')
def create_comment(body:CommentCreate):
    with storage_mutations.tx() as con:
        p=project(con); result=_create_comment_thread(con,p['id'],body.items,body.body)
        return _comment_payload(con,p['id'],one(con,'SELECT * FROM comment_threads WHERE id=?',(result['thread_id'],)))

@router.get('/api/comments')
def list_comments(status:str='open',q:str=''):
    if status not in {'open','resolved','all'}: raise HTTPException(400,'Note status must be open, resolved, or all')
    with storage_mutations.tx() as con:
        p=project(con); rows=allrows(con,"SELECT * FROM comment_threads WHERE project_id=? ORDER BY id",(p['id'],)); out=[]; needle=(q or '').strip().casefold()
        for row in rows:
            if status!='all' and row.get('status')!=status: continue
            payload=_comment_payload(con,p['id'],row)
            if not payload['anchors']: continue
            hay=' '.join([payload.get('first_message',''),payload.get('excerpt',''),payload.get('chapter_title',''),payload.get('scene_title',''),*(m.get('body','') for m in payload.get('messages',[]))]).casefold()
            if needle and needle not in hay: continue
            out.append(payload)
        def order_key(x):
            a=(x.get('anchors') or [{}])[0]
            return (int(a.get('tab_position') or 0),int(a.get('chapter_position') or 0),float(a.get('block_position') or 0),int(a.get('start_offset') or 0),int(x.get('id') or 0))
        out.sort(key=order_key); return out

@router.get('/api/comments/{tid}')
def get_comment(tid:int):
    with storage_mutations.tx() as con:
        p=project(con); row=one(con,'SELECT * FROM comment_threads WHERE id=? AND project_id=?',(tid,p['id']))
        if not row: raise HTTPException(404,'Note thread not found')
        return _comment_payload(con,p['id'],row)

@router.patch('/api/comments/{tid}')
def update_comment_thread(tid:int,body:CommentThreadUpdate):
    vals=body.model_dump(exclude_unset=True)
    if 'status' in vals and vals['status'] not in {'open','resolved'}: raise HTTPException(400,'Note status must be open or resolved')
    with storage_mutations.tx() as con:
        p=project(con); row=one(con,'SELECT id FROM comment_threads WHERE id=? AND project_id=?',(tid,p['id']))
        if not row: raise HTTPException(404,'Note thread not found')
        if 'status' in vals:
            t=storage_values.now();con.execute('UPDATE comment_threads SET status=?,updated_at=? WHERE id=?',(vals['status'],t,tid));con.execute("UPDATE annotations SET status=?,updated_at=? WHERE thread_id=?",(vals['status'],t,tid))
        return _comment_payload(con,p['id'],one(con,'SELECT * FROM comment_threads WHERE id=?',(tid,)))

def _delete_comment_thread_for_project(con,project_id:int,tid:int):
    row=one(con,'SELECT id FROM comment_threads WHERE id=? AND project_id=?',(tid,project_id))
    if not row: raise HTTPException(404,'Note thread not found')
    # annotations.thread_id intentionally predates a foreign-key relationship.
    # Remove anchors first; the cleanup trigger may remove the now-empty thread,
    # while the explicit deletes make the operation correct even without it.
    con.execute('DELETE FROM annotations WHERE project_id=? AND thread_id=?',(project_id,tid))
    con.execute('DELETE FROM comment_messages WHERE project_id=? AND thread_id=?',(project_id,tid))
    con.execute('DELETE FROM comment_threads WHERE project_id=? AND id=?',(project_id,tid))
    return {'ok':True,'id':tid}

@router.delete('/api/comments/{tid}')
def delete_comment_thread(tid:int):
    with storage_mutations.tx() as con:
        p=project(con); return _delete_comment_thread_for_project(con,p['id'],tid)

@router.post('/api/comments/{tid}/delete')
def delete_comment_thread_action(tid:int):
    # The UI uses an explicit action route. This avoids DELETE-method handling
    # differences in launchers/proxies while preserving the same journalled mutation.
    with storage_mutations.tx() as con:
        p=project(con); return _delete_comment_thread_for_project(con,p['id'],tid)

@router.post('/api/comments/{tid}/messages')
def reply_to_comment(tid:int,body:CommentMessageCreate):
    text=_clean_comment_body(body.body)
    if not text: raise HTTPException(400,'Response cannot be empty')
    if len(text)>12000: raise HTTPException(400,'Response is too long')
    with storage_mutations.tx() as con:
        p=project(con); thread=one(con,'SELECT id FROM comment_threads WHERE id=? AND project_id=?',(tid,p['id']))
        if not thread: raise HTTPException(404,'Note thread not found')
        t=storage_values.now();con.execute('INSERT INTO comment_messages(project_id,thread_id,body,created_at,updated_at) VALUES(?,?,?,?,?)',(p['id'],tid,text,t,t));con.execute('UPDATE comment_threads SET updated_at=? WHERE id=?',(t,tid))
        return _comment_payload(con,p['id'],one(con,'SELECT * FROM comment_threads WHERE id=?',(tid,)))

@router.patch('/api/comment-messages/{mid}')
def update_comment_message(mid:int,body:CommentMessageUpdate):
    text=_clean_comment_body(body.body)
    if not text: raise HTTPException(400,'Note cannot be empty')
    if len(text)>12000: raise HTTPException(400,'Note is too long')
    with storage_mutations.tx() as con:
        p=project(con); msg=one(con,'SELECT * FROM comment_messages WHERE id=? AND project_id=?',(mid,p['id']))
        if not msg: raise HTTPException(404,'Note message not found')
        t=storage_values.now();con.execute('UPDATE comment_messages SET body=?,updated_at=? WHERE id=?',(text,t,mid));con.execute('UPDATE comment_threads SET updated_at=? WHERE id=?',(t,msg['thread_id']))
        first=one(con,'SELECT id FROM comment_messages WHERE thread_id=? ORDER BY id LIMIT 1',(msg['thread_id'],))
        if first and int(first['id'])==mid: con.execute("UPDATE annotations SET body=?,updated_at=? WHERE thread_id=?",(text,t,msg['thread_id']))
        return _comment_payload(con,p['id'],one(con,'SELECT * FROM comment_threads WHERE id=?',(msg['thread_id'],)))

