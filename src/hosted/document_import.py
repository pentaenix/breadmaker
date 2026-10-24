"""Owner-bound, resumable document imports. Each HTTP step does bounded work.

No background thread survives a request. Immutable, verified plans and receipts
survive replica changes; the existing package authority transaction publishes the
new project only after every batch has committed. Existing projects are untouched.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import json
import time

from hosted.domain_store import operation_id
from hosted.document_io import parse_document_bytes
from hosted.firestore.contracts import DocumentConflict, DocumentNotFound, HostedValidationError
from hosted.firestore.ids import require_opaque_id, require_user_id
from hosted.idempotency import stable_entity_id, stable_project_id
from security.image_security import HOSTED_DEFAULT_IMAGE_BYTES, inspect_hosted_image_bytes
from hosted.packages import HostedProjectPackageService

MAX_RECORDS=10000
MAX_PLAN_BYTES=32*1024*1024
BATCH_SIZE=64
LIFETIME=86400


class DocumentImport:
    def __init__(self,projects):
        self.packages=HostedProjectPackageService(projects)
        self.projects=projects
        self.documents=projects.documents
        self.domain=projects.domain

    @staticmethod
    def _path(uid,job):
        return ('document_imports',require_user_id(uid),'jobs',require_opaque_id(job,label='import_job'))

    def _load(self,uid,job):
        row=self.documents.get(self._path(uid,job))
        if row is None:raise DocumentNotFound('HOSTED_IMPORT_NOT_FOUND')
        data=dict(row.data)
        if data['actor']!=uid:raise DocumentNotFound('HOSTED_IMPORT_NOT_FOUND')
        if time.time()>data['expires_at']:raise DocumentConflict('HOSTED_IMPORT_EXPIRED')
        return data

    @staticmethod
    def _public(job,data):
        return {'job_id':job,'next_batch':data['next_batch'],'total_batches':len(data['plans']),
            'chapters':data['chapters'],'images':int(data.get('images') or 0),'warnings':data['warnings'],
            'ready':data['next_batch']==len(data['plans'])}

    def status(self,uid,job):
        return self._public(job,self._load(uid,job))

    def begin(self,raw,filename,heading,title,uid,operation=None):
        uid=require_user_id(uid)
        if self.domain.encrypted_objects is not None or self.domain.encrypted_fields is not None:
            raise HostedValidationError('HOSTED_ENCRYPTED_PACKAGE_IMPORT_NOT_READY')
        op=operation or operation_id('document_import')
        job=stable_entity_id('import_',op)
        path=self._path(uid,job)
        name=(title or str(filename).rsplit('.',1)[0] or 'Imported Project').strip()[:240] or 'Imported Project'
        fingerprint=hashlib.sha256(raw+b'\0'+json.dumps([filename,heading,name]).encode()).hexdigest()
        old=self.documents.get(path)
        if old is not None:
            if old.data['fingerprint']!=fingerprint:
                raise DocumentConflict('HOSTED_IMPORT_REQUEST_CHANGED')
            return self.status(uid,job)
        pid=stable_project_id(op)
        assets={}
        def collect_image(blob,extension):
            info=inspect_hosted_image_bytes(blob,extension,byte_limit=HOSTED_DEFAULT_IMAGE_BYTES)
            digest=hashlib.sha256(blob).hexdigest()
            path='/api/assets/img_'+digest[:24]+info.extension
            assets[path]=(blob,info.extension)
            if len(assets)>64 or sum(len(value[0]) for value in assets.values())>MAX_PLAN_BYTES:
                raise HostedValidationError('HOSTED_IMPORT_IMAGE_BUDGET_EXCEEDED')
            return path
        tabs,warnings=parse_document_bytes(raw,filename,heading,image_store=collect_image)
        entries=[];chapters=0
        def add(entity,prefix,data):
            eid=stable_entity_id(prefix,f'{op}_{len(entries)}')
            entries.append({'entity':entity,'id':eid,'data':data})
            return eid
        for tpos,tab in enumerate(tabs):
            tid=add('document_tab','tab_',{'title':tab.get('title') or 'Manuscript',
                'position':tpos,'kind':'manuscript' if tpos==0 else 'document','is_manuscript':tpos==0})
            for cpos,chapter in enumerate(tab['chapters']):
                cid=add('chapter','chapter_',{'tab_id':tid,'title':str(chapter['title'])[:240],'position':cpos})
                chapters+=1
                for bpos,block in enumerate(chapter['blocks']):
                    add('manuscript_block','b_',{'tab_id':tid,'chapter_id':cid,'position':bpos,
                        'block_type':'image' if block.get('kind')=='image' else 'paragraph','semantic_style':block.get('style') or 'body',
                        'scene_id':None,'image_path':block.get('image_path') or '','image_width':100,'image_footer':'',
                        'image_description':'','image_row_id':'',
                        'text_content':block.get('text') or '','content_html':block.get('html') or ''})
        if not 1<=len(entries)<=MAX_RECORDS:
            raise HostedValidationError('HOSTED_IMPORT_DOCUMENT_TOO_LARGE')
        encoded=[json.dumps(entries[i:i+BATCH_SIZE],ensure_ascii=False,separators=(',',':')).encode()
            for i in range(0,len(entries),BATCH_SIZE)]
        if sum(map(len,encoded))>MAX_PLAN_BYTES or any(len(part)>4*1024*1024 for part in encoded):
            raise HostedValidationError('HOSTED_IMPORT_DOCUMENT_TOO_LARGE')
        # Assets are validated/published/verified under the NEW unpublished
        # project before any referencing block can become visible. Binaries
        # never inflate the JSON plans or enter Firestore documents.
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda asset:self.packages._stage_asset(pid,uid,*asset),assets.values()))
        def publish(item):
            index,part=item
            return self.domain._publish_hybrid('manuscript_block',
                {'project_id':pid,'block_id':f'b_plan_{index}'},gzip.compress(part,mtime=0))
        with ThreadPoolExecutor(max_workers=8) as pool:
            plans=list(pool.map(publish,enumerate(encoded)))
        data={'actor':uid,'project_id':pid,'title':name,'fingerprint':fingerprint,
            'plans':plans,'next_batch':0,'chapters':chapters,'images':sum(entry['entity']=='manuscript_block' and entry['data'].get('block_type')=='image' for entry in entries),'warnings':warnings,
            'expires_at':time.time()+LIFETIME}
        def reserve(tx):
            existing=tx.get(path)
            if existing is not None:
                if existing.data['fingerprint']!=fingerprint:
                    raise DocumentConflict('HOSTED_IMPORT_REQUEST_CHANGED')
                return
            # This project prefix is server generated and must remain unpublished.
            if tx.get(('projects',pid)) is not None:
                raise DocumentConflict('HOSTED_IMPORT_PROJECT_ALREADY_PUBLISHED')
            tx.create(path,data)
        self.documents.run_transaction(reserve)
        return self.status(uid,job)

    def step(self,uid,job,index):
        data=self._load(uid,job)
        if type(index) is not int or not 0<=index<len(data['plans']):
            raise HostedValidationError('HOSTED_IMPORT_BATCH_INVALID')
        if index<data['next_batch']:return self._public(job,data)
        if index!=data['next_batch']:raise DocumentConflict('HOSTED_IMPORT_BATCH_OUT_OF_ORDER')
        pointer=data['plans'][index]
        encoded=b''.join(self.domain.objects.read_chunks(pointer['object_name'],
            max_bytes=8*1024*1024,if_generation_match=int(pointer['object_generation'])))
        if (len(encoded)!=int(pointer['object_size']) or
                hashlib.sha256(encoded).hexdigest()!=pointer['object_sha256']):
            raise HostedValidationError('HOSTED_IMPORT_PLAN_INTEGRITY_FAILED')
        entries=json.loads(gzip.decompress(encoded))
        self.packages.stage_import_batch(data['project_id'],entries,bundle_blocks=True)
        def advance(tx):
            current=tx.get(self._path(uid,job))
            updated=dict(current.data)
            if updated['next_batch']==index:
                updated['next_batch']=index+1
                tx.set(self._path(uid,job),updated)
            elif updated['next_batch']<index:
                raise DocumentConflict('HOSTED_IMPORT_BATCH_OUT_OF_ORDER')
        self.documents.run_transaction(advance)
        return self.status(uid,job)

    def finish(self,uid,job):
        data=self._load(uid,job)
        if data['next_batch']!=len(data['plans']):
            raise DocumentConflict('HOSTED_IMPORT_NOT_FINISHED')
        project=self.packages._finish_import(data['project_id'],data['title'],uid)
        return {'ok':True,'reload':True,'project':project,'chapters':data['chapters'],
            'images':int(data.get('images') or 0),'warnings':data['warnings']}
