"""Canonical immutable annotation packs, not a cache or a parallel database.

One small annotation head per document/category participates in the existing
generation CAS + structural journal. Its verified immutable body contains normal
logical annotations. Every read/edit/export uses this projection; a partial
upload never becomes canonical. Native annotations/local persistence are intact.
"""
import gzip
import hashlib
import io
import json
import re

from hosted.firestore.contracts import DocumentConflict, DocumentNotFound, HostedValidationError
from hosted.firestore.ids import require_opaque_id, require_project_id, require_user_id
from api.annotations.editing_lexicon import PASSES
from hosted.narration.io_limits import io_timeout

FIELD = '_bm_editing_pack'
HEAD = re.compile(r'epack_([0-9a-f]{24})')
ITEM = re.compile(r'eann_([0-9a-f]{24})_([0-9a-f]{24})')
MAX_PACKS = 64
MAX_ROWS = 100_000
MAX_PACK_ROWS = 20_000
MAX_BYTES = 8 * 1024**2
MAX_DECODED = 16 * 1024**2
MAX_TOTAL_DECODED = 24 * 1024**2
ROW_FIELDS = frozenset({'id','annotation_id','project_id','block_id','chapter_id','annotation_type','label_id',
    'start_offset','end_offset','selected_text','prefix_context','suffix_context','thread_id','body','status',
    'stale','created_at','updated_at','generation','source_generation'})


def child(value): return isinstance(value,str) and ITEM.fullmatch(value) is not None


def annotation_view(domain,pid,uid,*,tab='',chapter=''):
    """Native-shaped presentation joins, with no manuscript BODY downloads.

    Canonical records/export keep raw anchors. The UI needs marker colour/name
    and the containing chapter; these joined fields never become saved authority.
    """
    packs=PackedAnnotations(domain);generation,physical=packs.snapshot(pid,uid)
    if not physical:return []
    def metadata(tx):
        packs.authorize(tx,pid,uid,strict=False)
        options={'field':'chapter_id','value':chapter} if chapter else {'field':'tab_id','value':tab} if tab else {}
        return [dict(row.data) for row in tx.query_collection(('projects',pid,'blocks'),**options)]
    blocks={row['id']:row for row in domain.documents.run_transaction(metadata)}
    chapters={row['id']:row for row in domain.list('chapter',{'project_id':pid},user_id=uid)
        if (not tab or row.get('tab_id')==tab) and (not chapter or row['id']==chapter)}
    labels={row['id']:row for row in domain.list('markup_label',{'project_id':pid},user_id=uid)}
    selected_tabs={row.get('tab_id') for row in chapters.values()}
    if domain.encrypted_fields is not None:
        source=domain.list('annotation',{'project_id':pid},user_id=uid)
    else:
        source=[]
        for head in physical:
            if FIELD in head:
                if (tab or chapter) and head.get('tab_id') not in selected_tabs:continue
                source.extend(packs.rows(pid,head))
            else:source.append(domain._hydrate('annotation',head))
    result=[]
    for row in source:
        cid=blocks.get(row.get('block_id'),{}).get('chapter_id') if row.get('block_id') else row.get('chapter_id')
        if (tab or chapter) and cid not in chapters:continue
        label=labels.get(row.get('label_id'),{})
        stale=row.get('stale',False) or ('source_generation' in row and row.get('source_generation')!=blocks.get(row.get('block_id'),{}).get('generation'))
        result.append({**row,'stale':stale,'chapter_id':cid,'title_chapter_id':row.get('chapter_id') if not row.get('block_id') else None,
            'chapter_title':chapters.get(cid,{}).get('title'),'tab_id':chapters.get(cid,{}).get('tab_id'),
            'label_name':label.get('name'),'label_color':label.get('color'),'label_description':label.get('description')})
    packs.fence(pid,uid,generation,strict=any(FIELD in row for row in physical))
    return result


