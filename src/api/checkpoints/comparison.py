from __future__ import annotations

"""Checkpoint read-only comparison and current-version projection."""

import difflib
import html
import json
import re

from fastapi import APIRouter, HTTPException

from backend.data_access import project
from storage import mutations as storage_mutations
from . import capture, lineage

router = APIRouter()

def _checkpoint_image_width(value) -> int:
    try: return max(5,min(100,int(value or 100)))
    except (TypeError,ValueError): return 100


def _checkpoint_image_unit(block:dict) -> dict:
    path=str(block.get('image_path') or '').strip()
    render_path=str(block.get('_render_image_path') or path).strip()
    width=_checkpoint_image_width(block.get('image_width'))
    footer=str(block.get('image_footer') or '')
    description=str(block.get('image_description') or '')
    row_id=str(block.get('image_row_id') or '')
    # Comparison identity is deliberately occurrence-level. The same asset resized,
    # recaptioned, or redescribed is a meaningful manuscript change.
    identity=json.dumps([path,width,footer,description,row_id],ensure_ascii=False,separators=(',',':'))
    safe_path=html.escape(render_path,quote=True); safe_description=html.escape(description,quote=True)
    caption=f'<figcaption>{html.escape(footer)}</figcaption>' if footer else ''
    rendered=(f'<figure class="checkpoint-image-figure" style="--checkpoint-image-width:{width}%">'
              f'<img class="checkpoint-image" src="{safe_path}" alt="{safe_description}">{caption}</figure>')
    return {'text':identity,'compare_key':'image:'+identity,'style':'image','kind':'image','html':rendered}


def _checkpoint_units(snapshot:dict) -> list[dict]:
    units=[]
    def text_unit(text,style):
        value=str(text or '')
        return {'text':value,'compare_key':'text:'+value,'style':str(style or 'body'),'kind':'text','html':html.escape(value)}
    def image_row_unit(blocks):
        image_units=[_checkpoint_image_unit(block) for block in blocks]
        width=_checkpoint_image_width(blocks[0].get('image_width'))
        identity=json.dumps([unit['compare_key'] for unit in image_units],ensure_ascii=False,separators=(',',':'))
        rendered=f'<div class="readonly-image-row checkpoint-image-row" style="width:{width}%">'+''.join(unit['html'] for unit in image_units)+'</div>'
        return {'text':identity,'compare_key':'image_row:'+identity,'style':'image','kind':'image','html':rendered}
    def append_blocks(blocks):
        ordered=sorted(blocks,key=lambda x:(x.get('position',0),str(x.get('id',''))));i=0
        while i<len(ordered):
            block=ordered[i]
            if block.get('block_type')=='image':
                row_id=str(block.get('image_row_id') or '').strip()
                if row_id:
                    group=[block];j=i+1
                    while j<len(ordered) and ordered[j].get('block_type')=='image' and str(ordered[j].get('image_row_id') or '').strip()==row_id:
                        group.append(ordered[j]);j+=1
                    if 2<=len(group)<=4:
                        units.append(image_row_unit(group));i=j;continue
                units.append(_checkpoint_image_unit(block))
            else: units.append(text_unit(block.get('text_content'),block.get('semantic_style') or 'body'))
            i+=1
    if snapshot.get('scope')=='chapter':
        if snapshot.get('chapter'):
            units.append(text_unit((snapshot.get('chapter') or {}).get('title') or 'Untitled Chapter','chapter'))
        append_blocks(snapshot.get('blocks',[]))
    else:
        blocks_by={}
        for b in snapshot.get('blocks',[]): blocks_by.setdefault(int(b.get('chapter_id') or 0),[]).append(b)
        for ch in sorted(snapshot.get('chapters',[]),key=lambda x:(x.get('position',0),x.get('id',0))):
            units.append(text_unit(ch.get('title') or 'Untitled Chapter','chapter'));append_blocks(blocks_by.get(int(ch['id']),[]))
    return units


def _checkpoint_unit_render(unit:dict,change_kind:str='same') -> str:
    if unit.get('kind')=='image':
        rendered=str(unit.get('html') or '')
        if change_kind!='same':
            marker='checkpoint-diff-added-figure' if change_kind=='added' else ('checkpoint-diff-removed-figure' if change_kind=='removed' else 'checkpoint-diff-changed-figure')
            rendered=rendered.replace('class="checkpoint-image-figure"',f'class="checkpoint-image-figure {marker}"',1)
        return rendered
    text=html.escape(str(unit.get('text') or ''))
    if change_kind=='removed': return f'<mark class="checkpoint-diff-removed">{text}</mark>'
    if change_kind=='added': return f'<mark class="checkpoint-diff-added">{text}</mark>'
    return text


