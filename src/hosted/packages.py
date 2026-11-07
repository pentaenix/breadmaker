from __future__ import annotations

"""Portable hosted project packages.

Cloud packages contain product state and referenced asset bytes, never memberships,
account ids, provider generations, or authorization authority. Import always creates a
fresh 32-hex project id. Legacy package-v2 SQLite is decoded transiently in memory only;
SQLite is never used as hosted persistence.
"""

import base64
import hashlib
import gzip
import io
import json
import sqlite3
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from string import Formatter
from typing import Any, Mapping

from hosted.domain_store import _ID_FIELDS, HostedDomainStore, operation_id
from hosted.idempotency import stable_project_id
from hosted.import_staging import package_import_staging
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


_PROVIDER_FIELDS = frozenset({'payload_object', 'object_name', 'object_generation',
    'object_sha256', 'object_size', 'encryption_operation', 'encryption_format'})
_PARTICIPANT_AUTHORITY = frozenset({'user_id', 'owner_user_id', 'member_id', 'role',
    'session_id', 'invitation_id', 'invitation_token', 'token', 'access_grant',
    'invite_token', 'access_token', 'session_token', 'reviewer_session_id',
    'auth_token', 'invite_url', 'invite_id', 'invited_user_id', 'reviewer_user_id',
    'external_user_id', 'magic_link', 'secret'})
_PARTICIPANT_PORTABLE = frozenset({'id', 'participant_id', 'review_id', 'project_id',
    'display_name', 'name', 'notes', 'email', 'status', 'started', 'completed',
    'bookmark', 'position', 'started_at', 'completed_at', 'revoked_at'})


def _portable_export_payload(logical, value):
    # Ordinary downloads carry hydrated product values, never provider pointers
    # or participant account bindings. Sealed private recovery does not use this.
    if isinstance(value, dict):
        return {key: _portable_export_payload('', child) for key, child in value.items()
                if key not in _PROVIDER_FIELDS and not key.startswith('_bm_')
                and not (logical == 'review_participant' and key in _PARTICIPANT_AUTHORITY)}
    if isinstance(value, list):
        return [_portable_export_payload('', child) for child in value]
    return value


