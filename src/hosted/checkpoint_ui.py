"""Authorized native-shaped checkpoint metadata and read-only comparison.

Immutable stored snapshots are never rewritten by this projection. Restore/apply
remains a separate hosted protocol and is deliberately not implemented here.
"""
from copy import deepcopy
import re
from threading import BoundedSemaphore

from fastapi import HTTPException

from hosted.firestore.contracts import DocumentConflict, DocumentNotFound

_COMPARE_GATE = BoundedSemaphore(2)
_SOURCE_FIELDS = ('source_tab_id','source_tab_title','source_tab_created_at',
    'source_chapter_id','source_chapter_title','source_chapter_created_at',
    'source_chapter_position','current_source_chapter_id','current_source_chapter_created_at','word_count')


class HostedCheckpointUI:
    def __init__(self, domain, project_id, user_id, capture_project):
        self.domain,self.pid,self.uid,self.capture_project=domain,project_id,user_id,capture_project
        self.parents={'project_id':project_id}

    def generation(self):return self.domain.project_generation(self.pid,self.uid)

    def fence(self,generation):
        if self.generation()!=generation:raise DocumentConflict('HOSTED_CHECKPOINT_SOURCE_CHANGED')

    def live(self,kind,key,value):
        if not value:return None
        try:return self.domain.get(kind,{**self.parents,key:str(value)},user_id=self.uid)
        except DocumentNotFound:return None

    def projection(self,row):
        raw=deepcopy(row.get('snapshot') or {})
        if not isinstance(raw,dict):raise HTTPException(409,'HOSTED_CHECKPOINT_SNAPSHOT_INVALID')
        scope=row.get('scope') or raw.get('scope')
        meta={key:value for key,value in row.items() if key!='snapshot'}
        if scope not in {'chapter','book'}:return meta,raw
        tabs=raw.get('tabs') or raw.get('documents') or ([raw['tab']] if raw.get('tab') else [])
        chapters=raw.get('chapters') or ([raw['chapter']] if raw.get('chapter') else [])
        if scope=='chapter':
            cid=row.get('source_chapter_id') or row.get('chapter_id') or (raw.get('chapter') or {}).get('id')
            chapter=next((item for item in chapters if str(item.get('id'))==str(cid)),{})
            tab=next((item for item in tabs if str(item.get('id'))==str(chapter.get('tab_id'))),raw.get('tab') or {})
            selected={str(cid)} if cid else set()
            live=self.live('chapter','chapter_id',cid)
            captured=row.get('source_chapter_created_at') or chapter.get('created_at')
            exists=bool(chapter and live and (not captured or captured in
                {live.get('created_at'), live.get('native_created_at')}))
            meta.update(source_chapter_id=cid,source_chapter_title=row.get('source_chapter_title') or chapter.get('title',''),
                source_chapter_created_at=captured,source_chapter_position=chapter.get('position'),
                current_chapter_id=cid if exists else None,source_exists=exists,
                current_source_chapter_id=cid if exists else None,
                current_source_chapter_created_at=(live.get('native_created_at') or live.get('created_at')) if exists else None)
        else:
            tab=(raw.get('tab') or next((item for item in tabs if item.get('id')=='tab_manuscript'),None)
                or next((item for item in tabs if item.get('is_manuscript')),tabs[0] if tabs else {}))
            chapter={};chapters=[item for item in chapters if str(item.get('tab_id'))==str(tab.get('id'))]
            selected={str(item.get('id')) for item in chapters}
            live=self.live('document_tab','tab_id',tab.get('id'))
            meta.update(current_chapter_id=None,source_exists=bool(live))
        blocks=[item for item in raw.get('blocks',[]) if str(item.get('chapter_id')) in selected or (scope=='chapter' and raw.get('scope')=='chapter' and item.get('chapter_id') is None)]
        snapshot={**raw,'scope':scope,'version':raw.get('version',1),'tab':tab,'blocks':blocks}
        if scope=='chapter':snapshot['chapter']=chapter;snapshot.pop('chapters',None)
        else:snapshot['chapters']=chapters;snapshot.pop('chapter',None)
        meta.update(scope=scope,source_tab_id=tab.get('id'),source_tab_title=row.get('source_tab_title') or tab.get('title',''),
            source_tab_created_at=row.get('source_tab_created_at') or tab.get('created_at'),
            word_count=len(re.findall(r"[\w’'-]+",' '.join(str(item.get('text_content') or '') for item in blocks),re.UNICODE)))
        default=(meta.get('source_chapter_title') if scope=='chapter' else meta.get('source_tab_title')) or ('Chapter' if scope=='chapter' else 'Book')
        meta.update(default_name=default,display_name=str(meta.get('name') or '').strip() or default)
        return meta,snapshot

    def create_payload(self,body):
        generation=self.generation()
        raw=body.get('snapshot') if 'snapshot' in body else self.capture_project(self.pid)
        payload={**body,'snapshot':raw}
        meta,_=self.projection(payload)
        if body.get('scope')=='chapter' and not meta.get('source_exists'):
            raise HTTPException(404,'HOSTED_CHECKPOINT_SOURCE_NOT_FOUND')
        self.fence(generation)
        return {**payload,**{key:meta[key] for key in _SOURCE_FIELDS if key in meta}}

    def list(self,query):
        generation=self.generation();rows=self.domain.list('checkpoint',self.parents,user_id=self.uid)
        needle=str(query.get('q') or '').strip().casefold();scope=query.get('scope');chapter=query.get('chapter_id')
        result=[]
        for row in rows:
            meta,_=self.projection(row)
            if scope in {'chapter','book'} and meta.get('scope')!=scope:continue
            if chapter is not None and (meta.get('scope')!='chapter' or str(meta.get('current_chapter_id'))!=str(chapter)):continue
            text=' '.join(str(meta.get(key) or '') for key in ('display_name','description','source_chapter_title','source_tab_title')).casefold()
            if needle and needle not in text:continue
            result.append(meta)
        self.fence(generation)
        return sorted(result,key=lambda row:(str(row.get('created_at') or ''),str(row.get('id') or '')),reverse=True)

    def record(self,cid):
        row=self.domain.get('checkpoint',{**self.parents,'checkpoint_id':cid},user_id=self.uid)
        return self.projection(row)

    def get(self,cid):
        generation=self.generation();meta,snapshot=self.record(cid);self.fence(generation)
        return {**meta,'checkpoint':meta,'snapshot':snapshot}

    def compare(self,left,right):
        if left=='current' and right=='current':raise HTTPException(400,'Choose at least one checkpoint')
        generation=self.generation()
        saved={ref:self.record(ref) for ref in {left,right} if ref!='current'}
        def resolve(ref,other):
            if ref!='current':return saved[ref]
            seed=saved[other][0]
            if not seed.get('source_exists'):raise HTTPException(409,'HOSTED_CHECKPOINT_SOURCE_NOT_FOUND')
            raw=self.capture_project(self.pid)
            return self.projection({'scope':seed.get('scope'),'chapter_id':seed.get('current_chapter_id'),'snapshot':raw,'name':'Current'})
        lm,ls=resolve(left,right);rm,rs=resolve(right,left)
        if ls.get('scope')!=rs.get('scope'):raise HTTPException(400,'Compare checkpoints with the same scope')
        if ls.get('scope')=='chapter' and (lm.get('source_chapter_id'),lm.get('source_chapter_created_at'))!=(rm.get('source_chapter_id'),rm.get('source_chapter_created_at')):
            raise HTTPException(400,'Compare checkpoints from the same chapter lineage')
        self.fence(generation)
        # Reuse the canonical pure renderer, never its SQLite route/capture paths.
        from formats.checkpoint_comparison import _checkpoint_compare_rows, _checkpoint_units
        source_rows=[*ls.get('blocks',[]),*ls.get('chapters',[]),*rs.get('blocks',[]),*rs.get('chapters',[])]
        if len(source_rows)>4000:
            raise HTTPException(413,'HOSTED_CHECKPOINT_COMPARE_TOO_LARGE')
        if sum(sum(len(str(row.get(key) or '')) for key in ('text_content','title','image_path','image_footer','image_description')) for row in source_rows)>800_000:
            raise HTTPException(413,'HOSTED_CHECKPOINT_COMPARE_TOO_LARGE')
        units=[*_checkpoint_units(ls),*_checkpoint_units(rs)]
        # Bound both the unit matcher and per-paragraph word matcher. A too-large
        # comparison remains available as a read-only reference; never truncate it.
        if len(units)>4000 or sum(len(str(unit.get('text') or '')) for unit in units)>800_000:
            raise HTTPException(413,'HOSTED_CHECKPOINT_COMPARE_TOO_LARGE')
        cost=sum(len(re.findall(r"\s+|[\w’'-]+|[^\w\s]",str(unit.get('text') or ''),re.UNICODE))**2 for unit in units)
        if cost>4_000_000:raise HTTPException(413,'HOSTED_CHECKPOINT_COMPARE_TOO_LARGE')
        if not _COMPARE_GATE.acquire(blocking=False):raise HTTPException(429,'HOSTED_CHECKPOINT_COMPARE_BUSY')
        try:result=_checkpoint_compare_rows(ls,rs)
        finally:_COMPARE_GATE.release()
        self.fence(generation)
        return {**result,'left_name':lm.get('display_name') or lm.get('name'),'right_name':rm.get('display_name') or rm.get('name'),'scope':ls.get('scope')}
