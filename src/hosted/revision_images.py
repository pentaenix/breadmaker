"""Inactive image helpers for caller-authorized manuscript revision jobs.

No runtime or route constructs this service. A durable job must already contain
``asset_manifest`` and be authorized by the caller's transaction-local loader.
Final image promotion belongs to the SAME journal transaction as tab publication.
Immutable bytes are never deleted, including on cancellation or failed commits.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import time

from hosted.account_lifecycle import AccountLifecycleDocumentStore
from hosted.action_journal import ActionDocumentStore, _ACTION
from hosted.firestore.contracts import DocumentConflict, DocumentNotFound, HostedValidationError
from hosted.firestore.ids import require_opaque_id, require_project_id, require_user_id
from hosted.firestore.repositories import HostedProjectRepository, _fingerprint
from hosted.manuscript_visibility import ASSET_REVISIONS_FIELD, HEAD_FIELD, ManuscriptDocumentStore
from hosted.storage.keys import project_asset_key
from hosted.storage.publication import ImmutableObjectPublisher
from security.image_security import HOSTED_DEFAULT_IMAGE_BYTES, UnsafeImage, inspect_hosted_image_bytes

MAX_IMAGES = 64
MAX_IMAGE_BYTES = 32 * 1024 * 1024
_MANIFEST_FIELDS = frozenset({'asset_id', 'name', 'api_path', 'sha256', 'extension',
    'media_type', 'object_name', 'object_size', 'tab_ids'})
_POINTER_FIELDS = ('object_name', 'object_generation', 'object_sha256', 'object_size')
_IDENTITY_FIELDS = ('name', 'api_path', 'sha256', *_POINTER_FIELDS)


def _invalid():
    raise HostedValidationError('HOSTED_REVISION_IMAGE_MANIFEST_INVALID')


def prepare_revision_images(project_id, images):
    """Pure validation before any writes; ``images`` contain data/ext/tab_ids.

    Bounds match new-project import: 64 distinct images and 32 MiB of distinct
    bytes, with the existing stricter 12 MiB per-image hostile-image boundary.
    Repeated DOCX references merge destinations, not object bytes or asset rows.
    """
    pid = require_project_id(project_id)
    unique, total = {}, 0
    for image in images:
        if not isinstance(image, dict) or set(image) != {'data', 'extension', 'tab_ids'}:
            _invalid()
        blob = image['data']
        if not isinstance(blob, bytes):
            _invalid()
        try:
            info = inspect_hosted_image_bytes(blob, image['extension'], byte_limit=HOSTED_DEFAULT_IMAGE_BYTES)
        except UnsafeImage as exc:
            raise HostedValidationError('HOSTED_REVISION_IMAGE_UNSAFE') from exc
        if (not isinstance(image['tab_ids'], (list, tuple)) or not image['tab_ids']
                or len(image['tab_ids']) > MAX_IMAGES):
            _invalid()
        if any(not isinstance(tid, str) for tid in image['tab_ids']):
            _invalid()
        tabs = sorted({require_opaque_id(tid, label='tab_id') for tid in image['tab_ids']})
        sha = hashlib.sha256(blob).hexdigest()
        name = 'img_' + sha[:24] + info.extension
        entry = {'asset_id': 'asset_' + sha, 'name': name, 'api_path': '/api/assets/' + name,
            'sha256': sha, 'extension': info.extension, 'media_type': info.media_type,
            'object_name': project_asset_key(pid, sha, info.extension), 'object_size': len(blob), 'tab_ids': tabs}
        if sha in unique:
            prior, previous = unique[sha]
            if previous != blob or prior['extension'] != info.extension:
                raise DocumentConflict('HOSTED_REVISION_IMAGE_COLLISION')
            prior['tab_ids'] = sorted(set(prior['tab_ids']) | set(tabs))
            if len(prior['tab_ids']) > MAX_IMAGES:
                raise HostedValidationError('HOSTED_REVISION_IMAGE_REFERENCE_LIMIT')
        else:
            unique[sha] = (entry, blob)
            total += len(blob)
        if len(unique) > MAX_IMAGES or total > MAX_IMAGE_BYTES:
            raise HostedValidationError('HOSTED_IMPORT_IMAGE_BUDGET_EXCEEDED')
    ordered = [unique[sha] for sha in sorted(unique)]
    # The durable plan contains no image bytes. Callers retain/source the separate
    # bounded payloads until stage succeeds; the job manifest binds every byte.
    return [entry for entry, _ in ordered], {entry['asset_id']: blob for entry, blob in ordered}


@dataclass(frozen=True)
class VerifiedRevisionImages:
    project_id: str
    actor: str
    job: str
    generation: int
    receipts: tuple


class RevisionImageStager:
    def __init__(self, domain, authorize):
        """``authorize(tx, pid, uid, job)`` is the caller's durable job loader.

        It must check job state/expiry, source generation, membership and live
        actor/owner accounts. This helper additionally verifies actor context,
        durable job equality, project membership/generation, and manifest scope.
        No raw provider or unauthenticated fallback is accepted.
        """
        if domain.encrypted_fields is not None or domain.encrypted_objects is not None:
            raise HostedValidationError('HOSTED_PRIVATE_BATCH_NOT_READY')
        view = domain.documents.store if isinstance(domain.documents, ActionDocumentStore) else domain.documents
        if (not isinstance(view, ManuscriptDocumentStore) or view.include_retained
                or not isinstance(view.store, AccountLifecycleDocumentStore) or not callable(authorize)):
            raise HostedValidationError('HOSTED_MANUSCRIPT_REVISION_READER_REQUIRED')
        self.domain, self.view, self.authorize = domain, view, authorize
        self.retained = view.retained_view()
        self.publisher = ImmutableObjectPublisher(domain.objects)

    def _scope(self, tx, pid, uid, job):
        pid, uid = require_project_id(pid), require_user_id(uid)
        job = require_opaque_id(job, label='import_job')
        if self.view.store.actor() != uid:
            raise HostedValidationError('HOSTED_IMPORT_AUTHENTICATED_ACTOR_REQUIRED')
        data = self.authorize(tx, pid, uid, job)
        path = ('projects', pid, 'manuscript_import_jobs', job)
        row = tx.get(path)
        root, member = HostedProjectRepository(None)._require_member(tx, pid, uid)
        self.domain.projects._open_project(pid, root)  # Reject private roots before plaintext publication.
        if (row is None or member.get('role') not in {'owner', 'editor'}
                or dict(row.data) != data or data.get('actor') != uid or data.get('actor_user_id') != uid):
            raise DocumentNotFound('HOSTED_IMPORT_NOT_FOUND')
        if type(data.get('generation')) is not int or data['generation'] != root['generation']:
            raise DocumentConflict('HOSTED_GENERATION_CONFLICT')
        if data.get('state') not in {'preparing', 'staging'} or time.time() >= data['expires_at']:
            raise DocumentConflict('HOSTED_IMPORT_NOT_STAGING')
        if tx.get(('projects', pid, 'mutation_receipts', job)) is not None:
            raise DocumentConflict('HOSTED_IMPORT_ALREADY_PUBLISHED_USE_UNDO')
        self._manifest(pid, data)
        # A pending marker must never accidentally authorize a live asset URL.
        for tid in {tid for entry in data['asset_manifest'] for tid in entry['tab_ids']}:
            tab = tx.get(('projects', pid, 'tabs', tid))
            if tab is not None and tab.data.get(HEAD_FIELD) == job:
                raise DocumentConflict('HOSTED_REVISION_IMAGE_ALREADY_VISIBLE')
        return path, deepcopy(data)

    @staticmethod
    def _manifest(pid, data):
        manifest = data.get('asset_manifest')
        destinations = data.get('result', {}).get('document_ids')
        if not isinstance(manifest, list) or len(manifest) > MAX_IMAGES or not isinstance(destinations, list):
            _invalid()
        ids, names, total = set(), set(), 0
        for entry in manifest:
            if not isinstance(entry, dict) or set(entry) != _MANIFEST_FIELDS:
                _invalid()
            sha, ext = entry['sha256'], entry['extension']
            if (not isinstance(sha, str) or len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha)
                    or not isinstance(ext, str) or ext not in {'.png', '.jpg', '.webp'}):
                _invalid()
            name = 'img_' + sha[:24] + ext
            if (entry['asset_id'] != 'asset_' + sha or entry['name'] != name
                    or entry['api_path'] != '/api/assets/' + name
                    or entry['object_name'] != project_asset_key(pid, sha, ext)
                    or entry['media_type'] != {'.png': 'image/png', '.jpg': 'image/jpeg', '.webp': 'image/webp'}[ext]
                    or type(entry['object_size']) is not int or not 0 < entry['object_size'] <= HOSTED_DEFAULT_IMAGE_BYTES
                    or not isinstance(entry['tab_ids'], list) or not 1 <= len(entry['tab_ids']) <= MAX_IMAGES):
                _invalid()
            tabs = entry['tab_ids']
            if any(not isinstance(tid, str) for tid in tabs) or sorted(set(tabs)) != tabs:
                _invalid()
            for tid in tabs:
                require_opaque_id(tid, label='tab_id')
                if tid not in destinations:
                    _invalid()
            if entry['asset_id'] in ids or name in names:
                raise DocumentConflict('HOSTED_REVISION_IMAGE_COLLISION')
            ids.add(entry['asset_id']); names.add(name); total += entry['object_size']
        if total > MAX_IMAGE_BYTES:
            raise HostedValidationError('HOSTED_IMPORT_IMAGE_BUDGET_EXCEEDED')

    @staticmethod
    def _refs(tx, pid, data):
        if ASSET_REVISIONS_FIELD not in data:
            return None
        refs = data[ASSET_REVISIONS_FIELD]
        if not isinstance(refs, list) or not 1 <= len(refs) <= MAX_IMAGES:
            _invalid()
        seen = set()
        for ref in refs:
            if not isinstance(ref, dict) or set(ref) != {'tab_id', 'revision'}:
                _invalid()
            if not isinstance(ref['tab_id'], str) or not isinstance(ref['revision'], str):
                _invalid()
            tid = require_opaque_id(ref['tab_id'], label='tab_id')
            revision = require_opaque_id(ref['revision'], label='manuscript_revision')
            if (tid, revision) in seen:
                _invalid()
            seen.add((tid, revision))
            tab = tx.get(('projects', pid, 'tabs', tid))
            if tab is not None and tab.data.get(HEAD_FIELD) == revision:
                # Supported publication always removes markers. Silently reusing
                # a scoped-visible row could retire its only old reference.
                raise DocumentConflict('HOSTED_REVISION_IMAGE_SCOPED_VISIBLE_UNSUPPORTED')
        return deepcopy(refs)

    @staticmethod
    def _matches(row, receipt):
        if any(row.get(key) != receipt[key] for key in _IDENTITY_FIELDS):
            raise DocumentConflict('HOSTED_REVISION_IMAGE_COLLISION')
        aid = receipt['asset_id']
        if row.get('id') != aid or row.get('asset_id') != aid or type(row.get('generation')) is not int or row['generation'] < 1:
            raise DocumentConflict('HOSTED_REVISION_IMAGE_COLLISION')

    def stage(self, pid, uid, job, payloads):
        """Verify bounded bytes, then atomically store hidden metadata/receipts."""
        _, before = self.retained.run_transaction(lambda tx: self._scope(tx, pid, uid, job))
        manifest = before['asset_manifest']
        if not isinstance(payloads, dict) or set(payloads) != {entry['asset_id'] for entry in manifest}:
            _invalid()
        # Validate the whole input set before writing the first immutable object.
        checked, _ = prepare_revision_images(pid, [
            {'data': payloads[entry['asset_id']], 'extension': entry['extension'], 'tab_ids': entry['tab_ids']}
            for entry in manifest])
        if checked != manifest:
            _invalid()
        receipts = []
        for entry in manifest:
            obj = self.publisher.publish_bytes(entry['object_name'], payloads[entry['asset_id']],
                max_bytes=HOSTED_DEFAULT_IMAGE_BYTES, content_type=entry['media_type'],
                metadata={'breadmaker-entity': 'project_asset'}).object
            if obj.name != entry['object_name']:
                raise DocumentConflict('HOSTED_REVISION_IMAGE_COLLISION')
            receipts.append({**entry, 'object_generation': obj.generation, 'object_sha256': obj.sha256})
        def commit(tx):
            path, current = self._scope(tx, pid, uid, job)
            if current['generation'] != before['generation'] or current['asset_manifest'] != manifest:
                raise DocumentConflict('HOSTED_IMPORT_REQUEST_CHANGED')
            if 'asset_receipts' in current and current['asset_receipts'] != receipts:
                raise DocumentConflict('HOSTED_REVISION_IMAGE_RECEIPT_CHANGED')
            changes = []
            for receipt in receipts:
                target = ('projects', pid, 'assets', receipt['asset_id'])
                row = tx.get(target)
                # A 96-bit URL prefix collision or a legacy duplicate must not
                # make committed URL lookup ambiguous, even if IDs differ.
                matches = tx.query_collection(target[:-1], field='name', value=receipt['name'])
                if any(match.path != target for match in matches):
                    raise DocumentConflict('HOSTED_REVISION_IMAGE_COLLISION')
                refs = [{'tab_id': tid, 'revision': job} for tid in receipt['tab_ids']]
                if row is not None:
                    self._matches(row.data, receipt)
                    previous = self._refs(tx, pid, row.data)
                    if previous is None:
                        continue  # Global/legacy availability is never narrowed.
                    refs = [*previous, *(ref for ref in refs if ref not in previous)]
                    if len(refs) > MAX_IMAGES:
                        raise HostedValidationError('HOSTED_REVISION_IMAGE_REFERENCE_LIMIT')
                    value = {**row.data, ASSET_REVISIONS_FIELD: refs}
                else:
                    public = {key: receipt[key] for key in _IDENTITY_FIELDS}
                    aid = receipt['asset_id']; stamp = self.domain.clock.now_iso()
                    public.update(id=aid, asset_id=aid)
                    value = {**public, 'generation': 1, 'created_at': stamp, 'updated_at': stamp,
                        '_bm_create_operation': current['operation'], '_bm_create_fingerprint': _fingerprint(public),
                        ASSET_REVISIONS_FIELD: refs}
                if row is None or value != row.data:
                    changes.append((target, value, row is None))
            # Every collision/reference read above precedes all writes.
            for target, value, create in changes:
                (tx.create if create else tx.set)(target, value)
            if current.get('asset_receipts') != receipts:
                tx.set(path, {**current, 'asset_receipts': receipts})
            return deepcopy(receipts)
        return self.retained.run_transaction(commit)

    def verify_for_publication(self, pid, uid, job):
        """Reverify pinned immutable bytes immediately before final transaction."""
        _, data = self.retained.run_transaction(lambda tx: self._scope(tx, pid, uid, job))
        receipts = self._receipts(data)
        for receipt in receipts:
            body = b''.join(self.domain.objects.read_chunks(receipt['object_name'],
                max_bytes=HOSTED_DEFAULT_IMAGE_BYTES, if_generation_match=receipt['object_generation']))
            if len(body) != receipt['object_size'] or hashlib.sha256(body).hexdigest() != receipt['sha256']:
                raise HostedValidationError('HOSTED_REVISION_IMAGE_INTEGRITY_FAILED')
            try:
                inspect_hosted_image_bytes(body, receipt['extension'], byte_limit=HOSTED_DEFAULT_IMAGE_BYTES)
            except UnsafeImage as exc:
                raise HostedValidationError('HOSTED_REVISION_IMAGE_UNSAFE') from exc
        return VerifiedRevisionImages(pid, uid, job, data['generation'], tuple(deepcopy(receipts)))

    @staticmethod
    def _receipts(data):
        receipts = data.get('asset_receipts')
        if not isinstance(receipts, list) or len(receipts) != len(data['asset_manifest']):
            raise DocumentConflict('HOSTED_REVISION_IMAGES_NOT_STAGED')
        for receipt, entry in zip(receipts, data['asset_manifest'], strict=True):
            if (not isinstance(receipt, dict) or set(receipt) != _MANIFEST_FIELDS | {'object_generation', 'object_sha256'}
                    or {key: receipt[key] for key in _MANIFEST_FIELDS} != entry
                    or receipt['object_sha256'] != entry['sha256']
                    or type(receipt['object_generation']) is not int or receipt['object_generation'] < 1):
                raise HostedValidationError('HOSTED_REVISION_IMAGE_RECEIPT_INVALID')
        return receipts

    def publication_changes(self, tx, pid, uid, job, verified):
        """Return <=64 (path, after, False) changes; never commit independently.

        Call BEFORE any final transaction writes. The caller must reserve these
        rows AND both before/after JSON bytes within the existing journal limits.
        Only exact project assets/<asset_id> paths need journal eligibility.
        """
        if _ACTION.get() is None or _ACTION.get()[1:] != (pid, uid):
            raise HostedValidationError('HOSTED_MANUSCRIPT_UNDO_ACTION_REQUIRED')
        _, data = self._scope(tx, pid, uid, job)
        receipts = self._receipts(data)
        if (not isinstance(verified, VerifiedRevisionImages)
                or (verified.project_id, verified.actor, verified.job, verified.generation) != (pid, uid, job, data['generation'])
                or list(verified.receipts) != receipts):
            raise DocumentConflict('HOSTED_REVISION_IMAGE_VERIFICATION_CHANGED')
        changes = []
        for receipt in receipts:
            path = ('projects', pid, 'assets', receipt['asset_id'])
            row = tx.get(path)
            if row is None:
                raise DocumentConflict('HOSTED_REVISION_IMAGES_NOT_STAGED')
            self._matches(row.data, receipt)
            refs = self._refs(tx, pid, row.data)
            if refs is None:
                continue
            if any({'tab_id': tid, 'revision': job} not in refs for tid in receipt['tab_ids']):
                raise DocumentConflict('HOSTED_REVISION_IMAGE_REFERENCE_CHANGED')
            after = {key: deepcopy(value) for key, value in row.data.items() if key != ASSET_REVISIONS_FIELD}
            after.update(generation=row.data['generation'] + 1, updated_at=self.domain.clock.now_iso())
            changes.append((path, after, False))
        return changes
