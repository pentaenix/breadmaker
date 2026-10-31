"""One-shot hosted English searches: immutable annotation body + atomic head CAS.

All manuscript text is read through canonical domain adapters, never the lazy
browser view. Generic annotation projection/export and existing Undo own results.
No source rewrites, background jobs, local SQLite or new cloud dependencies.
"""
from copy import deepcopy
import hashlib
import json
import secrets

from api.annotations.editing_lexicon import PASSES
from api.annotations.editing_search import find_matches
from api.annotations.editing_descriptions import DESCRIPTIONS, LEGACY_DESCRIPTION
from api.annotations.marker_colors import recommend_marker_color
from hosted.action_journal import _ACTION
from hosted.editing_packs import PackedAnnotations, FIELD, HEAD, MAX_PACKS, MAX_ROWS, MAX_PACK_ROWS, MAX_TOTAL_DECODED
from hosted.firestore.contracts import DocumentConflict, DocumentNotFound, HostedValidationError
from hosted.firestore.ids import require_opaque_id, require_project_id, require_user_id
from hosted.idempotency import operation_for


class HostedEditingSearch:
    def __init__(self,domain): self.domain,self.packs=domain,PackedAnnotations(domain)

    def physical(self,logical,parents,*,user_id,field=None,value=None):
        path,ids=self.domain._collection_path(logical,parents)
        model=self.domain._model(logical)
        def read(tx):
            self.domain.entities._authorize(tx,model,ids,user_id,mutation=False)
            return [dict(row.data) for row in tx.query_collection(path,field=field,value=value)]
        return self.domain.documents.run_transaction(read)

    def capture(self,pid,uid,tab):
        pid,uid,tab=require_project_id(pid),require_user_id(uid),require_opaque_id(tab,label='tab_id')
        if self.domain.encrypted_fields is not None or self.domain.encrypted_objects is not None:
            raise HostedValidationError('HOSTED_PRIVATE_ANNOTATION_PACK_UNSUPPORTED')
        generation,physical=self.packs.snapshot(pid,uid,mutation=True)
        self.domain.get('document_tab',{'project_id':pid,'tab_id':tab},user_id=uid)
        labels=self.physical('markup_label',{'project_id':pid},user_id=uid)
        if self.domain.project_generation(pid,uid)!=generation:raise DocumentConflict('HOSTED_GENERATION_CONFLICT')
        return generation,physical,labels

    @staticmethod
    def marker(labels,physical,kind):
        # Tracking is PROJECT-shared canonical marker metadata, not collaborator
        # preferences. Undo restores the exact marker/category identity. Opaque
        # IDs are never inferred from a name, account, supplied registry or email.
        saved=[row for row in labels if kind in row.get('editing_search_categories',[])]
        if len(saved)>1:raise HostedValidationError('EDITING_SEARCH_MARKER_AMBIGUOUS')
        if saved:return saved[0]
        owned={row.get('label_id') for row in physical if row.get('scan')==kind and FIELD in row
            or row.get('body')=='editing-search/v1:'+kind}
        candidates=[row for row in labels if row['id'] in owned]
        if len(candidates)>1:raise HostedValidationError('EDITING_SEARCH_MARKER_AMBIGUOUS')
        return candidates[0] if candidates else next((row for row in labels
            if str(row.get('name') or '').strip().casefold()==PASSES[kind][0].casefold()),None)

    def status(self,pid,uid,tab):
        generation,physical,labels=self.capture(pid,uid,tab)
        rows=[row for row in physical if FIELD not in row and str(row.get('body') or '').startswith('editing-search/v1:')]
        blocks=({row['id'] for row in self.physical('manuscript_block',{'project_id':pid},user_id=uid)
            if row.get('tab_id')==tab} if rows else set())
        result={}
        for kind in PASSES:
            marker=self.marker(labels,physical,kind)
            result[kind]=dict(marker=marker,matches=sum(row.get('block_id') in blocks and row.get('label_id')==(marker or {}).get('id')
                and row.get('body')=='editing-search/v1:'+kind for row in rows)+sum(row[FIELD]['count'] for row in physical
                    if FIELD in row and row.get('scan')==kind and row.get('tab_id')==tab and row.get('label_id')==(marker or {}).get('id')))
        self.packs.fence(pid,uid,generation,strict=True)
        return {'searches':result}

    def run(self,pid,uid,data):
        if (not isinstance(data,dict) or set(data)-{'scan','action','tab_id'} or data.get('scan') not in PASSES
                or data.get('action','scan') not in {'scan','clear','recheck','remove'}):
            raise HostedValidationError('EDITING_SEARCH_REQUEST_INVALID')
        tab=require_opaque_id(data.get('tab_id'),label='tab_id')
        kind,action=data['scan'],data.get('action','scan')
        context=_ACTION.get()
        if context is None or context[1:]!=(pid,uid):
            raise HostedValidationError('EDITING_SEARCH_STRUCTURAL_ACTION_REQUIRED')
        generation,physical,labels=self.capture(pid,uid,tab)
        operation=operation_for('editing_search') or 'editing_'+hashlib.sha256(context[0].encode()).hexdigest()
        receipt_path=('projects',pid,'mutation_receipts',operation)
        fingerprint=hashlib.sha256(json.dumps([kind,action,tab],separators=(',',':')).encode()).hexdigest()
        previous=self.domain.documents.get(receipt_path)
        if previous is not None:
            if (previous.data.get('fingerprint')!=fingerprint or previous.data.get('actor')!=uid
                    or previous.data.get('action')!=context[0] or previous.data.get('generation')!=generation):
                raise DocumentConflict('HOSTED_IDEMPOTENCY_CONFLICT')
            self.packs.fence(pid,uid,generation)
            return deepcopy(previous.data['result'])
        def receipt(result):
            return receipt_path,dict(v=1,fingerprint=fingerprint,actor=uid,action=context[0],generation=generation+1,result=result)
        marker=self.marker(labels,physical,kind)
        stamp='editing-search/v1:'+kind
        own=[row for row in physical if FIELD in row and row.get('tab_id')==tab and row.get('scan')==kind]
        if len(own)>1:raise HostedValidationError('EDITING_SEARCH_PACK_AMBIGUOUS')
        head=own[0] if own else None
        before_marker=deepcopy(marker)
        now=self.domain.clock.now_iso()
        changes=[]
        if action=='remove':
            if marker is None:return dict(marker=None,matches=0,added=0,removed=0)
            return self.remove_marker(pid,uid,generation,physical,marker,receipt_factory=receipt)
        if action=='clear':
            matches=[];blocks=None
        else:
            raw=self.physical('manuscript_block',{'project_id':pid},user_id=uid,field='tab_id',value=tab)
            if len(raw)>20_000:raise HostedValidationError('EDITING_SEARCH_TOO_LARGE')
            chapters={row['id'] for row in self.physical('chapter',{'project_id':pid},user_id=uid,field='tab_id',value=tab)}
            if any(row.get('chapter_id') not in chapters for row in raw):
                raise HostedValidationError('EDITING_SEARCH_DOCUMENT_STRUCTURE_INVALID')
            chapter_ids={row.get('chapter_id') for row in raw}
            blocks=self.domain.bundled_manuscript(pid,chapter_ids,user_id=uid)
            if blocks is None:blocks=self.domain.list('manuscript_block',{'project_id':pid},user_id=uid,field='tab_id',value=tab)
            blocks=[row for row in blocks if row.get('tab_id')==tab and (row.get('block_type') or 'paragraph')=='paragraph']
            if sum(len(row.get('text_content') or '') for row in blocks)>5_000_000 or any(len(row.get('text_content') or '')>100_000 for row in blocks):
                raise HostedValidationError('EDITING_SEARCH_TOO_LARGE')
            matches=[]
            source_generations={block['id']:block['generation'] for block in blocks}
            for block in blocks:
                text=block.get('text_content') or ''
                for start,end in find_matches(text,kind):
                    matches.append((block['id'],start,end,text[start:end],text[max(0,start-45):start],text[end:end+45]))
                    if len(matches)>MAX_PACK_ROWS:raise HostedValidationError('EDITING_SEARCH_TOO_MANY_MATCHES')
        if marker is None and not matches:return dict(marker=None,matches=0,added=0,removed=0)
        if marker is None:
            lid='label_'+secrets.token_hex(12)
            marker=dict(id=lid,label_id=lid,project_id=pid,name=PASSES[kind][0],description=DESCRIPTIONS[kind],
                color=recommend_marker_color([row.get('color') for row in labels]),generation=1,created_at=now,updated_at=now)
        else:
            marker=deepcopy(marker)
            marker['generation']=int(marker['generation'])+1;marker['updated_at']=now
            if marker.get('description')==LEGACY_DESCRIPTION and any(row.get('label_id')==marker['id'] and
                    (row.get('scan')==kind or row.get('body')==stamp) for row in physical):
                marker['description']=DESCRIPTIONS[kind]
        marker['editing_search_categories']=sorted(set(marker.get('editing_search_categories',[]))|{kind})
        changes.append((('projects',pid,'markup_labels',marker['id']),before_marker,marker))
        target_ids={row['id'] for row in raw} if action!='clear' else {
            row['id'] for row in self.physical('manuscript_block',{'project_id':pid},user_id=uid,field='tab_id',value=tab)}
        old_pack=self.packs.rows(pid,head) if head else []
        old_regular=[row for row in physical if FIELD not in row and row.get('label_id')==marker['id']
            and row.get('block_id') in target_ids and row.get('annotation_type')=='highlight']
        regular_ids={row['id'] for row in old_regular}
        key=lambda row:(row.get('block_id'),row.get('start_offset'),row.get('end_offset'),row.get('selected_text'))
        manual={key(row) for row in old_regular if row.get('body')!=stamp and not row.get('stale')}
        wanted={item[:4] for item in matches}
        removed=0;retained=[]
        for row in [*old_pack,*old_regular]:
            if row.get('body')!=stamp:continue
            remove=action in {'clear','recheck'} or key(row) not in wanted or row.get('stale') or key(row) in manual
            if remove:
                removed+=1
                if row['id'] in regular_ids:changes.append((('projects',pid,'annotations',row['id']),row,None))
            else:
                retained.append(row);manual.add(key(row))
        if head is None:
            hid='epack_'+secrets.token_hex(12)
            new_head=dict(id=hid,annotation_id=hid,project_id=pid,tab_id=tab,scan=kind,
                annotation_type='editing-pack/v1',label_id=marker['id'],generation=1,created_at=now,updated_at=now)
        else:new_head={**head,'generation':head['generation']+1,'updated_at':now}
        packed=[row for row in retained if row['id'] not in regular_ids]
        added=0
        for block,start,end,selected,prefix,suffix in matches:
            if (block,start,end,selected) in manual:continue
            iid='eann_'+HEAD.fullmatch(new_head['id'])[1]+'_'+secrets.token_hex(12)
            packed.append(dict(id=iid,annotation_id=iid,project_id=pid,block_id=block,chapter_id=None,
                annotation_type='highlight',label_id=marker['id'],start_offset=start,end_offset=end,
                selected_text=selected,prefix_context=prefix,suffix_context=suffix,body=stamp,status='open',
                stale=False,thread_id=None,created_at=now,updated_at=now,generation=new_head['generation']))
            packed[-1]['source_generation']=source_generations[block]
            added+=1
        total=sum(row[FIELD]['count'] for row in physical if FIELD in row and row is not head)+len(packed)
        if total>MAX_ROWS or head is None and sum(FIELD in row for row in physical)>=MAX_PACKS:
            raise HostedValidationError('EDITING_SEARCH_TOO_LARGE')
        if len(changes)+1>90:raise HostedValidationError('EDITING_SEARCH_TOO_LARGE')
        if self.domain.project_generation(pid,uid)!=generation:raise DocumentConflict('HOSTED_GENERATION_CONFLICT')
        remaining=MAX_TOTAL_DECODED-sum(row[FIELD]['decoded_bytes'] for row in physical if FIELD in row and row is not head)
        staged=self.packs.stage(pid,new_head,packed,available_bytes=remaining)
        self.packs.rows(pid,staged)
        if sum(row[FIELD]['decoded_bytes'] for row in physical if FIELD in row and row is not head)+staged[FIELD]['decoded_bytes']>MAX_TOTAL_DECODED:
            raise HostedValidationError('EDITING_SEARCH_TOO_LARGE')
        changes.append((('projects',pid,'annotations',new_head['id']),head,staged))
        result=dict(marker=marker,matches=len(matches),added=added,removed=removed)
        self.packs.commit(pid,uid,generation,changes,receipt=receipt(result))
        return result

    def remove_marker(self,pid,uid,generation,physical,marker,*,receipt_factory=None):
        changes=[];removed=0
        for row in physical:
            if row.get('label_id')!=marker['id']:continue
            if FIELD in row:removed+=row[FIELD]['count']
            elif row.get('annotation_type')=='highlight':removed+=1
            else:
                # Comments retain content but lose their deleted marker binding.
                changes.append((('projects',pid,'annotations',row['id']),row,{**row,'label_id':None,
                    'generation':int(row['generation'])+1,'updated_at':self.domain.clock.now_iso()}));continue
            changes.append((('projects',pid,'annotations',row['id']),row,None))
        # Native FK SET NULL equivalents: preserve cards, links and task content.
        for logical,collection,key,id_key in [('task','tasks','marker_id','task_id'),('manuscript_block','blocks','label_id','block_id')]:
            for row in self.physical(logical,{'project_id':pid},user_id=uid,field=key,value=marker['id']):
                changes.append((('projects',pid,collection,row[id_key]),row,{**row,key:None,
                    'generation':int(row['generation'])+1,'updated_at':self.domain.clock.now_iso()}))
        canvases=self.domain.list('canvas',{'project_id':pid},user_id=uid)
        for canvas in canvases:
            for logical,collection,key,eid in [('canvas_node','nodes','label_id','node_id'),('canvas_edge','edges','marker_id','edge_id')]:
                for row in self.physical(logical,{'project_id':pid,'canvas_id':canvas['id']},user_id=uid,field=key,value=marker['id']):
                    changes.append((('projects',pid,'canvases',canvas['id'],collection,row[eid]),row,{**row,key:None,
                        'generation':int(row['generation'])+1,'updated_at':self.domain.clock.now_iso()}))
        changes.append((('projects',pid,'markup_labels',marker['id']),marker,None))
        if len(changes)>90:raise HostedValidationError('EDITING_SEARCH_TOO_LARGE')
        result=dict(marker=None,matches=0,added=0,removed=removed)
        self.packs.commit(pid,uid,generation,changes,receipt=receipt_factory(result) if receipt_factory else None)
        return result

    def clear_selected(self,pid,uid,data):
        """Selection clear rewrites each affected immutable pack ONCE, not per hit.

        Selection offsets follow the native API's Unicode code-point contract.
        Source bodies are canonical; neither browser-selected text nor cached
        generated snippets establish the target or current content.
        """
        if self.domain.encrypted_fields is not None or self.domain.encrypted_objects is not None:
            raise HostedValidationError('HOSTED_PRIVATE_ANNOTATION_PACK_UNSUPPORTED')
        items=data.get('items') if isinstance(data,dict) else None
        if not isinstance(items,list) or len(items)>1024:raise HostedValidationError('EDITING_SEARCH_TOO_LARGE')
        generation,physical=self.packs.snapshot(pid,uid,mutation=True)
        targets={};cuts={}
        unit=data.get('offset_unit','codepoints')
        if unit not in {'utf16','codepoints'}:raise HostedValidationError('HOSTED_ANNOTATION_RANGE_INVALID')
        def codepoint(text,offset):
            if type(offset) is not int or offset<0:raise HostedValidationError('HOSTED_ANNOTATION_RANGE_INVALID')
            raw=text.encode('utf-16-le')
            if offset*2>len(raw):raise HostedValidationError('HOSTED_ANNOTATION_RANGE_INVALID')
            try:return len(raw[:offset*2].decode('utf-16-le'))
            except UnicodeDecodeError:raise HostedValidationError('HOSTED_ANNOTATION_RANGE_INVALID') from None
        for item in items:
            if (not isinstance(item,dict) or bool(item.get('block_id'))==bool(item.get('chapter_id'))
                    or type(item.get('start_offset')) is not int or type(item.get('end_offset')) is not int):
                raise HostedValidationError('HOSTED_ANNOTATION_RANGE_INVALID')
            kind='block_id' if item.get('block_id') else 'chapter_id'
            target=require_opaque_id(item[kind],label=kind);key=(kind,target)
            if key not in targets:
                row=self.domain.get('manuscript_block' if kind=='block_id' else 'chapter',
                    {'project_id':pid,kind:target},user_id=uid)
                targets[key]=row.get('text_content' if kind=='block_id' else 'title') or ''
            start,end=item['start_offset'],item['end_offset']
            if unit=='utf16':start,end=codepoint(targets[key],start),codepoint(targets[key],end)
            if not 0<=start<=end<=len(targets[key]):raise HostedValidationError('HOSTED_ANNOTATION_RANGE_INVALID')
            cuts.setdefault(key,[]).append((start,end))
        changes=[];changed=0;now=self.domain.clock.now_iso()
        total_decoded=sum(row[FIELD]['decoded_bytes'] for row in physical if FIELD in row)
        def split(row,head=None):
            nonlocal changed
            if row.get('annotation_type')!='highlight':return [row]
            key=('block_id',row.get('block_id')) if row.get('block_id') else ('chapter_id',row.get('chapter_id'))
            if key not in cuts:return [row]
            text=targets[key]
            owned=str(row.get('body') or '').startswith('editing-search/v1:')
            start,end=row.get('start_offset'),row.get('end_offset')
            if not owned:start,end=codepoint(text,start),codepoint(text,end)
            segments=[(start,end)];original=list(segments)
            for lo,hi in cuts[key]:
                following=[]
                for start,end in segments:
                    if lo>=end or hi<=start:following.append((start,end));continue
                    if start<lo:following.append((start,lo))
                    if hi<end:following.append((hi,end))
                segments=following
            if segments==original:return [row]
            changed+=1;out=[]
            for index,(start,end) in enumerate(segments):
                iid=row['id'] if index==0 else ('eann_'+HEAD.fullmatch(head['id'])[1]+'_' if head else 'annotation_')+secrets.token_hex(12)
                stored_start=start if owned else len(text[:start].encode('utf-16-le'))//2
                stored_end=end if owned else len(text[:end].encode('utf-16-le'))//2
                out.append({**row,'id':iid,'annotation_id':iid,'start_offset':stored_start,'end_offset':stored_end,
                    'selected_text':text[start:end],'prefix_context':text[max(0,start-45):start],
                    'suffix_context':text[end:end+45],'generation':int(row['generation'])+1,'updated_at':now})
            return out
        for old in physical:
            if FIELD in old:
                # Avoid reading unrelated documents' bodies at all.
                if not any(kind=='block_id' for kind,_ in cuts):continue
                before=self.packs.rows(pid,old);after=[item for row in before for item in split(row,old)]
                if before==after:continue
                head={**old,'generation':old['generation']+1,'updated_at':now}
                remaining=MAX_TOTAL_DECODED-total_decoded+old[FIELD]['decoded_bytes']
                staged=self.packs.stage(pid,head,after,available_bytes=remaining);self.packs.rows(pid,staged)
                total_decoded+=staged[FIELD]['decoded_bytes']-old[FIELD]['decoded_bytes']
                changes.append((('projects',pid,'annotations',old['id']),old,staged))
            else:
                after=split(old)
                if after==[old]:continue
                changes.append((('projects',pid,'annotations',old['id']),old,after[0] if after else None))
                for new in after[1:]:changes.append((('projects',pid,'annotations',new['id']),None,new))
        if len(changes)>90:raise HostedValidationError('EDITING_SEARCH_TOO_LARGE')
        if changes:self.packs.commit(pid,uid,generation,changes)
        else:self.packs.fence(pid,uid,generation)
        return {'ok':True,'changed':changed,'removed':changed}