class PackedAnnotations:
    def __init__(self, domain): self.domain = domain

    def authorize(self,tx,pid,uid,*,strict):
        authority=getattr(self.domain,'editing_authority',None)
        if strict and callable(authority) and authority(tx,uid,pid,'read') is not True:
            raise DocumentNotFound('HOSTED_DOCUMENT_NOT_FOUND')
        root,member=self.domain.projects._require_member(tx,pid,uid)
        if member.get('role') not in {'owner','editor'}:
            raise DocumentNotFound('HOSTED_DOCUMENT_NOT_FOUND')
        if strict:
            user=tx.get(('users',uid));lock=tx.get(('account_erasure_locks',uid))
            if user is None or user.data.get('status','active')!='active' or lock is not None:
                raise DocumentNotFound('HOSTED_DOCUMENT_NOT_FOUND')
            owners=tx.query_collection(('projects',pid,'members'),field='role',value='owner')
            if len(owners)!=1:raise DocumentNotFound('HOSTED_DOCUMENT_NOT_FOUND')
            owner=owners[0].data.get('user_id')
            owner_row=tx.get(('users',require_user_id(owner)))
            owner_lock=tx.get(('account_erasure_locks',owner))
            if owner_row is None or owner_row.data.get('status','active')!='active' or owner_lock is not None:
                raise DocumentNotFound('HOSTED_DOCUMENT_NOT_FOUND')
        return root

    def snapshot(self,pid,uid,*,mutation=False):
        pid,uid=require_project_id(pid),require_user_id(uid)
        def read(tx):
            root=self.authorize(tx,pid,uid,strict=mutation)
            rows=[dict(row.data) for row in tx.query_collection(('projects',pid,'annotations'))]
            heads=[row for row in rows if FIELD in row]
            if heads and not mutation:self.authorize(tx,pid,uid,strict=True)
            if (len(heads)>MAX_PACKS or sum(self._header(pid,head)['count'] for head in heads)>MAX_ROWS
                    or sum(self._header(pid,head)['decoded_bytes'] for head in heads)>MAX_TOTAL_DECODED):
                raise HostedValidationError('EDITING_SEARCH_TOO_LARGE')
            return int(root['generation']),rows
        return self.domain.documents.run_transaction(read)

    @staticmethod
    def _header(pid,head):
        pointer=head.get(FIELD)
        hid=head.get('id')
        if (not isinstance(hid,str) or not HEAD.fullmatch(hid) or head.get('annotation_id')!=hid
                or head.get('project_id')!=pid or head.get('annotation_type')!='editing-pack/v1'
                or head.get('scan') not in PASSES
                or type(head.get('generation')) is not int or head['generation']<1
                or not isinstance(pointer,dict) or set(pointer)!={'v','count','decoded_bytes','object_name','object_generation','object_size','object_sha256'}
                or type(pointer['v']) is not int or pointer['v']!=1
                or type(pointer['count']) is not int or not 0<=pointer['count']<=MAX_PACK_ROWS
                or type(pointer['decoded_bytes']) is not int or not 0<pointer['decoded_bytes']<=MAX_DECODED
                or type(pointer['object_generation']) is not int or pointer['object_generation']<1
                or type(pointer['object_size']) is not int or not 0<pointer['object_size']<=MAX_BYTES
                or not isinstance(pointer['object_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',pointer['object_sha256'])
                or pointer['object_name']!=f"projects/{pid}/annotation-packs/{hid}/{pointer['object_sha256']}.json.gz"):
            raise HostedValidationError('HOSTED_ANNOTATION_PACK_INVALID')
        require_opaque_id(head.get('tab_id'),label='tab_id')
        require_opaque_id(head.get('label_id'),label='label_id')
        return pointer

    def rows(self,pid,head):
        if self.domain.encrypted_objects is not None or self.domain.encrypted_fields is not None:
            raise HostedValidationError('HOSTED_PRIVATE_ANNOTATION_PACK_UNSUPPORTED')
        ref=self._header(pid,head)
        raw=b''.join(self.domain.objects.read_chunks(ref['object_name'],max_bytes=MAX_BYTES,
            if_generation_match=ref['object_generation']))
        if len(raw)!=ref['object_size'] or hashlib.sha256(raw).hexdigest()!=ref['object_sha256']:
            raise HostedValidationError('HOSTED_ANNOTATION_PACK_INTEGRITY')
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                data=stream.read(MAX_DECODED+1)
            if len(data)>MAX_DECODED or len(data)!=ref['decoded_bytes']:raise ValueError()
            body=json.loads(data)
            if (not isinstance(body,dict) or set(body)!={'v','head','rows'} or type(body['v']) is not int or body['v']!=1
                    or body['head']!=head['id'] or not isinstance(body['rows'],list) or len(body['rows'])!=ref['count']):
                raise ValueError()
            ids=set()
            for row in body['rows']:
                iid=row.get('id') if isinstance(row,dict) else None
                match=ITEM.fullmatch(iid) if isinstance(iid,str) else None
                if (not isinstance(row,dict) or set(row)-ROW_FIELDS or match is None
                        or match[1]!=HEAD.fullmatch(head['id'])[1] or iid in ids or row.get('annotation_id')!=iid
                        or row.get('project_id')!=pid or row.get('label_id')!=head['label_id']
                        or row.get('annotation_type')!='highlight' or row.get('chapter_id') is not None
                        or row.get('body')!='editing-search/v1:'+head['scan']
                        or type(row.get('source_generation')) is not int or row['source_generation']<1
                        or type(row.get('start_offset')) is not int or type(row.get('end_offset')) is not int
                        or not 0<=row['start_offset']<=row['end_offset']<=100_000
                        or any(not isinstance(row.get(key),str) for key in ('selected_text','prefix_context','suffix_context'))
                        or len(row['selected_text'])!=row['end_offset']-row['start_offset']
                        or len(row['prefix_context'])>45 or len(row['suffix_context'])>45):
                    raise ValueError()
                require_opaque_id(row.get('block_id'),label='block_id');ids.add(iid)
                row['generation']=head['generation']  # Undo head generations never ABA
                row['updated_at']=head['updated_at']
            return body['rows']
        except (ValueError,TypeError,KeyError):
            raise HostedValidationError('HOSTED_ANNOTATION_PACK_INVALID') from None

    def stage(self,pid,head,rows,*,available_bytes=MAX_TOTAL_DECODED):
        io_timeout()  # a cancelled/timed-out pass may stage no new work
        if len(rows)>MAX_PACK_ROWS:raise HostedValidationError('EDITING_SEARCH_TOO_MANY_MATCHES')
        body=json.dumps(dict(v=1,head=head['id'],rows=rows),ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
        if len(body)>min(MAX_DECODED,available_bytes):raise HostedValidationError('EDITING_SEARCH_TOO_LARGE')
        encoded=gzip.compress(body,compresslevel=6,mtime=0)
        if len(encoded)>MAX_BYTES:raise HostedValidationError('EDITING_SEARCH_TOO_LARGE')
        digest=hashlib.sha256(encoded).hexdigest()
        name=f"projects/{pid}/annotation-packs/{head['id']}/{digest}.json.gz"
        written=self.domain.immutable.publish_stream(name,lambda:[encoded],expected_sha256=digest,
            expected_size=len(encoded),max_bytes=MAX_BYTES,content_type='application/gzip')
        return {**head,FIELD:dict(v=1,count=len(rows),decoded_bytes=len(body),object_name=name,object_generation=written.object.generation,
            object_size=len(encoded),object_sha256=digest)}

    def list(self,pid,uid,*,field=None,value=None,order_by=('position','created_at','id')):
        generation,physical=self.snapshot(pid,uid)
        rows=[]
        for row in physical:
            rows.extend(self.rows(pid,row) if FIELD in row else [self.domain._hydrate('annotation',row,ids={
                'project_id':pid,'annotation_id':row['id']},user_id=uid)])
        if any('source_generation' in row for row in rows):
            def sources(tx):
                self.authorize(tx,pid,uid,strict=False)
                return {row.path[-1]:row.data.get('generation') for row in tx.query_collection(('projects',pid,'blocks'))}
            generations=self.domain.documents.run_transaction(sources)
            rows=[{**row,'stale':row.get('stale',False) or row['source_generation']!=generations.get(row.get('block_id'))}
                if 'source_generation' in row else row for row in rows]
        # Current authority AND generation after object I/O; never return a mixed
        # head snapshot after revocation or an intervening canonical mutation.
        self.fence(pid,uid,generation,strict=any(FIELD in row for row in physical))
        rows=[row for row in rows if field is None or row.get(field)==value]
        return sorted(rows,key=lambda row:tuple(str(row.get(key) or '') for key in order_by))

    def get(self,pid,uid,iid):
        require_project_id(pid);require_user_id(uid)
        match=ITEM.fullmatch(iid)
        if match is None:raise DocumentNotFound('HOSTED_DOCUMENT_NOT_FOUND')
        generation,physical=self.snapshot(pid,uid)
        head=next((row for row in physical if row['id']=='epack_'+match[1] and FIELD in row),None)
        row=next((row for row in self.rows(pid,head) if row['id']==iid),None) if head else None
        self.fence(pid,uid,generation,strict=True)
        if row is None:raise DocumentNotFound('HOSTED_DOCUMENT_NOT_FOUND')
        source=self.domain.documents.get(('projects',pid,'blocks',row['block_id']))
        if source is None or row['source_generation']!=source.data.get('generation'):row['stale']=True
        self.fence(pid,uid,generation,strict=True)
        return generation,head,row

    def fence(self,pid,uid,generation,*,strict=False):
        def read(tx):
            root=self.authorize(tx,pid,uid,strict=strict)
            if int(root['generation'])!=generation:raise DocumentConflict('HOSTED_GENERATION_CONFLICT')
        self.domain.documents.run_transaction(read)

    def mutate(self,pid,uid,iid,patch,*,expected_generation=None,expected_project_revision=None):
        generation,head,row=self.get(pid,uid,iid)
        if (expected_generation is not None and expected_generation!=head['generation']
                or expected_project_revision is not None and expected_project_revision!=generation):
            raise DocumentConflict('HOSTED_GENERATION_CONFLICT')
        allowed={'status','stale','start_offset','end_offset','selected_text','prefix_context','suffix_context'}
        # Ownership/marker/body/target are not editable through a virtual range.
        # Explicit Clear/Remove/Recheck own those transitions atomically.
        if patch is not None and (not isinstance(patch,dict) or set(patch)-allowed):
            raise HostedValidationError('HOSTED_ANNOTATION_PACK_PATCH_INVALID')
        rows=self.rows(pid,head)
        desired=None if patch is None else {**row,**patch}
        if desired is not None and (patch.get('stale') is False or {'start_offset','end_offset','selected_text'} & patch.keys()):
            source=self.domain.get('manuscript_block',{'project_id':pid,'block_id':row['block_id']},user_id=uid)
            text=source.get('text_content') or ''
            if (type(desired.get('start_offset')) is not int or type(desired.get('end_offset')) is not int
                    or not 0<=desired['start_offset']<=desired['end_offset']<=len(text)
                    or text[desired['start_offset']:desired['end_offset']]!=desired.get('selected_text')):
                raise HostedValidationError('HOSTED_ANNOTATION_SOURCE_CHANGED')
            desired['source_generation']=source['generation']
        rows=[desired if old['id']==iid else old for old in rows if old['id']!=iid or desired is not None]
        changed={**head,'generation':head['generation']+1,'updated_at':self.domain.clock.now_iso()}
        current,physical=self.snapshot(pid,uid,mutation=True)
        if current!=generation:raise DocumentConflict('HOSTED_GENERATION_CONFLICT')
        remaining=MAX_TOTAL_DECODED-sum(item[FIELD]['decoded_bytes'] for item in physical if FIELD in item and item['id']!=head['id'])
        staged=self.stage(pid,changed,rows,available_bytes=remaining)
        # Validate our generated body by the SAME reader BEFORE pointer switch.
        self.rows(pid,staged)
        self.commit(pid,uid,generation,[(('projects',pid,'annotations',head['id']),head,staged)])
        return None if desired is None else {**desired,'generation':staged['generation'],'updated_at':staged['updated_at']}

    def commit(self,pid,uid,generation,changes,*,receipt=None):
        def commit(tx):
            io_timeout()  # reject work starting after the request deadline
            root=self.authorize(tx,pid,uid,strict=True)
            if int(root['generation'])!=generation:raise DocumentConflict('HOSTED_GENERATION_CONFLICT')
            for path,before,after in changes:
                current=tx.get(path)
                if (dict(current.data) if current else None)!=before:raise DocumentConflict('HOSTED_GENERATION_CONFLICT')
            if receipt is not None and tx.get(receipt[0]) is not None:
                raise DocumentConflict('HOSTED_IDEMPOTENCY_CONFLICT')
            io_timeout()  # slow authority/CAS reads cannot start late writes
            # ActionDocumentStore buffers writes and enforces its existing size,
            # path/authority limits. No cap increase or partial publication.
            for path,before,after in changes:
                if after is None:tx.delete(path)
                elif before is None:tx.create(path,after)
                else:tx.set(path,after)
            self.domain._advance_project(tx,('projects',pid),root,
                expected_generation=generation,stamp=self.domain.clock.now_iso())
            if receipt is not None:tx.create(*receipt)
        self.domain.documents.run_transaction(commit)
