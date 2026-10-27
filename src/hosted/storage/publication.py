from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

from hosted.firestore.contracts import (
    DocumentAlreadyExists,
    DocumentConflict,
    DocumentStore,
    HostedValidationError,
    Transaction,
)
from hosted.firestore.ids import require_opaque_id, require_project_id, require_user_id
from hosted.firestore.model_registry import model_for
from hosted.firestore.repositories import HostedProjectEntityRepository, HostedProjectRepository, UtcClock, render_model_path

from .contracts import (
    ObjectAlreadyExists,
    ObjectGenerationConflict,
    ObjectIntegrityError,
    ObjectMetadata,
    ObjectStore,
)
from .keys import require_hybrid_entity_object_key, require_object_name, require_sha256, revision_key



_REVISION_MANAGED_FIELDS = {
    "generation",
    "created_at",
    "updated_at",
    "published_at",
    "project_id",
    "revision_id",
    "project_generation",
    "author_user_id",
    "object_name",
    "object_generation",
    "object_sha256",
    "object_size",
}
_SECRET_FIELD_FRAGMENTS = {"password", "client_secret", "refresh_token", "access_token", "private_key", "credential"}

def history_capture_token(generation, bookmark, context):
    value = None if bookmark is None else {
        k: v for k, v in bookmark.items()
        if not k.startswith('_bm_') and k not in {'generation', 'created_at', 'updated_at'}
    }
    material = json.dumps([int(generation), value, context],
                          sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(material.encode()).hexdigest()


def _canonical_revision_metadata(payload: Mapping[str, object]) -> dict[str, object]:
    public = dict(payload)
    for key in public:
        normalized = str(key).lower()
        if normalized.startswith("_bm_") or normalized in _REVISION_MANAGED_FIELDS:
            raise HostedValidationError("HOSTED_REVISION_MANAGED_FIELD_RESERVED")
        if any(fragment in normalized for fragment in _SECRET_FIELD_FRAGMENTS):
            raise HostedValidationError("HOSTED_SECRET_FIELD_FORBIDDEN")
    try:
        json.dumps(public, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise HostedValidationError("HOSTED_DOCUMENT_NOT_JSON") from exc
    return public


@dataclass(frozen=True)
class PublishedObject:
    object: ObjectMetadata
    created: bool


class ImmutableObjectPublisher:
    """Create immutable bytes, then verify the exact stored generation and SHA-256."""

    def __init__(self, store: ObjectStore) -> None:
        self.store = store

    def _verify(self, metadata: ObjectMetadata, *, expected_sha256: str, expected_size: int, max_bytes: int) -> ObjectMetadata:
        if metadata.generation < 1:
            raise ObjectIntegrityError("HOSTED_OBJECT_GENERATION_MISSING")
        if metadata.size != int(expected_size):
            raise ObjectIntegrityError("HOSTED_OBJECT_SIZE_MISMATCH")
        digest = hashlib.sha256()
        total = 0
        for chunk in self.store.read_chunks(
            metadata.name,
            max_bytes=max_bytes,
            if_generation_match=metadata.generation,
        ):
            total += len(chunk)
            digest.update(chunk)
        if total != int(expected_size) or digest.hexdigest() != require_sha256(expected_sha256):
            raise ObjectIntegrityError("HOSTED_OBJECT_HASH_MISMATCH")
        return ObjectMetadata(
            name=metadata.name,
            generation=metadata.generation,
            size=total,
            sha256=digest.hexdigest(),
            content_type=metadata.content_type,
            metadata=metadata.metadata,
        )

    def publish_stream(
        self,
        name: str,
        source_factory: Callable[[], Iterable[bytes]],
        *,
        expected_sha256: str,
        expected_size: int,
        max_bytes: int,
        content_type: str = "application/octet-stream",
        metadata: Mapping[str, str] | None = None,
    ) -> PublishedObject:
        key = require_object_name(name)
        digest = require_sha256(expected_sha256)
        size = int(expected_size)
        limit = int(max_bytes)
        if size < 0 or size > limit:
            raise ObjectIntegrityError("HOSTED_OBJECT_EXPECTED_SIZE_INVALID")
        custom = {**dict(metadata or {}), "breadmaker-sha256": digest}
        try:
            stored = self.store.write_chunks(
                key,
                source_factory(),
                max_bytes=limit,
                content_type=content_type,
                if_generation_match=0,
                metadata=custom,
            )
            created = True
        except ObjectAlreadyExists:
            stored = self.store.stat(key)
            if stored is None:
                raise ObjectGenerationConflict("HOSTED_OBJECT_RETRY_RACE")
            created = False
        verified = self._verify(stored, expected_sha256=digest, expected_size=size, max_bytes=limit)
        return PublishedObject(verified, created)

    def publish_bytes(
        self,
        name: str,
        payload: bytes,
        *,
        max_bytes: int,
        content_type: str = "application/octet-stream",
        metadata: Mapping[str, str] | None = None,
    ) -> PublishedObject:
        data = bytes(payload)
        digest = hashlib.sha256(data).hexdigest()
        return self.publish_stream(
            name,
            lambda: (data,),
            expected_sha256=digest,
            expected_size=len(data),
            max_bytes=max_bytes,
            content_type=content_type,
            metadata=metadata,
        )


class HostedHybridObjectPublisher:
    """Publish GCS bytes first and Firestore object metadata/pointers last."""

    def __init__(self, objects: ObjectStore, documents: DocumentStore, *, clock=None) -> None:
        self.objects = objects
        self.documents = documents
        self.clock = clock or UtcClock()
        self.immutable = ImmutableObjectPublisher(objects)
        self.entities = HostedProjectEntityRepository(documents, clock=self.clock)
        self.projects = HostedProjectRepository(documents, clock=self.clock)

    @staticmethod
    def _pointer(publication: PublishedObject) -> dict[str, object]:
        obj = publication.object
        return {
            "object_name": obj.name,
            "object_generation": obj.generation,
            "object_sha256": obj.sha256,
            "object_size": obj.size,
        }

    def _preflight_hybrid_object(
        self,
        logical_entity: str,
        ids: Mapping[str, str],
        *,
        user_id: str,
        object_name: str,
        object_payload: bytes,
        for_update: bool,
    ) -> tuple[str, str]:
        model = model_for(logical_entity)
        if model.hosted_store != "hybrid" or logical_entity == "hosted_revision_history":
            raise HostedValidationError("HOSTED_OBJECT_ENTITY_UNSUPPORTED")
        if model.concurrency_policy not in {"immutable_create_only", "entity_generation_cas"}:
            raise HostedValidationError("HOSTED_OBJECT_SPECIALIZED_TRANSACTION_REQUIRED")
        if for_update and model.concurrency_policy != "entity_generation_cas":
            raise HostedValidationError("HOSTED_OBJECT_ENTITY_IMMUTABLE")
        # Validate the document IDs and reject unsupported/system-only project
        # entities before bytes are created. Final authorization is repeated by
        # HostedProjectEntityRepository during the Firestore publication.
        render_model_path(model, **dict(ids))
        uid = require_user_id(user_id)
        pid = require_project_id(str(ids.get("project_id") or ""))
        self.projects.get_for_user(pid, uid)
        key_digest = require_hybrid_entity_object_key(logical_entity, dict(ids), object_name)
        payload_digest = hashlib.sha256(bytes(object_payload)).hexdigest()
        if key_digest != payload_digest:
            raise ObjectIntegrityError("HOSTED_OBJECT_KEY_HASH_MISMATCH")
        return uid, require_object_name(object_name)

    def publish_new_entity(
        self,
        logical_entity: str,
        ids: Mapping[str, str],
        payload: Mapping[str, object],
        *,
        user_id: str,
        operation_id: str,
        object_name: str,
        object_payload: bytes,
        max_bytes: int,
        content_type: str = "application/octet-stream",
    ) -> dict[str, object]:
        op = require_opaque_id(operation_id, label="operation_id")
        uid, key = self._preflight_hybrid_object(
            logical_entity,
            ids,
            user_id=user_id,
            object_name=object_name,
            object_payload=object_payload,
            for_update=False,
        )
        publication = self.immutable.publish_bytes(
            key,
            object_payload,
            max_bytes=max_bytes,
            content_type=content_type,
            metadata={"breadmaker-entity": logical_entity},
        )
        # The structured create is the authoritative publication point. If it
        # conflicts, the verified immutable object remains an orphan by design.
        row = dict(payload)
        row.update(self._pointer(publication))
        return self.entities.create(logical_entity, ids, row, user_id=uid, operation_id=op)

    def publish_entity_payload(
        self,
        logical_entity: str,
        ids: Mapping[str, str],
        *,
        user_id: str,
        expected_generation: int,
        operation_id: str,
        object_name: str,
        object_payload: bytes,
        max_bytes: int,
        pointer_field: str = "payload_object",
        content_type: str = "application/octet-stream",
    ) -> dict[str, object]:
        op = require_opaque_id(operation_id, label="operation_id")
        uid, key = self._preflight_hybrid_object(
            logical_entity,
            ids,
            user_id=user_id,
            object_name=object_name,
            object_payload=object_payload,
            for_update=True,
        )
        publication = self.immutable.publish_bytes(
            key,
            object_payload,
            max_bytes=max_bytes,
            content_type=content_type,
            metadata={"breadmaker-entity": logical_entity},
        )
        patch = {pointer_field: self._pointer(publication)}
        return self.entities.update(
            logical_entity,
            ids,
            patch,
            user_id=uid,
            expected_generation=expected_generation,
            operation_id=op,
        )

    def _capture_state(self, tx, pid, uid, context):
        project, _ = self.projects._require_member(tx, pid, uid)
        bookmark = tx.get(("projects", pid, "members", uid, "state", "bookmark"))
        # Every canonical workspace mutation advances this generation, not just
        # prose. Bookmark/config state captured outside that clock is included.
        return project, history_capture_token(project['generation'], None if bookmark is None else bookmark.data, context)

    def history_capture_state(self, project_id, user_id, context):
        pid, uid = require_project_id(project_id), require_user_id(user_id)
        def read(tx):
            project, token = self._capture_state(tx, pid, uid, context)
            head = tx.get(self.projects._member_path(pid, uid))
            revision = None
            if head is not None and head.data.get('_bm_history_capture_token') == token:
                row = tx.get(('projects', pid, 'revisions', head.data['_bm_history_revision_id']))
                if row is not None: revision = dict(row.data)
            return {'generation': int(project['generation']), 'token': token, 'revision': revision}
        return self.documents.run_transaction(read)

    def publish_revision(
        self,
        *,
        project_id: str,
        user_id: str,
        revision_id: str,
        expected_project_generation: int,
        envelope: bytes,
        revision_metadata: Mapping[str, object],
        operation_id: str,
        max_bytes: int,
        bookkeeping_only: bool = False,
        capture_token: str | None = None,
        capture_context: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        pid = require_project_id(project_id)
        uid = require_user_id(user_id)
        rid = require_opaque_id(revision_id, label="revision_id")
        op = require_opaque_id(operation_id, label="operation_id")
        expected = int(expected_project_generation)
        if expected < 1:
            raise ValueError("HOSTED_REVISION_EXPECTED_GENERATION_INVALID")
        public = _canonical_revision_metadata(revision_metadata)
        # Fail unauthorized callers before creating bytes. Authorization is still
        # repeated inside the publication transaction so membership revocation
        # cannot race the authoritative pointer commit.
        self.projects.get_for_user(pid, uid)
        committed_generation = expected if bookkeeping_only else expected + 1
        object_name = revision_key(pid, committed_generation, rid)
        publication = self.immutable.publish_bytes(
            object_name,
            envelope,
            max_bytes=max_bytes,
            content_type="application/gzip",
            metadata={"breadmaker-entity": "hosted_revision_history", "breadmaker-project": pid},
        )
        pointer = self._pointer(publication)
        revision_model = model_for("hosted_revision_history")
        revision_path = render_model_path(revision_model, project_id=pid, revision_id=rid)
        project_path = self.projects._project_path(pid)
        stamp = self.clock.now_iso()
        public.update(
            {
                "project_id": pid,
                "revision_id": rid,
                "project_generation": committed_generation,
                "author_user_id": uid,
                **pointer,
                "published_at": stamp,
            }
        )
        fingerprint_payload = {key: value for key, value in public.items() if key != "published_at"}
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()

        def commit(tx: Transaction) -> dict[str, object]:
            project, _ = self.projects._require_member(tx, pid, uid)
            existing = tx.get(revision_path)
            # Internal metadata on the existing membership record stays inside
            # established project/account deletion and privacy ownership paths.
            head_path = self.projects._member_path(pid, uid)
            if capture_token is not None:
                _, current_token = self._capture_state(tx, pid, uid, capture_context)
                if current_token != capture_token:
                    raise DocumentConflict('HOSTED_HISTORY_CAPTURE_CHANGED')
                head = tx.get(head_path)
                if head is not None and head.data.get('_bm_history_capture_token') == capture_token:
                    prior = tx.get(('projects', pid, 'revisions', head.data['_bm_history_revision_id']))
                    if prior is not None:
                        return {**dict(prior.data), 'unchanged': True}
            if existing is not None:
                row = dict(existing.data)
                same_pointer = all(row.get(key) == value for key, value in pointer.items())
                same_publication = (
                    row.get("_bm_create_operation") == op
                    and row.get("_bm_create_fingerprint") == fingerprint
                )
                if (
                    same_pointer
                    and same_publication
                    and int(row.get("project_generation") or 0) == committed_generation
                ):
                    return row
                raise DocumentAlreadyExists("HOSTED_REVISION_ALREADY_EXISTS")
            current = int(project.get("generation") or 0)
            if current != expected:
                raise DocumentConflict("HOSTED_GENERATION_CONFLICT")
            row = dict(public)
            row["generation"] = 1
            row["created_at"] = stamp
            row["updated_at"] = stamp
            row["_bm_create_operation"] = op
            row["_bm_create_fingerprint"] = fingerprint
            tx.create(revision_path, row)
            if not bookkeeping_only:
                project["generation"] = committed_generation
                project["updated_at"] = stamp
            project["current_revision_id"] = rid
            project["current_revision"] = dict(pointer)
            tx.set(project_path, project)
            if capture_token is not None:
                tx.set(head_path, {**dict(head.data), '_bm_history_capture_token': capture_token, '_bm_history_revision_id': rid})
            return row

        # No object rollback here. A failed CAS intentionally leaves only the
        # verified immutable object, discoverable later as an orphan.
        return self.documents.run_transaction(commit)


def discover_orphans(
    store: ObjectStore,
    *,
    prefix: str,
    referenced_object_names: Iterable[str],
) -> list[ObjectMetadata]:
    """Return unreferenced candidates; deletion is deliberately a separate act."""
    referenced = {require_object_name(name) for name in referenced_object_names}
    return [row for row in store.list_prefix(prefix) if row.name not in referenced]


def delete_confirmed_orphan(store: ObjectStore, candidate: ObjectMetadata) -> None:
    """Generation-CAS deletion for a caller that has already proven retention/reachability."""
    store.delete(candidate.name, if_generation_match=candidate.generation)
