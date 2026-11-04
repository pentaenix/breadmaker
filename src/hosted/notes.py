"""Hosted Notes: native thread payloads and bounded, journalled atomic mutations.

Threads, messages and manuscript anchors share one project-generation commit.
Local SQLite routes are never imported; private-codec activation stays fail closed.
"""
from copy import deepcopy
import hashlib
import json

from fastapi import HTTPException
from hosted.domain_store import _public, operation_id
from hosted.firestore.contracts import DocumentConflict, DocumentNotFound, HostedValidationError
from hosted.firestore.ids import require_project_id, require_user_id, require_opaque_id
from hosted.firestore.repositories import render_model_path
from hosted.idempotency import operation_for, stable_entity_id

_MAX_ROWS = 90  # Preserve room for root, receipt and the existing Undo journal.


def _body(value):
    if not isinstance(value, str):raise HTTPException(400, 'Note must contain text')
    text=value.replace('\r\n','\n').replace('\r','\n').strip()
    if not text:raise HTTPException(400, 'Note cannot be empty')
    if len(text)>12000:raise HTTPException(400, 'Note is too long')
    return text


class HostedNotes:
    def __init__(self, domain, project_id, user_id):
        self.domain=domain
        self.pid,self.uid=require_project_id(project_id),require_user_id(user_id)
        if domain.encrypted_fields is not None or domain.encrypted_objects is not None:
            raise HostedValidationError('HOSTED_PRIVATE_NOTES_NOT_READY')

    def path(self, kind, eid, tid=None):
        field={'comment_thread':'thread_id','comment_message':'message_id','annotation':'annotation_id'}[kind]
        ids={'project_id':self.pid,field:require_opaque_id(eid,label=field)}
        if tid is not None:ids['thread_id']=require_opaque_id(tid,label='thread_id')
        return render_model_path(self.domain._model(kind),**ids)

    def authorize(self,tx,mutation=False):
        return self.domain._authorize_and_project(tx,self.domain._model('comment_thread'),
            {'project_id':self.pid,'thread_id':'notes_authority'},self.uid,mutation=mutation)

    def rows(self,tx,kind,tid=None):
        parents={'project_id':self.pid}
        if kind=='comment_message':parents['thread_id']=tid
        path,_=self.domain._collection_path(kind,parents)
        kwargs={'field':'thread_id','value':tid} if kind=='annotation' else {}
        rows=[(row.path,dict(row.data)) for row in tx.query_collection(path,**kwargs)]
        return rows

    def thread_rows(self,tx,tid):
        path=self.path('comment_thread',tid);row=tx.get(path)
        if row is None:raise DocumentNotFound('HOSTED_DOCUMENT_NOT_FOUND')
        messages=self.rows(tx,'comment_message',tid)
        anchors=self.rows(tx,'annotation',tid)
        return path,dict(row.data),messages,anchors

    def source(self,kind,eid,*,metadata=False):
        if not eid:return None
        key={'manuscript_block':'block_id','chapter':'chapter_id','scene':'scene_id','document_tab':'tab_id'}[kind]
        try:
            ids={'project_id':self.pid,key:str(eid)}
            if metadata and kind=='manuscript_block':
                return self.domain.entities.get(kind,ids,user_id=self.uid)
            return self.domain.get(kind,ids,user_id=self.uid)
        except DocumentNotFound:return None

    def anchor(self,row,sources):
        def source(kind,eid):
            key=(kind,str(eid or ''))
            if key not in sources:sources[key]=self.source(kind,eid,metadata=True)
            return sources[key]
        a=_public(row);block=source('manuscript_block',a.get('block_id')) if a.get('block_id') else None
        cid=(block or {}).get('chapter_id') if a.get('block_id') else a.get('chapter_id')
        chapter=source('chapter',cid);tab=source('document_tab',(chapter or {}).get('tab_id'))
        scene=source('scene',(block or {}).get('scene_id'))
        stale=bool(a.get('stale')) or not chapter or (bool(a.get('block_id')) and not block)
        return {**a,'chapter_id':cid,'title_chapter_id':row.get('chapter_id'),
            'chapter_title_anchor':not bool(a.get('block_id')),'chapter_title':(chapter or {}).get('title') or '',
            'chapter_position':(chapter or {}).get('position',0),'tab_position':(tab or {}).get('position',0),
            'block_position':(block or {}).get('position',-1),'scene_id':(scene or {}).get('id'),
            'scene_title':(scene or {}).get('title') or '', 'stale':stale}

    def get(self,tid,*,_sources=None):
        def read(tx):
            _,root=self.authorize(tx)
            _,thread,messages,anchors=self.thread_rows(tx,tid)
            return root['generation'],thread,messages,anchors
        generation,thread,messages,anchors=self.domain.documents.run_transaction(read)
        # Preserve already-saved raw body/items from the former HTTP handler as a
        # read-only projection. Editing its virtual first message updates that body;
        # no migration, silent deletion or fabricated persisted message is performed.
        legacy=isinstance(thread.get('body'),str)
        visible=[_public(row) for _,row in messages]
        if legacy:visible.insert(0,{'id':tid,'body':thread['body'],'position':-1,'created_at':thread.get('created_at'),'updated_at':thread.get('updated_at')})
        visible.sort(key=lambda row:(row.get('position',0),str(row.get('created_at') or ''),str(row['id'])))
        raw_anchors=[row for _,row in anchors]
        if legacy and not raw_anchors:raw_anchors=[dict(item) for item in (thread.get('items') or []) if isinstance(item,dict)]
        sources={} if _sources is None else _sources
        projected=[self.anchor(row,sources) for row in raw_anchors]
        projected.sort(key=lambda a:(a.get('tab_position') or 0,a.get('chapter_position') or 0,a.get('block_position') or 0,a.get('start_offset') or 0,str(a.get('id') or '')))
        first=visible[0]['body'] if visible else '';a=projected[0] if projected else {}
        if self.domain.project_generation(self.pid,self.uid)!=generation:raise DocumentConflict('HOSTED_GENERATION_CONFLICT')
        return {**_public(thread),'status':thread.get('status','open'),'messages':visible,'anchors':projected,
            'first_message':first,'reply_count':max(0,len(visible)-1),
            'excerpt':' … '.join(str(x.get('selected_text') or '').strip() for x in projected)[:600],
            'chapter_id':a.get('chapter_id'),'chapter_title':a.get('chapter_title',''),'scene_title':a.get('scene_title',''),
            'stale':bool(projected) and all(x['stale'] for x in projected)}

    def list(self,status='open',q=''):
        if status not in {'open','resolved','all'}:raise HTTPException(400,'Note status must be open, resolved, or all')
        generation=self.domain.project_generation(self.pid,self.uid)
        def read(tx):
            self.authorize(tx)
            return [row['id'] for _,row in self.rows(tx,'comment_thread') if status=='all' or row.get('status','open')==status]
        ids=self.domain.documents.run_transaction(read);needle=str(q or '').strip().casefold();result=[];sources={}
        for tid in ids:
            row=self.get(tid,_sources=sources)
            text=' '.join([row['first_message'],row['excerpt'],row['chapter_title'],row['scene_title'],*[x['body'] for x in row['messages']]]).casefold()
            if not needle or needle in text:result.append(row)
        if self.domain.project_generation(self.pid,self.uid)!=generation:raise DocumentConflict('HOSTED_GENERATION_CONFLICT')
        return sorted(result,key=lambda row:tuple((row.get('anchors') or [{}])[0].get(k) or 0 for k in ('tab_position','chapter_position','block_position','start_offset'))+(str(row['id']),))

    def mutate(self,kind,body,build):
        generation=self.domain.project_generation(self.pid,self.uid)
        operation=operation_for('notes_'+kind) or operation_id('notes_'+kind)
        fingerprint=hashlib.sha256(json.dumps([kind,body],sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        receipt=('projects',self.pid,'mutation_receipts',operation)
        stamp=self.domain.clock.now_iso()
        def run(tx):
            root_path,root=self.authorize(tx,mutation=True)
            previous=tx.get(receipt)
            if previous is not None:
                data=previous.data
                if data.get('actor')!=self.uid or data.get('fingerprint')!=fingerprint or data.get('generation')!=root['generation']:
                    raise DocumentConflict('HOSTED_IDEMPOTENCY_CONFLICT')
                return deepcopy(data['result'])
            # The builder only reads and stages canonical product rows. Provider
            # writes begin after ALL source, membership and size checks succeed.
            changes,result=build(tx,operation,stamp)
            if len(changes)>_MAX_ROWS or len(json.dumps(changes,ensure_ascii=False,default=str).encode())>300_000:
                raise HTTPException(413,'HOSTED_NOTES_TOO_LARGE')
            self.domain._advance_project(tx,root_path,root,expected_generation=generation,stamp=stamp)
            for path,data in changes:
                if data is None:tx.delete(path)
                else:tx.set(path,data)
            tx.create(receipt,{'actor':self.uid,'fingerprint':fingerprint,'generation':generation+1,'result':result})
            return result
        return self.domain.documents.run_transaction(run)

    @staticmethod
    def changed(row,stamp,**patch):
        return {**row,**patch,'generation':int(row.get('generation',0))+1,'updated_at':stamp}

    @staticmethod
    def created(eid,field,stamp,**fields):
        return {'id':eid,field:eid,**fields,'generation':1,'created_at':stamp,'updated_at':stamp}

    def checked_items(self,items):
        if not isinstance(items,list) or not items:raise HTTPException(400,'Select manuscript text before adding a note')
        if len(items)>_MAX_ROWS-2:raise HTTPException(413,'HOSTED_NOTES_TOO_LARGE')
        result=[]
        for item in items:
            if not isinstance(item,dict) or set(item)-{'block_id','chapter_id','start_offset','end_offset','selected_text','prefix_context','suffix_context'}:
                raise HTTPException(400,'Invalid note anchor')
            bid,cid=item.get('block_id'),item.get('chapter_id')
            if bool(bid)==bool(cid):raise HTTPException(400,'Note must anchor to one block or Chapter title')
            source=self.source('manuscript_block' if bid else 'chapter',bid or cid)
            if not source or (not bid and source.get('is_implicit')):raise HTTPException(409,'Note source changed')
            text=str((source.get('text_content') if bid else source.get('title')) or '')
            start,end=item.get('start_offset'),item.get('end_offset')
            if type(start) is not int or type(end) is not int or not 0<=start<=end<=len(text.encode('utf-16-le'))//2:
                raise HTTPException(400,'Note range is outside its manuscript target')
            try:selected=text.encode('utf-16-le')[start*2:end*2].decode('utf-16-le')
            except UnicodeError:raise HTTPException(400,'Note range splits a character')
            if item.get('selected_text') and item['selected_text']!=selected:raise HTTPException(409,'Note source changed')
            if any(not isinstance(item.get(key,''),str) for key in ('selected_text','prefix_context','suffix_context')):raise HTTPException(400,'Invalid note anchor text')
            result.append({**item,'block_id':bid,'chapter_id':cid,'selected_text':selected})
        return result

    def create(self,body):
        if set(body)-{'body','items'}:raise HTTPException(400,'Invalid note fields')
        # Capture the root BEFORE reading source text and recheck it at commit.
        generation=self.domain.project_generation(self.pid,self.uid)
        text=_body(body.get('body'));items=self.checked_items(body.get('items'))
        def build(tx,op,stamp):
            _,root=self.authorize(tx,mutation=True)
            if root['generation']!=generation:raise DocumentConflict('HOSTED_GENERATION_CONFLICT')
            tid=stable_entity_id('thread_',op);mid=stable_entity_id('message_',op+'_first')
            path=self.path('comment_thread',tid)
            if tx.get(path) is not None or tx.get(self.path('comment_message',mid,tid)) is not None:raise DocumentConflict('HOSTED_NOTES_ID_CONFLICT')
            changes=[(path,self.created(tid,'thread_id',stamp,status='open',first_message_id=mid)),
                (self.path('comment_message',mid,tid),self.created(mid,'message_id',stamp,body=text,position=0))]
            for i,item in enumerate(items):
                aid=stable_entity_id('annotation_',op+'_'+str(i));apath=self.path('annotation',aid)
                if tx.get(apath) is not None:raise DocumentConflict('HOSTED_NOTES_ID_CONFLICT')
                changes.append((apath,self.created(aid,'annotation_id',stamp,**item,annotation_type='comment',label_id=None,thread_id=tid,body=text,status='open',stale=False)))
            return changes,{'id':tid}
        result=self.mutate('create',body,build)
        return self.get(result['id'])

    def update(self,tid,body):
        if set(body)!={'status'} or body['status'] not in {'open','resolved'}:raise HTTPException(400,'Note status must be open or resolved')
        def build(tx,op,stamp):
            path,thread,messages,anchors=self.thread_rows(tx,tid)
            return [(path,self.changed(thread,stamp,status=body['status'])),*[(p,self.changed(a,stamp,status=body['status'])) for p,a in anchors]],{'id':tid}
        result=self.mutate('status',{'id':tid,**body},build);return self.get(result['id'])

    def reply(self,tid,body):
        if set(body)!={'body'}:raise HTTPException(400,'Invalid note fields')
        text=_body(body.get('body'))
        def build(tx,op,stamp):
            path,thread,messages,anchors=self.thread_rows(tx,tid)
            if len(messages)+len(anchors)+2>_MAX_ROWS:raise HTTPException(413,'HOSTED_NOTES_TOO_LARGE')
            mid=stable_entity_id('message_',op);mpath=self.path('comment_message',mid,tid)
            if tx.get(mpath) is not None:raise DocumentConflict('HOSTED_NOTES_ID_CONFLICT')
            position=max((m.get('position',0) for _,m in messages),default=0)+1
            return [(path,self.changed(thread,stamp)),(mpath,self.created(mid,'message_id',stamp,body=text,position=position))],{'id':tid}
        result=self.mutate('reply',{'id':tid,**body},build);return self.get(result['id'])

    def edit_message(self,mid,body):
        # Compatibility for existing clients without a parent hint. Membership is
        # checked BEFORE any project record lookup; no global collection group.
        mid=require_opaque_id(mid,label='message_id')
        if mid.startswith('thread_'):return self.edit(mid,mid,body)
        def locate(tx):
            self.authorize(tx)
            threads=self.rows(tx,'comment_thread')
            if len(threads)>2048:raise DocumentConflict('HOSTED_ENTITY_LOOKUP_REQUIRES_PARENT')
            for _,row in threads:
                if tx.get(self.path('comment_message',mid,row['id'])) is not None:return row['id']
            raise DocumentNotFound('HOSTED_DOCUMENT_NOT_FOUND')
        tid=self.domain.documents.run_transaction(locate)
        return self.edit(tid,mid,body)

    def edit(self,tid,mid,body):
        if set(body)!={'body'}:raise HTTPException(400,'Invalid note fields')
        text=_body(body.get('body'))
        def build(tx,op,stamp):
            path,thread,messages,anchors=self.thread_rows(tx,tid)
            legacy=mid==tid and isinstance(thread.get('body'),str)
            found=next(((p,m) for p,m in messages if m['id']==mid),None)
            if not found and not legacy:raise DocumentNotFound('HOSTED_DOCUMENT_NOT_FOUND')
            changes=[(path,self.changed(thread,stamp,**({'body':text} if legacy else {})))]
            if found:changes.append((found[0],self.changed(found[1],stamp,body=text)))
            first_id=thread.get('first_message_id') or (sorted(messages,key=lambda x:(x[1].get('position',0),str(x[1].get('created_at') or ''),str(x[1]['id'])))[0][1]['id'] if messages else None)
            if legacy or mid==first_id:
                changes.extend((p,self.changed(a,stamp,body=text)) for p,a in anchors)
            return changes,{'id':tid}
        result=self.mutate('edit',{'id':tid,'message_id':mid,**body},build);return self.get(result['id'])

    def delete(self,tid):
        def build(tx,op,stamp):
            path,thread,messages,anchors=self.thread_rows(tx,tid)
            return [(p,None) for p,_ in [*anchors,*messages,(path,thread)]],{'ok':True,'id':tid}
        return self.mutate('delete',{'id':tid},build)
