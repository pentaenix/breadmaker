from __future__ import annotations

"""M12L: operator-only encrypted recovery into a NEW, unpublished project.

No HTTP route, scheduler, runtime bootstrap or local SQLite dependency. Recovery
bucket admission and destination-actor admission are distinct mandatory callbacks.
The source snapshot must be an authenticated PRIVATE encrypted source; encrypted
containers of public/legacy source ZIPs are NOT eligible for this restore path.

Restore stages records/objects under an unowned random destination project ID,
using NEW destination/path-bound ciphertext, verifies the staged bytes, and only
then atomically publishes project+owner authority. Incomplete staging is never
visible. Failed operations leave bounded orphan candidates for later retention
inventory, rather than automatically deleting potentially needed recovery data.
Unsupported hybrids, participants and assets FAIL before project publication.
"""

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from typing import Callable, Mapping, Any

from hosted.domain_store import _HYBRID_PAYLOAD_FIELDS, _check_encrypted_block_metadata, _json_gunzip
from hosted.encrypted_recovery import HostedEncryptedRecoveryReader
from hosted.firestore.contracts import DocumentAlreadyExists, HostedValidationError, ProjectUnavailable
from hosted.firestore.ids import new_project_id, require_project_id, require_user_id, require_opaque_id
from hosted.firestore.model_registry import MODELS
from hosted.firestore.repositories import render_model_path, _canonical_payload, _reject_managed_create_fields
from hosted.packages import HostedProjectPackageService, _fields, _restore_safe, _public, _MAX_PACKAGE
from hosted.storage.encrypted_objects import HostedEncryptedObjectStore, EncryptedObjectReference
from hosted.storage.assets import HostedAssetStore
from security.image_security import inspect_hosted_image_bytes, HOSTED_DEFAULT_IMAGE_BYTES
from hosted.security.envelope import FORMAT
from hosted.recovery_drills import _inventory


class HostedEncryptedLiveRestoreError(ValueError):
    """Safe codes only, never decrypted manuscript text in exceptions."""


@dataclass(frozen=True)
class HostedEncryptedLiveRestoreResult:
    destination_project_id: str
    restored_entity_count: int
    source_project_id: str
    snapshot_id: str


_MAX_RECORDS = 12000

# A recovered Review contains *historical feedback*, not reusable invitation
# or reviewer-session authority. The legacy project did not implement hosted
# external reviewer admission. Explicitly strip known access capabilities,
# reject unknown participant schema and start every restored participant in
# revoked state. Writers must re-authorize/re-invite in the new workspace.
_REVIEW_PARTICIPANT_PORTABLE = frozenset({
    'id', 'participant_id', 'review_id', 'project_id', 'display_name', 'name',
    'notes', 'email', 'status', 'started', 'completed', 'bookmark', 'position',
})
_REVIEW_PARTICIPANT_AUTHORITY = frozenset({
    'invite_token', 'invitation_token', 'access_token', 'session_token',
    'session_id', 'reviewer_session_id', 'auth_token', 'invite_url',
    'invitation_id', 'invite_id', 'invited_user_id', 'reviewer_user_id',
    'external_user_id', 'magic_link', 'secret',
})


