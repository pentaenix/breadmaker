from __future__ import annotations

"""Portable hosted project packages.

Cloud packages contain product state and referenced asset bytes, never memberships,
account ids, provider generations, or authorization authority. Import always creates a
fresh 32-hex project id. Legacy package-v2 SQLite is decoded transiently in memory only;
SQLite is never used as hosted persistence.
"""

import base64
import hashlib
import io
import json
import sqlite3
import zipfile
from string import Formatter
from typing import Any, Mapping

from hosted.domain_store import _ID_FIELDS, HostedDomainStore, operation_id
from hosted.idempotency import stable_project_id
from hosted.firestore.ids import new_project_id, require_opaque_id, require_project_id, require_user_id
from hosted.firestore.contracts import DocumentConflict, HostedAuthorizationError
from hosted.firestore.model_registry import MODELS
from hosted.firestore.repositories import _canonical_payload, _fingerprint, _reject_managed_create_fields, render_model_path
from hosted.storage.keys import project_asset_key
from hosted.storage.publication import ImmutableObjectPublisher
from security.image_security import HOSTED_DEFAULT_IMAGE_BYTES, UnsafeImage, inspect_hosted_image_bytes

_FORMAT='breadmaker-hosted-project'
_VERSION=3
_MAX_PACKAGE=256*1024*1024
_MAX_INLINE_EXPANDED=256*1024*1024
_MAX_INLINE_ENTRY=64*1024*1024
_MAX_INLINE_ASSET=12*1024*1024
_SKIP={
    'project','project_membership','hosted_model_metadata','recovery_state','hosted_user_profile','identity_binding',
    'project_asset','hosted_revision_history','checkpoint_asset_reference','backup_destination_configuration',
    'member_bookmark','member_ui_state',
}


def _fields(template:str) -> list[str]:
    return [name for _,name,_,_ in Formatter().parse(template) if name]


def _public(row:Mapping[str,Any]) -> dict[str,Any]:
    blocked={'generation','created_at','updated_at','object_name','object_generation','object_sha256','object_size','payload_object',
             'encryption_operation','encryption_format'}
    return {str(k):_json_safe(v) for k,v in dict(row).items() if not str(k).startswith('_bm_') and str(k) not in blocked}


