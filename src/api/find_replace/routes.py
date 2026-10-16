from __future__ import annotations

"""Find Replace API/domain routes.

Extracted mechanically from the pre-10.1 composition module. Route behavior and
function names are intentionally unchanged; dependencies are declared explicitly below; cross-feature calls resolve to their
canonical owner lazily to avoid import cycles.
"""

from fastapi import APIRouter

from backend.dependencies import feature_call
from bs4 import BeautifulSoup
from fastapi import HTTPException
from bs4 import NavigableString
import re
from storage import mutations as storage_mutations
from storage import values as storage_values
from .contracts import FindRequest, ReplaceRequest
from backend.content import (
    _html_from_text,
    sanitize_inline_html,
)
from backend.data_access import (
    allrows,
    one,
    project,
)
from backend.history_runtime import commit

# Explicit composition-root dependencies.

# Explicit cycle-safe calls to canonical feature owners.
reanchor_block_annotations = feature_call('manuscript.anchors', 'reanchor_block_annotations')
reanchor_block_bookmark = feature_call('manuscript.anchors', 'reanchor_block_bookmark')

router = APIRouter()

# ---------------- Find / replace ----------------
def pattern_for(q:str,case:bool,whole:bool):
    pat=re.escape(q); pat=(r'(?<!\w)'+pat+r'(?!\w)') if whole else pat; return re.compile(pat,0 if case else re.I)

def replace_in_html(fragment:str,rx,replacement:str)->str:
    soup=BeautifulSoup(fragment or '', 'html.parser')
    # Fragment may be plain text. BeautifulSoup still exposes text nodes.
    for node in list(soup.descendants):
        if isinstance(node,NavigableString) and node.parent and node.parent.name not in {'script','style'}:
            new=rx.sub(lambda _m: replacement,str(node))
            if new!=str(node): node.replace_with(new)
    return str(soup)

@router.post("/api/find")
def find_text(body:FindRequest):
    if not body.query: raise HTTPException(400,"Find text cannot be empty")
    if len(body.query)>10000: raise HTTPException(400,"Find text is too long")
    rx=pattern_for(body.query,body.case_sensitive,body.whole_word)
    with storage_mutations.tx() as con:
        p=project(con); q="SELECT b.*,CASE WHEN c.is_implicit=1 THEN '' ELSE c.title END chapter_title,s.title scene_title FROM manuscript_blocks b JOIN chapters c ON c.id=b.chapter_id LEFT JOIN scenes s ON s.id=b.scene_id WHERE b.project_id=?"; args=[p['id']]
        if body.scope=='chapter' and body.chapter_id:
            ch=one(con,"SELECT id FROM chapters WHERE id=? AND project_id=?",(body.chapter_id,p['id']))
            if not ch: raise HTTPException(400,"Find chapter no longer exists")
            q+=' AND b.chapter_id=?'; args.append(body.chapter_id)
        elif body.scope=='scene' and body.scene_id:
            scene=one(con,"SELECT id FROM scenes WHERE id=? AND project_id=?",(body.scene_id,p['id']))
            if not scene: raise HTTPException(400,"Find Scene no longer exists")
            q+=' AND b.scene_id=?'; args.append(body.scene_id)
        else:
            tab_id=body.tab_id
            if tab_id is None:
                row=con.execute("SELECT id FROM document_tabs WHERE project_id=? AND is_manuscript=1 ORDER BY position,id LIMIT 1",(p['id'],)).fetchone(); tab_id=row[0] if row else None
            tab=one(con,"SELECT id FROM document_tabs WHERE id=? AND project_id=?",(tab_id,p['id'])) if tab_id is not None else None
            if not tab: raise HTTPException(400,"Find source tab no longer exists")
            q+=' AND b.tab_id=?'; args.append(tab_id)
        q+=' ORDER BY c.position,b.position'; rows=allrows(con,q,args)
    hits=[]
    for r in rows:
        for m in rx.finditer(r['text_content'] or ''):
            text=r['text_content'] or ''; hits.append({"block_id":r['id'],"chapter_id":r['chapter_id'],"chapter_title":r['chapter_title'],"scene_title":r['scene_title'],"start":m.start(),"end":m.end(),"match":m.group(0),"context":text[max(0,m.start()-45):min(len(text),m.end()+70)]})
    return {"count":len(hits),"hits":hits[:5000]}

@router.post("/api/replace")
def replace_text(body:ReplaceRequest):
    if not body.query: raise HTTPException(400,"Find text cannot be empty")
    if len(body.query)>10000 or len(body.replacement)>100000: raise HTTPException(400,"Find/replace text is too long")
    rx=pattern_for(body.query,body.case_sensitive,body.whole_word)
    with storage_mutations.tx() as con:
        p=project(con); q="SELECT b.*,CASE WHEN c.is_implicit=1 THEN '' ELSE c.title END chapter_title FROM manuscript_blocks b JOIN chapters c ON c.id=b.chapter_id WHERE b.project_id=?"; args=[p['id']]
        if body.scope=='chapter' and body.chapter_id:
            ch=one(con,"SELECT id FROM chapters WHERE id=? AND project_id=?",(body.chapter_id,p['id']))
            if not ch: raise HTTPException(400,"Replace chapter no longer exists")
            q+=' AND b.chapter_id=?'; args.append(body.chapter_id)
        elif body.scope=='scene' and body.scene_id:
            scene=one(con,"SELECT id FROM scenes WHERE id=? AND project_id=?",(body.scene_id,p['id']))
            if not scene: raise HTTPException(400,"Replace Scene no longer exists")
            q+=' AND b.scene_id=?'; args.append(body.scene_id)
        else:
            tab_id=body.tab_id
            if tab_id is None:
                row=con.execute("SELECT id FROM document_tabs WHERE project_id=? AND is_manuscript=1 ORDER BY position,id LIMIT 1",(p['id'],)).fetchone(); tab_id=row[0] if row else None
            tab=one(con,"SELECT id FROM document_tabs WHERE id=? AND project_id=?",(tab_id,p['id'])) if tab_id is not None else None
            if not tab: raise HTTPException(400,"Replace source tab no longer exists")
            q+=' AND b.tab_id=?'; args.append(tab_id)
        rows=allrows(con,q,args)
        affected=[]; total=0
        for r in rows:
            count=len(rx.findall(r['text_content'] or ''))
            if count: affected.append({"block_id":r['id'],"chapter_id":r['chapter_id'],"chapter_title":r['chapter_title'],"count":count,"context":(r['text_content'] or '')[:160]}); total+=count
        if body.preview: return {"count":total,"affected":affected[:1000]}
    commit(f"Before Replace All: {body.query}","safety")
    with storage_mutations.tx() as con:
        for a in affected:
            r=one(con,"SELECT * FROM manuscript_blocks WHERE id=?",(a['block_id'],)); newtext=rx.sub(lambda _m: body.replacement,r['text_content'] or ''); newhtml=replace_in_html(r['content_html'] or _html_from_text(r['text_content'] or ''),rx,body.replacement); newhtml=sanitize_inline_html(newhtml,newtext); reanchor_block_annotations(con,r['id'],newtext); reanchor_block_bookmark(con,r['id'],newtext); con.execute("UPDATE manuscript_blocks SET text_content=?,content_html=?,content_hash=?,updated_at=? WHERE id=?",(newtext,newhtml,storage_values.block_content_hash(newhtml,newtext,''),storage_values.now(),r['id']))
    commit(f"Replace All: {body.query} → {body.replacement} ({total})","replace"); return {"ok":True,"count":total}