def _portable_review_participant(source: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(source, Mapping):
        raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_REVIEWER_SCHEMA_INVALID')
    unknown = set(source) - _REVIEW_PARTICIPANT_PORTABLE - _REVIEW_PARTICIPANT_AUTHORITY
    if unknown:
        raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_REVIEWER_SCHEMA_UNSUPPORTED')
    # Snapshot-derived feedback stays in its own encrypted records; session
    # capabilities, regardless of apparent validity, never cross project IDs.
    restored = {key: value for key, value in source.items()
                if key not in _REVIEW_PARTICIPANT_AUTHORITY}
    restored['status'] = 'revoked'
    restored['started'] = False
    return restored


class HostedEncryptedLiveRestorer:
    """Explicitly privileged, fail-closed destination-scoped recovery service.

    The injected reader authorizes the source *before* any bucket/KMS read. The
    separately injected callback must attest the destination operator's ability
    to create a project for this user, not merely that a user_id string exists.
    Staging encryption is scoped to one random unowned project ID for this call;
    it must never be reused as an application-wide bypass of membership checks.
    """

    def __init__(self, reader: HostedEncryptedRecoveryReader, destination_projects,
                 *, authorize_destination: Callable[[str], bool]):
        if not isinstance(reader, HostedEncryptedRecoveryReader) or not callable(authorize_destination):
            raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_AUTH_REQUIRED')
        self.reader = reader
        self.projects = destination_projects
        self._authorize_destination = authorize_destination

    def restore(self, source_project_id: str, source_owner_user_id: str,
                destination_owner_user_id: str, *, operation: str,
                restore_operation: str) -> HostedEncryptedLiveRestoreResult:
        src = require_project_id(source_project_id)
        src_owner = require_user_id(source_owner_user_id)
        dest_owner = require_user_id(destination_owner_user_id)
        op = require_opaque_id(restore_operation, label='restore_operation')
        if self._authorize_destination(dest_owner) is not True:
            raise PermissionError('HOSTED_PRIVATE_RESTORE_DESTINATION_DENIED')
        domain = self.projects.domain
        if domain.encrypted_fields is None or domain.encrypted_objects is None:
            raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_CODECS_REQUIRED')
        # Fail before touching ciphertext if the destination account isn't present.
        self.projects.users.get_self(dest_owner)
        # This reader independently verifies authenticated source operator access,
        # manifest, every ciphertext chunk, pinned generation and total digest.
        raw = self.reader.read_offline(src, src_owner, operation=operation)
        _, _, sid = self.reader._snapshot(src, src_owner, operation)
        committed, _ = self.reader._read_manifest(src, src_owner, sid)
        if committed['source_private'] is not True:
            raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_PRIVATE_SOURCE_REQUIRED')
        if not 1 <= len(raw) <= _MAX_PACKAGE:
            raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_SIZE_INVALID')
        _source_inventory, source_assets = _inventory(raw, src, allow_private=True)

        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                if set(archive.namelist()) != {'manifest.json', 'project.json'} | set(source_assets):
                    raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_MEMBERS_INVALID')
                manifest = json.loads(archive.read('manifest.json'))
                document = json.loads(archive.read('project.json'))
                asset_bytes = {name: archive.read(name) for name in source_assets}
            title = str(manifest['title']).strip()
            entities = document['entities']
        except (KeyError, TypeError, UnicodeError, ValueError, zipfile.BadZipFile):
            raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_ARCHIVE_INVALID') from None
        if not title or len(title) > 240 or not isinstance(entities, dict):
            raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_ARCHIVE_INVALID')
        from hosted.checkpoint_dependencies import require_checkpoint_assets_present
        # Validate historical image dependency closure before staging a single
        # Firestore row or ciphertext object under the destination namespace.
        require_checkpoint_assets_present(entities)
        total = sum(len(items) for items in entities.values())
        if total > _MAX_RECORDS:
            raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_TOO_MANY_RECORDS')
        for logical in entities:
            if logical != 'manuscript_block' and not domain.encrypted_fields.protects(logical):
                raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_MODEL_UNSUPPORTED')
        # An operation that failed *after* the atomic authority publication is
        # recoverable: the existing project is already visible and the receipt
        # can be repaired without producing a second project. By contrast, a
        # receipt left by incomplete staging is never a publish permit.
        receipt_path = ('hosted_private_restore_operations', op)
        manifest_ref = self.reader._read_manifest(src, src_owner, sid)[1]

        def complete(tx, project_id: str):
            previous = tx.get(receipt_path)
            if previous is None:
                raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_RECEIPT_INVALID')
            info = dict(previous.data)
            if (info.get('destination_project_id') != project_id
                    or info.get('destination_owner_user_id') != dest_owner
                    or info.get('source_project_id') != src
                    or info.get('source_owner_user_id') != src_owner
                    or info.get('source_snapshot_id') != sid
                    or info.get('source_manifest_ciphertext_sha256') != manifest_ref.ciphertext_sha256
                    or info.get('restored_entity_count') != total
                    or info.get('status') not in {'staging', 'committed'}):
                raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_RECEIPT_INVALID')
            info['status'] = 'committed'
            tx.set(receipt_path, info)

        previous = self.projects.documents.get(receipt_path)
        if previous is not None:
            info = dict(previous.data)
            previous_pid = info.get('destination_project_id')
            # Exact request binding is required even if an operator has access
            # to several source projects or destination accounts.
            if (not isinstance(previous_pid, str)
                    or info.get('destination_owner_user_id') != dest_owner
                    or info.get('source_project_id') != src
                    or info.get('source_owner_user_id') != src_owner
                    or info.get('source_snapshot_id') != sid
                    or info.get('source_manifest_ciphertext_sha256') != manifest_ref.ciphertext_sha256
                    or info.get('restored_entity_count') != total
                    or info.get('status') != 'staging'):
                raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_OPERATION_REUSED')
            # Authority exists only after the entire prior staging + semantic
            # readback succeeded. A stranded staging receipt is not sufficient.
            try:
                published = self.projects.authority.get_for_user(previous_pid, dest_owner)
            except ProjectUnavailable:
                raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_OPERATION_REUSED') from None
            if (published.get('_bm_create_operation') != 'private_restore_' + previous_pid[:24]
                    or published.get('title') != title):
                raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_OPERATION_REUSED')
            self.projects.documents.run_transaction(lambda tx: complete(tx, previous_pid))
            return HostedEncryptedLiveRestoreResult(previous_pid, total, src, sid)

        destination_pid = new_project_id()
        # Validate path identity, duplicates and expected parent records *before*
        # reserving an operation or writing staged ciphertext.
        ordered: list[tuple[str, dict[str, str], dict[str, Any]]] = []
        path_set: set[tuple[str, ...]] = set()
        referenced_assets: set[str] = set()
        for logical in sorted(entities, key=lambda name: (len(MODELS[name].path_template.split('/')), name)):
            model = MODELS[logical]
            fields = _fields(model.path_template)
            for item in entities[logical]:
                raw_ids = item['ids']
                if (not isinstance(raw_ids, dict) or set(raw_ids) != set(fields)
                        or raw_ids.get('project_id') != src):
                    raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_ROW_INVALID')
                ids = {key: require_opaque_id(val, label=key) for key, val in raw_ids.items() if key != 'project_id'}
                # project id is a new namespace; all other record IDs survive.
                data = _restore_safe(item['data'])
                if not isinstance(data, dict):
                    raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_ROW_INVALID')
                path = render_model_path(model, project_id=src, **ids)
                if path in path_set:
                    raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_DUPLICATE_ROW')
                path_set.add(path)
                # Never transfer another account's authority via nested payloads.
                if logical == 'review_participant':
                    data = _portable_review_participant(data)
                if logical in {'member_bookmark', 'member_ui_state'}:
                    if ids.get('user_id') != src_owner:
                        raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_FOREIGN_MEMBER_STATE')
                    ids['user_id'] = dest_owner
                    if logical == 'member_bookmark':
                        if data.get('id', src_owner) != src_owner:
                            raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_MEMBER_ID_INVALID')
                        data['id'] = dest_owner
                data = self._translate_content(data, src, src_owner, dest_owner, destination_pid)
                if logical == 'project_asset':
                    name = data.get('name')
                    digest = data.get('sha256')
                    if not isinstance(name, str) or not isinstance(digest, str):
                        raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_ASSET_ROW_INVALID')
                    try:
                        HostedAssetStore._asset_name(name)
                    except Exception:
                        raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_ASSET_ROW_INVALID') from None
                    ext = '.' + name.rsplit('.', 1)[1]
                    asset_entry = 'assets/' + digest + ext
                    if (asset_entry not in asset_bytes or
                            hashlib.sha256(asset_bytes[asset_entry]).hexdigest() != digest or
                            data.get('api_path') != '/api/assets/' + name):
                        raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_ASSET_ROW_INVALID')
                    try:
                        inspect_hosted_image_bytes(asset_bytes[asset_entry], ext,
                                                   byte_limit=HOSTED_DEFAULT_IMAGE_BYTES)
                    except Exception:
                        raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_ASSET_ROW_INVALID') from None
                    referenced_assets.add(asset_entry)
                ordered.append((logical, ids, data))
        if referenced_assets != set(source_assets):
            raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_ASSET_INVENTORY_MISMATCH')
        for logical, ids, _data in ordered:
            path = render_model_path(MODELS[logical], project_id=src, **ids)
            if (len(path) > 4 and path[:-2] not in path_set
                    and logical not in {'member_bookmark', 'member_ui_state'}):
                raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_ORPHAN_REFERENCE')

        # KMS and external GCS calls must be outside provider transaction
        # callbacks; Firestore can replay a transaction callback at any point.
        stage_timestamp = domain.clock.now_iso()
        def reserve(tx):
            existing = tx.get(receipt_path)
            if existing is not None:
                raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_OPERATION_REUSED')
            tx.create(receipt_path, {
                'destination_project_id': destination_pid, 'destination_owner_user_id': dest_owner,
                'source_snapshot_id': sid, 'source_project_id': src,
                'source_owner_user_id': src_owner,
                'source_manifest_ciphertext_sha256': manifest_ref.ciphertext_sha256,
                'restored_entity_count': total,
                # Immutable UTC receipt clock for retention planning. Previously
                # staged operations without this proof are never GC-eligible.
                'staging_started_at': stage_timestamp,
                'status': 'staging',
            })
        self.projects.documents.run_transaction(reserve)
        # This store is usable only for the unpublished project of this one
        # privileged call; the canonical project's object store still enforces
        # normal membership and can read the new ciphertext AFTER final commit.
        scoped = HostedEncryptedObjectStore(
            self.projects.objects, domain.encrypted_objects._codec,
            authorize=lambda pid, uid: pid == destination_pid and uid == dest_owner,
        )
        expected: dict[str, list[str]] = {}
        asset_pointer_fields = frozenset({'object_name','object_generation',
            'object_sha256','object_size','encryption_format','encryption_operation'})

        def portable(logical, content):
            row = _public(content)
            if logical == 'project_asset':
                return {key: value for key, value in row.items() if key not in asset_pointer_fields}
            return row

        stamp = domain.clock.now_iso()
        for logical, ids, content in ordered:
            try:
                model = MODELS[logical]
                scoped_ids = {'project_id': destination_pid, **ids}
                entity_field = _fields(model.path_template)[-1]
                entity_id = ids[entity_field]
                path = render_model_path(model, **scoped_ids)
                payload = _canonical_payload(content)
                _reject_managed_create_fields(payload)
                if payload.get('project_id', destination_pid) != destination_pid:
                    raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_IDENTITY_INVALID')
                payload.pop('project_id', None)
                if payload.get('id', entity_id) != entity_id or payload.get(entity_field, entity_id) != entity_id:
                    raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_IDENTITY_INVALID')
                payload.setdefault('id', entity_id)
                payload.setdefault(entity_field, entity_id)
                if logical == 'project_asset':
                    entry = 'assets/' + payload['sha256'] + '.' + payload['name'].rsplit('.', 1)[1]
                    object_op = 'restore_' + op[:70]
                    ref = scoped.publish(
                        project_id=destination_pid, user_id=dest_owner,
                        entity='project_asset', record_id=entity_id,
                        operation_id=object_op, plaintext=asset_bytes[entry])
                    stored = domain.encrypted_fields.seal(logical, scoped_ids, {
                        **payload, 'object_name':ref.name,
                        'object_generation':ref.generation,
                        'object_sha256':ref.ciphertext_sha256,
                        'object_size':ref.ciphertext_size,
                        'encryption_format':FORMAT,'encryption_operation':object_op})
                    opened = domain.encrypted_fields.open(logical, scoped_ids, stored)
                    reopened = scoped.read(project_id=destination_pid, user_id=dest_owner,
                        entity='project_asset', record_id=entity_id,
                        operation_id=object_op, reference=ref)
                    if reopened != asset_bytes[entry]:
                        raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_ASSET_MISMATCH')
                elif logical in _HYBRID_PAYLOAD_FIELDS:
                    hybrid = domain._hybrid_payload(logical, payload)
                    if hybrid is None:
                        raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_BLOCK_INVALID')
                    structured, encoded = hybrid
                    if logical == 'manuscript_block':
                        _check_encrypted_block_metadata(structured)
                    object_op = 'restore_' + op[:70]
                    ref = scoped.publish(
                        project_id=destination_pid, user_id=dest_owner,
                        entity=logical,
                        record_id=domain._encrypted_hybrid_record_id(logical, scoped_ids),
                        operation_id=object_op, plaintext=encoded,
                    )
                    stored = {**structured, 'payload_object': {
                        'object_name': ref.name, 'object_generation': ref.generation,
                        'object_sha256': ref.ciphertext_sha256,
                        'object_size': ref.ciphertext_size,
                        'encryption_format': FORMAT, 'encryption_operation': object_op,
                    }}
                    if logical != 'manuscript_block':
                        stored = domain.encrypted_fields.seal(logical, scoped_ids, stored)
                        private_structured = domain.encrypted_fields.open(logical, scoped_ids, stored)
                    else:
                        private_structured = stored
                    # Roundtrip before a project authority can exist.
                    opened = {**{k: v for k, v in private_structured.items()
                                 if k != 'payload_object'}, **_json_gunzip(scoped.read(
                        project_id=destination_pid, user_id=dest_owner,
                        entity=logical,
                        record_id=domain._encrypted_hybrid_record_id(logical, scoped_ids),
                        operation_id=object_op, reference=ref,
                    ))}
                else:
                    stored = domain.encrypted_fields.seal(logical, scoped_ids, payload)
                    opened = domain.encrypted_fields.open(logical, scoped_ids, stored)
                if _canonical(portable(logical, opened)) != _canonical(portable(logical, payload)):
                    raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_READBACK_MISMATCH')
                stored.update({'generation': 1, 'created_at': stamp, 'updated_at': stamp,
                               '_bm_create_operation': 'private_restore',
                               '_bm_create_fingerprint': hashlib.sha256(
                                   json.dumps(stored, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()})
                self.projects.documents.run_transaction(lambda tx: tx.create(path, stored))
                expected.setdefault(logical, []).append(_canonical([
                    {key: val for key, val in scoped_ids.items() if key != 'project_id'},
                    portable(logical, payload),
                ]))
            except (HostedEncryptedLiveRestoreError, PermissionError):
                raise
            except Exception:
                raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_STAGING_FAILED') from None
        # Before publication, independently read back every staged Firestore
        # record and decrypt it at the NEW project/path scope; block reads use
        # their precise destination GCS generations. Never trust write ACK alone.
        observed: dict[str, list[str]] = {}
        for logical, ids, payload in ordered:
            scoped_ids = {'project_id': destination_pid, **ids}
            path = render_model_path(MODELS[logical], **scoped_ids)
            row = self.projects.documents.get(path)
            if row is None:
                raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_STAGING_LOST')
            if logical == 'project_asset':
                hydrated = domain.encrypted_fields.open(logical, scoped_ids, row.data)
                asset_entry = 'assets/' + hydrated['sha256'] + '.' + hydrated['name'].rsplit('.', 1)[1]
                ref = EncryptedObjectReference(
                    hydrated['object_name'], hydrated['object_generation'],
                    hydrated['object_sha256'], hydrated['object_size'])
                rebuilt = scoped.read(project_id=destination_pid,user_id=dest_owner,
                    entity='project_asset',record_id=ids['asset_id'],
                    operation_id=hydrated['encryption_operation'],reference=ref)
                if rebuilt != asset_bytes[asset_entry]:
                    raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_ASSET_MISMATCH')
            elif logical in _HYBRID_PAYLOAD_FIELDS:
                decrypted = (domain.encrypted_fields.open(logical, scoped_ids, row.data)
                             if logical != 'manuscript_block' else row.data)
                ptr = decrypted['payload_object']
                hydrated = {key: value for key, value in decrypted.items() if not key.startswith('_bm_')}
                hydrated.pop('payload_object', None)
                hydrated.update(_json_gunzip(scoped.read(
                    project_id=destination_pid, user_id=dest_owner,
                    entity=logical,
                    record_id=domain._encrypted_hybrid_record_id(logical, scoped_ids),
                    operation_id=ptr['encryption_operation'],
                    reference=EncryptedObjectReference(ptr['object_name'], ptr['object_generation'],
                                                       ptr['object_sha256'], ptr['object_size']),
                )))
            else:
                hydrated = domain.encrypted_fields.open(logical, scoped_ids, row.data)
            observed.setdefault(logical, []).append(_canonical([
                ids, portable(logical, hydrated),
            ]))
        if {k: sorted(v) for k, v in expected.items()} != {k: sorted(v) for k, v in observed.items()}:
            raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_SEMANTIC_MISMATCH')
        if self.projects.documents.get(('projects', destination_pid)) is not None:
            raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_PREMATURE_VISIBILITY')
        # Existing authority create commits project + owner membership together.
        # Until this line succeeds the staged rows cannot be reached through API.
        self.projects.authority.create(title, owner_user_id=dest_owner,
                                       operation_id='private_restore_' + destination_pid[:24],
                                       project_id=destination_pid)
        # Recovery receipt is operational metadata only. On retry a committed
        # operation returns a project only after its canonical owner check.
        self.projects.documents.run_transaction(lambda tx: complete(tx, destination_pid))
        # This best-effort convenience is intentionally AFTER the atomic commit.
        # Failure here may not hide a successfully restored project.
        try:
            self.projects._update_last_project(dest_owner, destination_pid)
        except Exception:
            pass
        return HostedEncryptedLiveRestoreResult(destination_pid, total, src, sid)

    @staticmethod
    def _translate_content(value: Any, src: str, src_owner: str, dest_owner: str,
                           destination_pid: str) -> Any:
        # Never transplant project/owner authority. Other user-bound fields must
        # be re-bound to the original owner or rejected, not quietly granted.
        def convert(item, key=''):
            if isinstance(item, dict):
                return {str(k): convert(v, str(k)) for k, v in item.items()}
            if isinstance(item, list):
                return [convert(v, key) for v in item]
            if key == 'project_id':
                if item != src:
                    raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_FOREIGN_PROJECT_REF')
                return destination_pid
            if key in {'user_id', 'owner_user_id', 'writer_user_id', 'created_by_user_id'}:
                if item != src_owner:
                    raise HostedEncryptedLiveRestoreError('HOSTED_PRIVATE_RESTORE_FOREIGN_USER_REF')
                return dest_owner
            return item
        return convert(value)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