def _json_safe(value:Any) -> Any:
    if isinstance(value,(str,int,float,bool)) or value is None:return value
    if isinstance(value,bytes):return {'$bytes':base64.b64encode(value).decode('ascii')}
    if isinstance(value,Mapping):return {str(k):_json_safe(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [_json_safe(v) for v in value]
    return str(value)


def _restore_safe(value:Any) -> Any:
    if isinstance(value,dict):
        if set(value)=={'$bytes'}:
            return base64.b64decode(str(value['$bytes']).encode('ascii'))
        return {str(k):_restore_safe(v) for k,v in value.items()}
    if isinstance(value,list):return [_restore_safe(v) for v in value]
    return value


class HostedProjectPackageService:
    def __init__(self, projects) -> None:
        self.projects=projects
        self.documents=projects.documents
        self.objects=projects.objects
        self.domain:HostedDomainStore=projects.domain

    def _records(self, project_id:str, user_id:str) -> dict[str,list[dict[str,Any]]]:
        # Normal exports (ZIP, JSON, local portability) MUST refuse opted-in
        # encryption. Never let a public download surface receive decrypted
        # manuscript/Review data from the experimental private storage modes.
        if self.domain.encrypted_objects is not None or self.domain.encrypted_fields is not None:
            raise ValueError('HOSTED_ENCRYPTED_PACKAGE_EXPORT_NOT_READY')
        return self._collect_authorized_records(project_id, user_id)

    def _collect_authorized_records(self, project_id:str, user_id:str) -> dict[str,list[dict[str,Any]]]:
        # Reads through HostedDomainStore.list, which checks membership BEFORE
        # decrypting any private Firestore fields or encrypted GCS payloads.
        # Used by the operator-only sealed recovery source; do not expose this
        # method through the HTTP/API or regular portable export interfaces.
        records:dict[str,list[dict[str,Any]]]={}
        identity_rows:dict[str,list[dict[str,str]]]={}
        ordered=sorted(
            (m for m in MODELS.values() if m.path_template.startswith('projects/{project_id}/')
             and (m.logical_entity not in _SKIP or
                  (m.logical_entity in {'hosted_revision_history','project_asset'} and
                   self.domain.encrypted_fields is not None and
                   self.domain.encrypted_objects is not None))),
            key=lambda m:(len(m.path_template.split('/')),m.logical_entity),
        )
        for model in ordered:
            fields=_fields(model.path_template)
            if not fields or fields[0]!='project_id':continue
            final=fields[-1];parent_fields=fields[:-1]
            if parent_fields==['project_id']:
                parent_sets=[{'project_id':project_id}]
            else:
                candidates=[]
                seen=set()
                for rows in identity_rows.values():
                    for ids in rows:
                        if all(field in ids for field in parent_fields):
                            item={field:ids[field] for field in parent_fields};key=tuple(sorted(item.items()))
                            if key not in seen:seen.add(key);candidates.append(item)
                parent_sets=candidates
            exported=[];ids_out=[]
            for parents in parent_sets:
                try:rows=self.domain.list(model.logical_entity,parents,user_id=user_id)
                except HostedAuthorizationError:
                    # Only explicitly system-managed entities may be excluded. Any
                    # other failed authoritative read must abort the whole export:
                    # a partial package is not an acceptable disaster recovery copy.
                    if model.authorization_policy != 'system_only':
                        raise
                    continue
                for row in rows:
                    entity_id=str(row.get(final) or row.get('id') or '')
                    if not entity_id:continue
                    ids={**parents,final:entity_id}
                    exported.append({'ids':ids,'data':_public(row)})
                    ids_out.append(ids)
            if exported:
                records[model.logical_entity]=exported
                identity_rows[model.logical_entity]=ids_out
        # Private snapshots include ONLY the source owner's member state. Other
        # members' preferences are separate account data, never transferrable
        # authority; the completeness guard explicitly rejects any such rows
        # rather than silently omitting them. Ordinary public exports do not
        # receive member state or the private decryption exception.
        if self.domain.encrypted_fields is not None and self.domain.encrypted_objects is not None:
            for logical, collection in (
                ('member_bookmark', ('projects', project_id, 'members', user_id, 'state')),
                ('member_ui_state', ('projects', project_id, 'members', user_id, 'ui_state')),
            ):
                restored = []
                for row in self.documents.query_collection(collection):
                    if logical == 'member_bookmark' and row.path[-1] != 'bookmark':
                        raise ValueError('HOSTED_PRIVATE_MEMBER_STATE_UNKNOWN_PATH')
                    ids = {'project_id': project_id, 'user_id': user_id}
                    if logical == 'member_ui_state':
                        ids['key'] = str(row.path[-1])
                    opened = self.domain.encrypted_fields.open(logical, ids, row.data)
                    restored.append({'ids': ids, 'data': _public(opened)})
                if restored:
                    records[logical] = restored
        return records

    def export_bytes(self, project_id:str, user_id:str) -> bytes:
        project=self.projects.authority.get_for_user(project_id,user_id)
        records=self._records(project_id,user_id)
        return self._render_package(project_id, user_id, project, records)

    def _sealed_recovery_source(self, project_id: str, user_id: str) -> bytes:
        """M12I operator-only in-memory source for an ENCRYPTED recovery writer.

        Never routed to the HTTP export/import handlers. Both private storage
        codecs are required to prevent a half-encrypted project from using
        this exception to the ordinary portability embargo. Only the separately
        authenticated, owner-fenced recovery writer may call this method.
        The ZIP exists transiently in process RAM and MUST be encrypted before
        a recovery ObjectStore write. It MUST NOT be logged or written to disk.
        """
        if self.domain.encrypted_fields is None or self.domain.encrypted_objects is None:
            raise ValueError('HOSTED_SEALED_RECOVERY_REQUIRES_BOTH_CODECS')
        pid, uid = require_project_id(project_id), require_user_id(user_id)
        # Defense in depth: the recovery writer already checks owner+generation,
        # but this private export also independently refuses reviewer/member
        # access, even if a future internal caller bypasses the writer.
        self.documents.run_transaction(
            lambda tx: self.projects.authority._require_member(tx, pid, uid, owner=True)
        )
        project = self.projects.authority.get_for_user(pid, uid)
        # A physical source fingerprint detects direct/provider mutations
        # during the author-controlled extraction even if they never advance
        # canonical project_generation. No plaintext content digests persist.
        from hosted.recovery_coverage import physical_project_fingerprint, physical_project_gcs_fingerprint, RecoveryCoverageError
        baseline = physical_project_fingerprint(self.documents, pid)
        gcs_baseline = physical_project_gcs_fingerprint(self.objects, pid)
        records = self._collect_authorized_records(pid, uid)
        # M12K: the ordinary portable exporter may omit deliberately nonportable
        # records. An encrypted disaster-recovery archive cannot silently do so.
        # Survey all registered, reachable project collections independently and
        # reject unsupported families, missing/duplicate rows, and plaintext
        # source records before publishing even the first recovery chunk.
        from hosted.recovery_coverage import require_complete_private_recovery
        require_complete_private_recovery(self.projects, pid, records, owner_user_id=uid)
        # Immutable checkpoints may reference images no longer present in live
        # manuscript rows. Their bytes must still be in the sealed asset set.
        from hosted.checkpoint_dependencies import require_checkpoint_assets_present
        require_checkpoint_assets_present(records)
        payload = self._render_package(pid, uid, project, records)
        if physical_project_fingerprint(self.documents, pid) != baseline:
            raise RecoveryCoverageError('HOSTED_RECOVERY_SOURCE_CHANGED_DURING_EXPORT')
        if physical_project_gcs_fingerprint(self.objects, pid) != gcs_baseline:
            raise RecoveryCoverageError('HOSTED_RECOVERY_GCS_CHANGED_DURING_EXPORT')
        return payload

    def _render_package(self, project_id: str, user_id: str,
                        project: Mapping[str, Any],
                        records: Mapping[str, list[dict[str, Any]]]) -> bytes:
        # This renderer is internal to the package service. Encrypted-mode
        # callers must enter via _sealed_recovery_source; public APIs continue
        # to fail closed. The only persistent recovery output is ciphertext.
        manifest={
            'format':_FORMAT,'format_version':_VERSION,'source_project_id':project_id,
            'title':str(project.get('title') or 'Untitled'),'authorization_portable':False,
        }
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
            archive.writestr('manifest.json',json.dumps(manifest,sort_keys=True,separators=(',',':')))
            archive.writestr('project.json',json.dumps({'entities':records},sort_keys=True,separators=(',',':'),ensure_ascii=False))
            # Export only assets that are authoritative in Firestore. Prefix scans may
            # include immutable orphan candidates left by failed pointer-last commits and
            # those must never become portable project state.
            for asset in self.domain.list('project_asset',{'project_id':project_id},user_id=user_id):
                digest=str(asset.get('sha256') or '').lower()
                name=str(asset.get('name') or '')
                object_name=str(asset.get('object_name') or '')
                generation=int(asset.get('object_generation') or 0)
                if len(digest)!=64 or not name or not object_name or generation<=0:
                    raise ValueError('HOSTED_PACKAGE_ASSET_METADATA_INVALID')
                if self.domain.encrypted_fields is not None and self.domain.encrypted_objects is not None:
                    from hosted.security.envelope import FORMAT as PRIVATE_FORMAT
                    from hosted.storage.encrypted_objects import EncryptedObjectReference
                    if asset.get('encryption_format') != PRIVATE_FORMAT:
                        raise ValueError('HOSTED_PACKAGE_PRIVATE_ASSET_POINTER_INVALID')
                    payload=self.domain.encrypted_objects.read(
                        project_id=project_id,user_id=user_id,entity='project_asset',
                        record_id=str(asset.get('asset_id') or ''),
                        operation_id=str(asset.get('encryption_operation') or ''),
                        reference=EncryptedObjectReference(
                            object_name,generation,str(asset.get('object_sha256') or ''),
                            int(asset.get('object_size') or -1)))
                else:
                    meta=self.objects.stat(object_name)
                    if meta is None or int(meta.generation)!=generation or str(meta.sha256)!=digest:
                        raise ValueError('HOSTED_PACKAGE_ASSET_POINTER_STALE')
                    payload=b''.join(self.objects.read_chunks(object_name,max_bytes=_MAX_PACKAGE,if_generation_match=generation))
                if hashlib.sha256(payload).hexdigest()!=digest:
                    raise ValueError('HOSTED_PACKAGE_ASSET_HASH_MISMATCH')
                ext='.'+name.rsplit('.',1)[1].lower() if '.' in name else ''
                if ext not in {'.png','.jpg','.webp'}:
                    raise ValueError('HOSTED_PACKAGE_ASSET_EXTENSION_INVALID')
                if self.domain.encrypted_objects is not None:
                    inspect_hosted_image_bytes(payload,ext,byte_limit=_MAX_INLINE_ASSET)
                archive.writestr('assets/'+digest+ext,payload)
        data=buffer.getvalue()
        if len(data)>_MAX_PACKAGE:raise ValueError('HOSTED_PACKAGE_TOO_LARGE')
        return data

    def export_local_bytes(self, project_id: str, user_id: str) -> bytes:
        """M9B: portable Schema-42 v2 download, accepted by unmodified local Breadmaker.

        Unlike the hosted-v3 recovery snapshot format, this is a one-way
        compatibility export. Concurrent Firestore mutations invalidate the
        snapshot, and unsupported entities fail closed rather than disappearing.
        """
        from hosted.firestore.contracts import DocumentConflict
        from hosted.local_portability import to_local_package

        pid, uid = require_project_id(project_id), require_user_id(user_id)
        before = self.domain.project_generation(pid, uid)
        source_project = self.projects.authority.get_for_user(pid, uid)
        frozen = self.export_bytes(pid, uid)
        portable = to_local_package(
            frozen, project_generation=before,
            created_at=str(source_project.get('created_at') or ''),
            updated_at=str(source_project.get('updated_at') or ''),
        )
        if self.domain.project_generation(pid, uid) != before:
            raise DocumentConflict('HOSTED_PORTABLE_CONCURRENT_MUTATION')
        return portable

    def export_json(self, project_id:str, user_id:str) -> dict[str,Any]:
        project=self.projects.authority.get_for_user(project_id,user_id)
        return {
            'format':'breadmaker-hosted-json','format_version':1,
            'source_project_id':str(project_id),
            'title':str(project.get('title') or 'Untitled'),
            'authorization_portable':False,
            'entities':self._records(project_id,user_id),
        }

    def preview_json(self, payload:Mapping[str,Any]) -> dict[str,Any]:
        if not isinstance(payload,Mapping):raise ValueError('HOSTED_JSON_IMPORT_INVALID')
        entities=payload.get('entities')
        if not isinstance(entities,Mapping):raise ValueError('HOSTED_JSON_IMPORT_INVALID')
        counts={}
        total=0
        for logical,items in entities.items():
            if logical not in MODELS or logical in _SKIP:continue
            if not isinstance(items,list):raise ValueError('HOSTED_JSON_IMPORT_INVALID')
            counts[str(logical)]=len(items);total+=len(items)
        return {'ok':True,'title':str(payload.get('title') or 'Imported Project')[:240],'entity_count':total,'counts':counts,'creates_new_project':True}

    def import_json(self, payload:Mapping[str,Any], user_id:str, *, name_override:str|None=None) -> dict[str,Any]:
        if self.domain.encrypted_objects is not None or self.domain.encrypted_fields is not None:
            raise ValueError('HOSTED_ENCRYPTED_PACKAGE_IMPORT_NOT_READY')
        self.preview_json(payload)
        entities=payload.get('entities') or {}
        title=str(name_override or payload.get('title') or 'Imported Project').strip()[:240] or 'Imported Project'
        pid=self._new_import_project_id()
        try:
            ordered=sorted(
                ((name,MODELS[name]) for name in entities if name in MODELS and name not in _SKIP),
                key=lambda pair:(len(pair[1].path_template.split('/')),pair[0]),
            )
            for logical,model in ordered:
                final=_fields(model.path_template)[-1]
                for item in entities.get(logical) or []:
                    if not isinstance(item,Mapping) or not isinstance(item.get('ids'),Mapping) or not isinstance(item.get('data'),Mapping):
                        raise ValueError('HOSTED_JSON_IMPORT_INVALID')
                    ids={str(k):str(v) for k,v in item['ids'].items()};ids['project_id']=pid
                    eid=str(ids.get(final) or '')
                    if not eid:raise ValueError('HOSTED_JSON_IMPORT_INVALID')
                    parents={k:v for k,v in ids.items() if k!=final}
                    data={k:_restore_safe(v) for k,v in item['data'].items()};data.pop('project_id',None)
                    self._stage_entity(logical,{**parents,'project_id':pid},data,entity_id=eid)
            return self._finish_import(pid,title,user_id)
        except Exception:
            self._fail_import(pid,user_id);raise

    def _reserve_import(self, *, operation:str, user_id:str, digest:str, name_override:str|None) -> None:
        # Durable replay receipt is written *before* any staged Firestore/GCS content.
        # It is not a process-local lock and is independently scoped to the server
        # authenticated actor. M9 will own receipt retention/garbage collection.
        uid=require_user_id(user_id)
        path=('hosted_import_operations',operation)
        stamp=self.domain.clock.now_iso()
        name=str(name_override or '')
        def reserve(tx):
            previous=tx.get(path)
            if previous is not None:
                old=dict(previous.data)
                if old.get('user_id')!=uid or old.get('sha256')!=digest or old.get('name_override')!=name:
                    raise DocumentConflict('HOSTED_IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PACKAGE')
                return
            tx.create(path,{'user_id':uid,'sha256':digest,'name_override':name,'created_at':stamp})
        self.documents.run_transaction(reserve)

    def import_bytes(self, payload:bytes, user_id:str, *, name_override:str|None=None, operation:str|None=None) -> dict[str,Any]:
        if self.domain.encrypted_objects is not None or self.domain.encrypted_fields is not None:
            raise ValueError('HOSTED_ENCRYPTED_PACKAGE_IMPORT_NOT_READY')
        raw=bytes(payload)
        if len(raw)>_MAX_PACKAGE:raise ValueError('HOSTED_PACKAGE_TOO_LARGE')
        try:
            with zipfile.ZipFile(io.BytesIO(raw),'r') as archive:
                names=set(archive.namelist())
                if any(name.startswith('/') or '..' in name.split('/') or '\\' in name for name in names):raise ValueError('HOSTED_PACKAGE_PATH_INVALID')
                # A tiny compressed ZIP may expand into arbitrarily large data.
                # Budget every member *before* any archive.read() or staging write.
                members=archive.infolist()
                if len(members)>25000:raise ValueError('HOSTED_PACKAGE_MEMBER_LIMIT')
                if any(i.file_size>_MAX_INLINE_ENTRY or (i.filename.startswith('assets/') and i.file_size>_MAX_INLINE_ASSET) for i in members):
                    raise ValueError('HOSTED_PACKAGE_EXPANDED_TOO_LARGE')
                if sum(i.file_size for i in members)>_MAX_INLINE_EXPANDED:
                    raise ValueError('HOSTED_PACKAGE_EXPANDED_TOO_LARGE')
                if operation:
                    self._reserve_import(operation=operation,user_id=user_id,digest=hashlib.sha256(raw).hexdigest(),name_override=name_override)
                manifest=json.loads(archive.read('manifest.json').decode('utf-8'))
                if manifest.get('format')==_FORMAT and int(manifest.get('format_version') or 0)==_VERSION:
                    return self._import_v3(archive,manifest,user_id,name_override=name_override,import_operation=operation)
                if str(manifest.get('package_format') or '')=='breadmaker-project' and int(manifest.get('package_version') or 0)==2:
                    return self._import_legacy_v2(archive,manifest,user_id,name_override=name_override,import_operation=operation)
        except (KeyError,zipfile.BadZipFile,json.JSONDecodeError,UnicodeDecodeError) as exc:
            raise ValueError('HOSTED_PACKAGE_INVALID') from exc
        raise ValueError('HOSTED_PACKAGE_UNSUPPORTED')

    def _new_import_project_id(self, operation:str|None=None) -> str:
        # Do not create project authority yet. Child documents/objects may be staged
        # safely under an unowned random project prefix because no reader can authorize
        # it until the final project+owner transaction commits.
        return stable_project_id(operation) if operation else new_project_id()

    def _stage_entity(self, logical:str, ids:Mapping[str,str], data:Mapping[str,Any], *, entity_id:str) -> None:
        if self.domain.encrypted_objects is not None or self.domain.encrypted_fields is not None:
            # Import stages records before the new project's membership exists.
            # This path must be redesigned instead of bypassing authorizer/KMS.
            raise ValueError('HOSTED_ENCRYPTED_PACKAGE_IMPORT_NOT_READY')
        model=MODELS.get(logical)
        if model is None or logical not in _ID_FIELDS or not model.path_template.startswith('projects/{project_id}/'):
            raise ValueError('HOSTED_PACKAGE_ENTITY_UNSUPPORTED')
        fields=_fields(model.path_template)
        final=fields[-1]
        expected_field,_prefix=_ID_FIELDS[logical]
        if final!=expected_field:
            raise ValueError('HOSTED_PACKAGE_ENTITY_ID_POLICY_INVALID')
        full_ids={str(k):str(v) for k,v in dict(ids).items()}
        full_ids['project_id']=require_project_id(str(full_ids.get('project_id') or ''))
        full_ids[final]=require_opaque_id(entity_id,label=final)
        path=render_model_path(model,**full_ids)
        public=_canonical_payload(self.domain._payload_with_identity(logical,data,entity_id))
        _reject_managed_create_fields(public)
        hybrid=self.domain._hybrid_payload(logical,public)
        if hybrid is not None:
            structured,encoded=hybrid
            stored=dict(structured)
            stored['payload_object']=self.domain._publish_hybrid(logical,full_ids,encoded)
        else:
            stored=dict(public)
        stamp=self.domain.clock.now_iso()
        op='import_'+logical+'_'+entity_id
        if len(op)>160:op='import_'+_fingerprint({'logical':logical,'id':entity_id})[:48]
        fingerprint=_fingerprint(public)
        stored.setdefault('generation',1)
        stored.setdefault('created_at',stamp)
        stored['updated_at']=stamp
        stored['_bm_create_operation']=op
        stored['_bm_create_fingerprint']=fingerprint
        def create(tx):
            old=tx.get(path)
            if old is not None:
                if old.data.get('_bm_create_operation')==op and old.data.get('_bm_create_fingerprint')==fingerprint:
                    return
                raise ValueError('HOSTED_PACKAGE_ENTITY_COLLISION')
            tx.create(path,stored)
        self.documents.run_transaction(create)

    def _stage_asset(self,pid:str,user_id:str,data:bytes,extension:str,*,expected_digest:str|None=None) -> None:
        ext='.'+str(extension or '').lower().lstrip('.')
        try:
            info=inspect_hosted_image_bytes(bytes(data),ext,byte_limit=HOSTED_DEFAULT_IMAGE_BYTES)
        except UnsafeImage as exc:
            raise ValueError('HOSTED_PACKAGE_ASSET_UNSAFE') from exc
        import hashlib
        digest=hashlib.sha256(data).hexdigest()
        if expected_digest is not None and digest!=str(expected_digest):
            raise ValueError('HOSTED_PACKAGE_ASSET_HASH_MISMATCH')
        publication=ImmutableObjectPublisher(self.objects).publish_bytes(
            project_asset_key(pid,digest,info.extension),data,max_bytes=HOSTED_DEFAULT_IMAGE_BYTES,
            content_type=info.media_type,metadata={'breadmaker-entity':'project_asset'},
        )
        obj=publication.object
        name=f'img_{digest[:24]}{info.extension}'
        self._stage_entity(
            'project_asset',{'project_id':pid},
            {'api_path':'/api/assets/'+name,'name':name,'sha256':digest,'object_name':obj.name,'object_generation':obj.generation,'object_sha256':obj.sha256,'object_size':obj.size},
            entity_id='asset_'+digest,
        )

    def _finish_import(self,pid:str,title:str,user_id:str) -> dict[str,Any]:
        uid=require_user_id(user_id)
        # This is the atomic visibility point. Before this transaction there is no
        # project document or membership, so direct /p/<id> and every API lookup fail
        # closed even though bounded child/object staging may already have completed.
        self.projects.authority.create(
            title,owner_user_id=uid,project_id=require_project_id(pid),
            operation_id='package_import_'+pid[:24],
        )
        self.projects._update_last_project(uid,pid)
        return self.projects.get(pid)

    def _fail_import(self,pid:str,user_id:str) -> None:
        # Before the final visibility transaction there is deliberately no project
        # authority to tombstone. Any staged child docs/immutable objects are unreachable
        # orphan candidates keyed by a random project id and may be retention-GC'd later.
        return None

    def _import_v3(self,archive:zipfile.ZipFile,manifest:Mapping[str,Any],user_id:str,*,name_override:str|None,import_operation:str|None=None):
        snapshot=json.loads(archive.read('project.json').decode('utf-8'))
        entities=snapshot.get('entities')
        if not isinstance(entities,dict):raise ValueError('HOSTED_PACKAGE_INVALID')
        title=str(name_override or manifest.get('title') or 'Imported Project').strip()[:240] or 'Imported Project'
        pid=self._new_import_project_id(import_operation)
        try:
            ordered=sorted(
                ((name,MODELS[name]) for name in entities if name in MODELS and name not in _SKIP),
                key=lambda pair:(len(pair[1].path_template.split('/')),pair[0]),
            )
            for logical,model in ordered:
                final=_fields(model.path_template)[-1]
                for item in entities.get(logical) or []:
                    if not isinstance(item,dict) or not isinstance(item.get('ids'),dict) or not isinstance(item.get('data'),dict):raise ValueError('HOSTED_PACKAGE_INVALID')
                    ids={str(k):str(v) for k,v in item['ids'].items()};ids['project_id']=pid
                    eid=str(ids.get(final) or '')
                    parents={k:v for k,v in ids.items() if k!=final}
                    data={k:_restore_safe(v) for k,v in item['data'].items()}
                    data.pop('project_id',None)
                    self._stage_entity(logical,{**parents,'project_id':pid},data,entity_id=eid)
            for name in sorted(archive.namelist()):
                if not name.startswith('assets/') or name.endswith('/'):continue
                leaf=name.split('/')[-1];parts=leaf.split('.',1)
                if len(parts)!=2 or len(parts[0])!=64:raise ValueError('HOSTED_PACKAGE_ASSET_INVALID')
                digest,ext=parts;self._stage_asset(pid,user_id,archive.read(name),'.'+ext,expected_digest=digest)
            return self._finish_import(pid,title,user_id)
        except Exception:
            self._fail_import(pid,user_id);raise

    def _import_legacy_v2(self,archive:zipfile.ZipFile,manifest:Mapping[str,Any],user_id:str,*,name_override:str|None,import_operation:str|None=None):
        db_name=next((name for name in archive.namelist() if name.endswith('project.sqlite3')),None)
        if not db_name:raise ValueError('HOSTED_PACKAGE_DATABASE_MISSING')
        database=archive.read(db_name)
        con=sqlite3.connect(':memory:');con.row_factory=sqlite3.Row
        try:
            if not hasattr(con,'deserialize'):raise ValueError('HOSTED_LEGACY_IMPORT_DESERIALIZE_UNAVAILABLE')
            con.deserialize(database)
            if str(con.execute('PRAGMA integrity_check').fetchone()[0]).lower()!='ok':raise ValueError('HOSTED_PACKAGE_DATABASE_INVALID')
            if con.execute('PRAGMA foreign_key_check').fetchone() is not None:raise ValueError('HOSTED_PACKAGE_DATABASE_INVALID')
            title_row=con.execute('SELECT title FROM projects ORDER BY id LIMIT 1').fetchone()
            title=str(name_override or (title_row['title'] if title_row else '') or manifest.get('name') or 'Imported Project').strip()[:240] or 'Imported Project'
            pid=self._new_import_project_id(import_operation)
            try:
                self._translate_legacy_tables(con,pid,user_id)
                for name in archive.namelist():
                    marker='/assets/'
                    if marker not in '/'+name or name.endswith('/'):continue
                    leaf=name.rsplit('/',1)[-1]
                    if not leaf.startswith('img_') or '.' not in leaf:continue
                    ext='.'+leaf.rsplit('.',1)[1].lower()
                    if ext not in {'.png','.jpg','.webp'}:continue
                    self._stage_asset(pid,user_id,archive.read(name),ext)
                return self._finish_import(pid,title,user_id)
            except Exception:
                self._fail_import(pid,user_id);raise
        finally:con.close()

    def _translate_legacy_tables(self,con:sqlite3.Connection,pid:str,user_id:str) -> None:
        contract=json.loads((__import__('pathlib').Path(__file__).resolve().parents[2]/'docs'/'HOSTED_DATA_MODEL_V1.json').read_text(encoding='utf-8'))
        mappings=[row for row in contract['schema42_mappings'] if row['logical_entity'] in MODELS and row['logical_entity'] not in _SKIP and row['logical_entity'] in _ID_FIELDS]
        mappings.sort(key=lambda row:(len(MODELS[row['logical_entity']].path_template.split('/')),row['logical_entity']))
        id_maps:dict[tuple[str,str],str]={}
        field_targets={
            'tab_id':'document_tab','parent_id':'document_tab','chapter_id':'chapter','scene_id':'scene','block_id':'manuscript_block',
            'label_id':'markup_label','thread_id':'comment_thread','canvas_id':'canvas','from_node_id':'canvas_node','to_node_id':'canvas_node',
            'map_id':'story_map','line_id':'map_line','from_line_id':'map_line','to_line_id':'map_line','from_item_id':'map_item','to_item_id':'map_item',
            'task_id':'task','review_id':'review','participant_id':'review_participant','questionnaire_id':'questionnaire',
        }
        tables={str(row[0]) for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rows_by_entity={}
        # Allocate every translated ID before resolving any cross-table link.
        # For example, chapters are visited before their document tabs.
        for mapping in mappings:
            table=mapping['source_table'];logical=mapping['logical_entity']
            if table not in tables:continue
            rows=[dict(row) for row in con.execute(f'SELECT * FROM "{table}"')]
            rows_by_entity[logical]=rows
            for row in rows:
                old=str(row.get('id') or '')
                if not old:continue
                new_id=f"{_ID_FIELDS[logical][1]}legacy_{old}"
                id_maps[(logical,old)]=new_id
        for mapping in mappings:
            logical=mapping['logical_entity']
            model=MODELS[logical];fields=_fields(model.path_template);final=fields[-1]
            rows=rows_by_entity.get(logical,[])
            for row in rows:
                old=str(row.get('id') or '')
                if not old:continue
                eid=id_maps[(logical,old)]
                ids={'project_id':pid}
                for parent in fields[1:-1]:
                    raw=row.get(parent)
                    if raw is None:continue
                    target=field_targets.get(parent)
                    ids[parent]=id_maps.get((target,str(raw)),str(raw)) if target else str(raw)
                payload={}
                for key,value in row.items():
                    # Legacy timestamps/generation are not hosted authority.
                    # _stage_entity assigns fresh server-owned values; keep its
                    # managed-field rejection intact for all other callers.
                    if key in {'id','project_id','created_at','updated_at','generation'}:continue
                    target=field_targets.get(key)
                    if target and value is not None:value=id_maps.get((target,str(value)),str(value))
                    payload[key]=_json_safe(value)
                # Schema-42 checkpoints store compressed snapshot bytes. Preserve them
                # as a portable opaque value rather than treating SQLite bytes as hosted authority.
                if logical=='checkpoint' and 'snapshot_blob' in payload:
                    payload['snapshot']={'legacy_snapshot_blob':payload.pop('snapshot_blob')}
                self._stage_entity(logical,ids,payload,entity_id=eid)