def _portable_import_payload(logical, data, *, ids=None, entity_id=None):
    """Untrusted public records cannot smuggle provider access or identities."""
    from hosted.domain_store import _HYBRID_PAYLOAD_FIELDS
    result = dict(data)
    if logical == 'review_participant':
        if _PARTICIPANT_AUTHORITY & set(result):
            raise ValueError('HOSTED_PACKAGE_PARTICIPANT_AUTHORITY_FORBIDDEN')
        if set(result) - _PARTICIPANT_PORTABLE:
            raise ValueError('HOSTED_PACKAGE_PARTICIPANT_SCHEMA_UNSUPPORTED')
    stack = [result]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            if any(key in _PROVIDER_FIELDS or str(key).startswith('_bm_') for key in value):
                raise ValueError('HOSTED_PACKAGE_PROVIDER_FIELDS_FORBIDDEN')
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    fields = _HYBRID_PAYLOAD_FIELDS.get(logical)
    image_body = logical == 'manuscript_block' and result.get('block_type') == 'image' and bool(result.get('image_path'))
    if fields and not image_body and not any(key in result and result[key] is not None for key in fields):
        raise ValueError('HOSTED_PACKAGE_BODY_MISSING')
    if ids is not None and entity_id is not None:
        expected = {**ids, _ID_FIELDS[logical][0]: entity_id, 'id': entity_id}
        for key, value in expected.items():
            if key != 'project_id' and key in result and str(result[key]) != str(value):
                raise ValueError('HOSTED_PACKAGE_PAYLOAD_IDENTITY_MISMATCH')
    return result


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
        records=self._collect_authorized_records(project_id, user_id)
        return {logical:[{**item,'data':_portable_export_payload(logical,item['data'])} for item in rows]
                for logical,rows in records.items()}

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
        written_assets=set();asset_paths={}
        with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
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
                archive_name='assets/'+digest+ext
                api_path='/api/assets/'+name
                asset_paths.setdefault(archive_name,[]).append(api_path)
                if archive_name not in written_assets:
                    archive.writestr(archive_name,payload)
                    written_assets.add(archive_name)
            # Logical asset names are product data, not GCS pointers. Canonical
            # packages need no extra mapping; aliases share one verified blob.
            if any(paths!=['/api/assets/img_'+name.split('/')[-1][:24]+'.'+name.rsplit('.',1)[-1]]
                   for name,paths in asset_paths.items()):
                manifest['asset_paths']={name:sorted(set(paths)) for name,paths in asset_paths.items()}
            archive.writestr('manifest.json',json.dumps(manifest,sort_keys=True,separators=(',',':')))
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
                if logical=='annotation':continue
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
            self._stage_annotation_records(pid,entities.get('annotation') or [])
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
                operation=operation or operation_id('project_import')
                self._reserve_import(operation=operation,user_id=user_id,digest=hashlib.sha256(raw).hexdigest(),name_override=name_override)
                manifest=json.loads(archive.read('manifest.json').decode('utf-8'))
                with package_import_staging(operation,user_id):
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

    def _prepare_import_entity(self, logical:str, ids:Mapping[str,str], data:Mapping[str,Any], *, entity_id:str,read_bundle=None):
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
        portable=(dict(data) if logical=='project_asset' else
                  _portable_import_payload(logical,data,ids=full_ids,entity_id=entity_id))
        public=_canonical_payload(self.domain._payload_with_identity(logical,portable,entity_id))
        _reject_managed_create_fields(public)
        hybrid=self.domain._hybrid_payload(logical,public)
        if hybrid is not None:
            structured,encoded=hybrid
            stored=dict(structured)
            stored['payload_object']=self.domain._publish_hybrid(logical,full_ids,encoded)
            if read_bundle is not None:
                # Expand/contract rollout: old releases MUST still read canonical
                # per-block pointers. The new bundle is an optional, hash-bound
                # read accelerator, never the sole copy of a paragraph.
                stored['_bm_read_bundle']={**read_bundle,'source_sha256':stored['payload_object']['object_sha256']}
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
        return path,stored

    def _stage_entity(self, logical:str, ids:Mapping[str,str], data:Mapping[str,Any], *, entity_id:str, unpublished_only:bool=False) -> None:
        path,stored=self._prepare_import_entity(logical,ids,data,entity_id=entity_id)
        def create(tx):
            if unpublished_only and tx.get(('projects',ids['project_id'])) is not None:
                raise ValueError('HOSTED_IMPORT_PROJECT_ALREADY_PUBLISHED')
            old=tx.get(path)
            if old is not None:
                if (old.data.get('_bm_create_operation')==stored['_bm_create_operation']
                        and old.data.get('_bm_create_fingerprint')==stored['_bm_create_fingerprint']):
                    return
                raise ValueError('HOSTED_PACKAGE_ENTITY_COLLISION')
            tx.create(path,stored)
        self.documents.run_transaction(create)

    def _stage_annotation_records(self,pid,entries):
        """Rebuild logical ranges under an UNPUBLISHED import prefix only.

        Public packages carry no trusted object pointers. Preserve opaque IDs,
        verify fresh immutable bodies, and create project membership LAST.
        """
        from hosted.editing_packs import PackedAnnotations, ITEM, MAX_PACKS, MAX_ROWS, MAX_TOTAL_DECODED, FIELD
        from api.annotations.editing_lexicon import PASSES
        if not entries:return
        if self.documents.get(('projects',pid)) is not None:raise ValueError('HOSTED_IMPORT_PROJECT_ALREADY_PUBLISHED')
        blocks={row.path[-1]:dict(row.data) for row in self.documents.query_collection(('projects',pid,'blocks'))}
        chapters={row.path[-1]:dict(row.data) for row in self.documents.query_collection(('projects',pid,'chapters'))}
        labels={row.path[-1] for row in self.documents.query_collection(('projects',pid,'markup_labels'))}
        groups={};seen=set();texts={}
        for item in entries:
            if not isinstance(item,dict) or not isinstance(item.get('ids'),dict) or not isinstance(item.get('data'),dict):
                raise ValueError('HOSTED_PACKAGE_INVALID')
            iid=require_opaque_id(item['ids'].get('annotation_id'),label='annotation_id')
            if iid in seen:raise ValueError('HOSTED_PACKAGE_ENTITY_COLLISION')
            seen.add(iid)
            data={key:_restore_safe(value) for key,value in item['data'].items()};data.pop('project_id',None)
            match=ITEM.fullmatch(iid);kind=str(data.get('body') or '').removeprefix('editing-search/v1:')
            if match is None or kind not in PASSES or data.get('annotation_type')!='highlight':
                self._stage_entity('annotation',{'project_id':pid},data,entity_id=iid,unpublished_only=True)
                continue
            block=blocks.get(data.get('block_id'));lid=data.get('label_id')
            if block is None or lid not in labels:raise ValueError('HOSTED_PACKAGE_ANNOTATION_TARGET_INVALID')
            chapter=chapters.get(block.get('chapter_id'))
            if chapter is None:raise ValueError('HOSTED_PACKAGE_ANNOTATION_CONTEXT_INVALID')
            tab=require_opaque_id(chapter.get('tab_id'),label='tab_id')
            if block.get('tab_id') not in (None,tab):raise ValueError('HOSTED_PACKAGE_ANNOTATION_CONTEXT_INVALID')
            hid='epack_'+match[1]
            key=(tab,lid,kind);stamp=block['created_at']
            group=groups.setdefault(hid,{'key':key,'stamp':stamp,'rows':[]})
            if group['key']!=key:raise ValueError('HOSTED_PACKAGE_ANNOTATION_CONTEXT_INVALID')
            if not data.get('stale'):
                if block['id'] not in texts:texts[block['id']]=self.domain._hydrate('manuscript_block',block).get('text_content') or ''
                start,end=data.get('start_offset'),data.get('end_offset');text=texts[block['id']]
                if (type(start) is int and type(end) is int and 0<=start<=end<=len(text)
                        and text[start:end]==data.get('selected_text')):
                    data['source_generation']=block['generation']
                else:data['stale']=True
            # Native packages have no provider generations. Fresh ranges were
            # verified above; stale ranges stay explicitly stale after rebinding.
            data.setdefault('source_generation',block['generation'])
            group['rows'].append({**data,'id':iid,'annotation_id':iid,'project_id':pid,'chapter_id':None,
                'created_at':stamp,'updated_at':stamp,'generation':1})
        if len(groups)>MAX_PACKS or sum(len(group['rows']) for group in groups.values())>MAX_ROWS:
            raise ValueError('HOSTED_PACKAGE_ANNOTATION_LIMIT')
        packs=PackedAnnotations(self.domain)
        decoded=0
        for hid,group in groups.items():
            tab,lid,kind=group['key'];stamp=group['stamp']
            head=dict(id=hid,annotation_id=hid,project_id=pid,tab_id=tab,label_id=lid,scan=kind,
                annotation_type='editing-pack/v1',generation=1,created_at=stamp,updated_at=stamp)
            staged=packs.stage(pid,head,group['rows'],available_bytes=MAX_TOTAL_DECODED-decoded);packs.rows(pid,staged)
            decoded+=staged[FIELD]['decoded_bytes']
            if decoded>MAX_TOTAL_DECODED:raise ValueError('HOSTED_PACKAGE_ANNOTATION_LIMIT')
            def commit(tx):
                if tx.get(('projects',pid)) is not None:raise ValueError('HOSTED_IMPORT_PROJECT_ALREADY_PUBLISHED')
                path=('projects',pid,'annotations',hid);old=tx.get(path)
                if old is not None:
                    if dict(old.data)!=staged:raise ValueError('HOSTED_PACKAGE_ENTITY_COLLISION')
                    return
                tx.create(path,staged)
            self.documents.run_transaction(commit)

    def stage_import_batch(self,pid:str,entries:list[dict],*,bundle_blocks:bool=False) -> None:
        """Bounded object-first staging under an UNPUBLISHED project only.

        No membership/authority is created here. All object integrity checks finish
        before the single metadata transaction; retries compare exact fingerprints.
        Never use this path to mutate an existing, visible project.
        """
        pid=require_project_id(pid)
        if not 1<=len(entries)<=64:raise ValueError('HOSTED_IMPORT_BATCH_SIZE_INVALID')
        if self.documents.get(('projects',pid)) is not None:
            raise ValueError('HOSTED_IMPORT_PROJECT_ALREADY_PUBLISHED')
        pointers={}
        if bundle_blocks:
            if self.domain.encrypted_objects is not None or self.domain.encrypted_fields is not None:
                raise ValueError('HOSTED_ENCRYPTED_PACKAGE_IMPORT_NOT_READY')
            # One optional verified read bundle per bounded batch. Canonical
            # per-block objects remain readable by the previous release during
            # rollback. A future writer cutover needs a bundle-capable baseline.
            bodies={entry['id']:{field:entry['data'].get(field,'') for field in ('content_html','text_content')}
                for entry in entries if entry['entity']=='manuscript_block'}
            if bodies:
                encoded=gzip.compress(json.dumps({'entries':bodies},sort_keys=True,
                    separators=(',',':'),ensure_ascii=False).encode(),mtime=0)
                if len(encoded)>8*1024*1024:raise ValueError('HOSTED_IMPORT_BATCH_TOO_LARGE')
                bid='b_bundle_'+hashlib.sha256('\0'.join(sorted(bodies)).encode()).hexdigest()[:24]
                pointer=self.domain._publish_hybrid('manuscript_block',{'project_id':pid,'block_id':bid},encoded)
                pointers={eid:{**pointer,'bundle_entry':eid} for eid in bodies}
        def prepare(entry):
            return self._prepare_import_entity(entry['entity'],{'project_id':pid},
                entry['data'],entity_id=entry['id'],read_bundle=pointers.get(entry['id']))
        with ThreadPoolExecutor(max_workers=8) as pool:
            # Preserve both authenticated session and unpublished import scope.
            # A fresh Context per item avoids sharing an entered Context between
            # workers, and never leaks authority into a reused worker thread.
            futures=[pool.submit(copy_context().run,prepare,entry) for entry in entries]
            prepared=[future.result() for future in futures]
        if len({path for path,_ in prepared})!=len(prepared):
            raise ValueError('HOSTED_IMPORT_DUPLICATE_ENTITY')
        def stage(tx):
            # Recheck in the SAME transaction, before writes. A delayed step
            # must never write canonical content after final publication.
            if tx.get(('projects',pid)) is not None:
                raise ValueError('HOSTED_IMPORT_PROJECT_ALREADY_PUBLISHED')
            paths=[path for path,_ in prepared]
            batch_get=getattr(tx,'get_many',None)
            previous=batch_get(paths) if batch_get is not None else [tx.get(path) for path in paths]
            for old,(_,stored) in zip(previous,prepared):
                if old is not None and any(old.data.get(key)!=stored[key] for key in
                        ('_bm_create_operation','_bm_create_fingerprint')):
                    raise ValueError('HOSTED_PACKAGE_ENTITY_COLLISION')
            for old,(path,stored) in zip(previous,prepared):
                if old is None:tx.create(path,stored)
        self.documents.run_transaction(stage)

    def _stage_asset(self,pid:str,user_id:str,data:bytes,extension:str,*,expected_digest:str|None=None,source_path:str|None=None) -> None:
        ext='.'+str(extension or '').lower().lstrip('.')
        try:
            info=inspect_hosted_image_bytes(bytes(data),ext,byte_limit=HOSTED_DEFAULT_IMAGE_BYTES)
        except UnsafeImage as exc:
            raise ValueError('HOSTED_PACKAGE_ASSET_UNSAFE') from exc
        import hashlib
        digest=hashlib.sha256(data).hexdigest()
        if expected_digest is not None and digest!=str(expected_digest):
            raise ValueError('HOSTED_PACKAGE_ASSET_HASH_MISMATCH')
        canonical_name=f'img_{digest[:24]}{info.extension}'
        name=canonical_name
        if source_path is not None:
            import re
            if not re.fullmatch(r'/api/assets/[A-Za-z0-9_-]{1,160}\.(?:png|jpg|webp)',source_path):
                raise ValueError('HOSTED_PACKAGE_ASSET_PATH_INVALID')
            name=source_path.removeprefix('/api/assets/')
        asset_id='asset_'+(digest if name==canonical_name else hashlib.sha256(source_path.encode()).hexdigest())
        publication=ImmutableObjectPublisher(self.objects).publish_bytes(
            project_asset_key(pid,digest,info.extension),data,max_bytes=HOSTED_DEFAULT_IMAGE_BYTES,
            content_type=info.media_type,metadata={'breadmaker-entity':'project_asset'},
        )
        obj=publication.object
        self._stage_entity(
            'project_asset',{'project_id':pid},
            {'api_path':'/api/assets/'+name,'name':name,'sha256':digest,'object_name':obj.name,'object_generation':obj.generation,'object_sha256':obj.sha256,'object_size':obj.size},
            entity_id=asset_id,
            unpublished_only=True,
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
        from hosted.native_package import _prepare_hosted_archive
        pid=self._new_import_project_id(import_operation)
        plan=_prepare_hosted_archive(archive,manifest,pid,name_override=name_override,excluded=_SKIP)
        try:
            self._stage_native_plan(pid,user_id,plan)
            return self._finish_import(pid,plan['title'],user_id)
        except Exception:
            self._fail_import(pid,user_id);raise

    def _import_legacy_v2(self,archive:zipfile.ZipFile,manifest:Mapping[str,Any],user_id:str,*,name_override:str|None,import_operation:str|None=None):
        from hosted.native_package import prepare_native_archive
        pid=self._new_import_project_id(import_operation)
        plan=prepare_native_archive(archive,manifest,pid,name_override=name_override,excluded=_SKIP)
        try:
            self._stage_native_plan(pid,user_id,plan)
            return self._finish_import(pid,plan['title'],user_id)
        except Exception:
            self._fail_import(pid,user_id);raise

    def _stage_native_plan(self,pid:str,user_id:str,plan) -> None:
        for item in plan['records']:
            self._stage_entity(item['entity'],item['parents'],item['data'],entity_id=item['id'])
        self._stage_annotation_records(pid,plan['annotations'])
        for asset in plan['assets']:
            self._stage_asset(pid,user_id,asset['data'],asset['extension'],expected_digest=asset['sha256'],source_path=asset['path'])

    def _translate_legacy_tables(self,con:sqlite3.Connection,pid:str,user_id:str) -> None:
        from hosted.native_package import plan_native_records
        # Resolve the entire relational graph first, including nested parent
        # paths. A late invalid Map/Canvas cannot leave thousands of uploads.
        planned=plan_native_records(con,pid,excluded=_SKIP)
        annotations=[]
        for item in planned:
            if item['entity']=='annotation':
                annotations.append({'ids':{**item['parents'],'annotation_id':item['id']},'data':{**item['data'],'id':item['id']}})
            else:
                self._stage_entity(item['entity'],item['parents'],item['data'],entity_id=item['id'])
        self._stage_annotation_records(pid,annotations)