def _word_diff_pair(left:str,right:str) -> tuple[str,str]:
    token_re=re.compile(r"\s+|[\w’'-]+|[^\w\s]",re.UNICODE); a=token_re.findall(left or ''); b=token_re.findall(right or '')
    matcher=difflib.SequenceMatcher(a=a,b=b,autojunk=False); lout=[]; rout=[]
    for op,i1,i2,j1,j2 in matcher.get_opcodes():
        la=html.escape(''.join(a[i1:i2])); rb=html.escape(''.join(b[j1:j2]))
        if op=='equal': lout.append(la); rout.append(rb)
        elif op=='delete': lout.append(f'<mark class="checkpoint-diff-removed">{la}</mark>')
        elif op=='insert': rout.append(f'<mark class="checkpoint-diff-added">{rb}</mark>')
        else:
            lout.append(f'<mark class="checkpoint-diff-removed">{la}</mark>'); rout.append(f'<mark class="checkpoint-diff-added">{rb}</mark>')
    return ''.join(lout),''.join(rout)


def _checkpoint_compare_rows(left:dict,right:dict) -> dict:
    a=_checkpoint_units(left); b=_checkpoint_units(right); matcher=difflib.SequenceMatcher(a=[u['compare_key'] for u in a],b=[u['compare_key'] for u in b],autojunk=False)
    rows=[]; changes=0
    for op,i1,i2,j1,j2 in matcher.get_opcodes():
        if op=='equal':
            for la,rb in zip(a[i1:i2],b[j1:j2]): rows.append({'kind':'same','left':_checkpoint_unit_render(la),'right':_checkpoint_unit_render(rb),'left_style':la['style'],'right_style':rb['style']})
            continue
        changes+=max(i2-i1,j2-j1)
        if op=='delete':
            for la in a[i1:i2]: rows.append({'kind':'removed','left':_checkpoint_unit_render(la,'removed'),'right':'','left_style':la['style'],'right_style':'body'})
        elif op=='insert':
            for rb in b[j1:j2]: rows.append({'kind':'added','left':'','right':_checkpoint_unit_render(rb,'added'),'left_style':'body','right_style':rb['style']})
        else:
            lefts=a[i1:i2]; rights=b[j1:j2]; n=max(len(lefts),len(rights))
            for k in range(n):
                la=lefts[k] if k<len(lefts) else {'text':'','compare_key':'text:','style':'body','kind':'text','html':''}; rb=rights[k] if k<len(rights) else {'text':'','compare_key':'text:','style':'body','kind':'text','html':''}
                if la.get('kind')=='image' or rb.get('kind')=='image':
                    lh=_checkpoint_unit_render(la,'changed') if la.get('text') or la.get('kind')=='image' else ''
                    rh=_checkpoint_unit_render(rb,'changed') if rb.get('text') or rb.get('kind')=='image' else ''
                else:
                    lh,rh=_word_diff_pair(la['text'],rb['text'])
                rows.append({'kind':'changed','left':lh,'right':rh,'left_style':la['style'],'right_style':rb['style']})
    return {'rows':rows,'change_count':changes}

def _checkpoint_version_snapshot(con,pid:int,ref:str,other:dict|None=None,other_row:dict|None=None) -> tuple[dict,str,bool]:
    if ref=='current':
        if not other: raise HTTPException(400,"Current comparison needs a checkpoint context")
        if other.get('scope')=='chapter':
            current_id=int((lineage._checkpoint_meta(con,other_row).get('current_chapter_id') if other_row else 0) or 0) or lineage._checkpoint_live_chapter_id_for_snapshot(con,pid,other)
            if current_id is None: raise HTTPException(409,"The source chapter no longer exists in the current manuscript")
            return capture._capture_chapter_checkpoint(con,pid,current_id),'Current',True
        return capture._capture_book_checkpoint(con,pid),'Current',True
    try: cid=int(ref)
    except ValueError: raise HTTPException(400,"Invalid comparison version")
    row=lineage._checkpoint_row(con,pid,cid); snap=capture._decode_checkpoint_snapshot(con,row); meta=lineage._checkpoint_meta(con,row)
    label=meta['display_name']
    return snap,label,meta['source_exists']

@router.get("/api/checkpoints/compare/versions")
def compare_checkpoints(left:str,right:str):
    if left=='current' and right=='current': raise HTTPException(400,"Choose at least one checkpoint")
    with storage_mutations.tx() as con:
        p=project(con); left_seed=None; right_seed=None
        try:
            left_row=lineage._checkpoint_row(con,p['id'],int(left)) if left!='current' else None; right_row=lineage._checkpoint_row(con,p['id'],int(right)) if right!='current' else None
        except ValueError:
            raise HTTPException(400,"Invalid comparison version")
        if left_row: left_seed=capture._decode_checkpoint_snapshot(con,left_row)
        if right_row: right_seed=capture._decode_checkpoint_snapshot(con,right_row)
        if left_seed and right_seed:
            if left_seed.get('scope')!=right_seed.get('scope'): raise HTTPException(400,"Compare checkpoints with the same scope")
            if left_seed.get('scope')=='chapter' and not lineage._checkpoint_rows_same_chapter_lineage(left_row,right_row): raise HTTPException(400,"Compare checkpoints from the same chapter lineage")
        l,lname,_=_checkpoint_version_snapshot(con,p['id'],left,right_seed,right_row); r,rname,_=_checkpoint_version_snapshot(con,p['id'],right,left_seed or l,left_row)
        if l.get('scope')!=r.get('scope'): raise HTTPException(400,"Compare checkpoints with the same scope")
        result=_checkpoint_compare_rows(l,r); result.update({'left_name':lname,'right_name':rname,'scope':l.get('scope')}); return result
