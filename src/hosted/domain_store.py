from __future__ import annotations

"""Cloud-native canonical project state used by the Milestone-5 hosted runtime.

Milestones 3 and 4 are frozen adapter layers.  This Milestone-5 domain service owns
product-level composition on top of them, including the invariant that canonical
project child mutations advance the project generation in the *same* Firestore
transaction.  Local Breadmaker continues to use Schema-42 SQLite unchanged.
"""

import gzip
import hashlib
import json
import re
import secrets
import string
from typing import Any, Mapping

from hosted.firestore import HostedProjectEntityRepository, HostedProjectRepository
from hosted.idempotency import stable_entity_id
from hosted.firestore.contracts import (
    DocumentAlreadyExists,
    DocumentConflict,
    DocumentNotFound,
    HostedValidationError,
    require_collection_path,
)
from hosted.firestore.ids import require_opaque_id, require_project_id, require_user_id
from hosted.firestore.model_registry import model_for
from hosted.firestore.repositories import (
    _canonical_payload,
    _fingerprint,
    _operation_id,
    _reject_managed_create_fields,
    render_model_path,
)
from hosted.storage.contracts import ObjectNotFound, ObjectStore
from hosted.storage.keys import (
    block_payload_key,
    checkpoint_key,
    cut_payload_key,
    require_hybrid_entity_object_key,
    review_snapshot_key,
)
from hosted.storage.publication import ImmutableObjectPublisher
from hosted.storage.encrypted_objects import EncryptedObjectReference, HostedEncryptedObjectStore
from hosted.security.envelope import FORMAT as _ENCRYPTED_FORMAT
from hosted.security.private_firestore import HostedPrivateFirestoreFields
from hosted.private_source_contract import audit_private_model_coverage, require_private_entity_supported


_ID_FIELDS: dict[str, tuple[str, str]] = {
    "document_tab": ("tab_id", "tab_"),
    "chapter": ("chapter_id", "chapter_"),
    "scene": ("scene_id", "scene_"),
    "manuscript_block": ("block_id", "b_"),
    "project_asset": ("asset_id", "asset_"),
    "hosted_revision_history": ("revision_id", "revision_"),
    "markup_label": ("label_id", "label_"),
    "icon_marker": ("icon_marker_id", "marker_"),
    "annotation": ("annotation_id", "annotation_"),
    "comment_thread": ("thread_id", "thread_"),
    "comment_message": ("message_id", "message_"),
    "cross_feature_reference": ("reference_id", "reference_"),
    "cut": ("cut_id", "cut_"),
    "checkpoint": ("checkpoint_id", "checkpoint_"),
    "checkpoint_asset_reference": ("asset_ref_id", "assetref_"),
    "canvas": ("canvas_id", "canvas_"),
    "canvas_node": ("node_id", "node_"),
    "canvas_edge": ("edge_id", "edge_"),
    "canvas_version": ("version_id", "version_"),
    "canvas_version_item": ("item_id", "item_"),
    "story_map": ("map_id", "map_"),
    "map_line": ("line_id", "line_"),
    "map_item": ("item_id", "item_"),
    "map_junction": ("junction_id", "junction_"),
    "task": ("task_id", "task_"),
    "task_milestone": ("milestone_id", "milestone_"),
    "review_marker_label": ("marker_label_id", "reviewmarker_"),
    "questionnaire": ("questionnaire_id", "questionnaire_"),
    "questionnaire_question": ("question_id", "question_"),
    "review": ("review_id", "review_"),
    "review_participant": ("participant_id", "participant_"),
    "review_marker_assignment": ("review_marker_id", "reviewmarkerlink_"),
    "review_snapshot": ("snapshot_id", "snapshot_"),
    "review_anchor": ("anchor_id", "anchor_"),
    "review_highlight": ("highlight_id", "highlight_"),
    "review_comment": ("comment_id", "reviewcomment_"),
    "review_comment_reply": ("reply_id", "reply_"),
    "review_suggestion": ("suggestion_id", "suggestion_"),
    "review_question_answer": ("answer_id", "answer_"),
    "review_questionnaire_assignment": ("questionnaire_id", "questionnaire_"),
    "review_scope_chapter": ("chapter_id", "chapter_"),
    "review_scope_scene": ("scene_id", "scene_"),
    "review_general_feedback": ("feedback_id", "feedback_"),
    "review_event": ("event_id", "event_"),
    "review_import_receipt": ("import_id", "import_"),
}

_HYBRID_PAYLOAD_FIELDS: dict[str, tuple[str, ...]] = {
    "manuscript_block": ("content_html", "text_content"),
    "cut": ("content_html", "text_content", "payload"),
    "checkpoint": ("snapshot",),
    "review_snapshot": ("snapshot",),
    "hosted_revision_history": ("envelope_b64",),
}

_MAX_HYBRID_BYTES = 8 * 1024 * 1024
# M12D: in experimental block-encryption mode, only narrow operational
# metadata stays visible in Firestore. Unknown fields fail before publication,
# rather than silently escaping encryption through the generic entity store.
_ENCRYPTED_BLOCK_METADATA = frozenset({
    "id", "block_id", "chapter_id", "scene_id", "tab_id", "position",
    "block_type", "semantic_style", "generation", "created_at", "updated_at",
    "payload_object",
})


def _check_encrypted_block_metadata(payload: Mapping[str, Any]) -> None:
    data = dict(payload)
    if any(str(key) not in _ENCRYPTED_BLOCK_METADATA and not str(key).startswith("_bm_") for key in data):
        raise HostedValidationError("HOSTED_ENCRYPTED_BLOCK_METADATA_FORBIDDEN")
    for field in ("block_type", "semantic_style"):
        if field in data and (not isinstance(data[field], str) or
                              not re.fullmatch(r"[a-z0-9_-]{1,48}", data[field])):
            raise HostedValidationError("HOSTED_ENCRYPTED_BLOCK_METADATA_INVALID")
    if "position" in data and (type(data["position"]) is not int or not 0 <= data["position"] <= 1000000):
        raise HostedValidationError("HOSTED_ENCRYPTED_BLOCK_POSITION_INVALID")
    for field in ("chapter_id", "tab_id", "scene_id"):
        if field in data and data[field] is not None:
            require_opaque_id(data[field], label=field)

_MANAGED_MUTATION_FIELDS = {
    "generation",
    "created_at",
    "updated_at",
    "object_name",
    "object_generation",
    "object_sha256",
    "object_size",
    "payload_object",
}


def new_entity_id(logical_entity: str) -> tuple[str, str]:
    try:
        field, prefix = _ID_FIELDS[logical_entity]
    except KeyError as exc:
        raise ValueError(f"HOSTED_ENTITY_ID_POLICY_MISSING:{logical_entity}") from exc
    return field, prefix + secrets.token_hex(12)


def operation_id(prefix: str) -> str:
    clean = "".join(ch for ch in str(prefix or "op") if ch.isalnum() or ch in "_-")[:48] or "op"
    return f"{clean}_{secrets.token_hex(12)}"


def _json_gzip(payload: Mapping[str, Any]) -> bytes:
    raw = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return gzip.compress(raw, compresslevel=6, mtime=0)


def _json_gunzip(payload: bytes) -> dict[str, Any]:
    try:
        value = json.loads(gzip.decompress(payload).decode("utf-8"))
    except Exception as exc:
        raise ValueError("HOSTED_OBJECT_PAYLOAD_INVALID") from exc
    if not isinstance(value, dict):
        raise ValueError("HOSTED_OBJECT_PAYLOAD_INVALID")
    return value


def _public(row: Mapping[str, Any]) -> dict[str, Any]:
    return {str(k): v for k, v in dict(row).items() if not str(k).startswith("_bm_")}


class HostedDomainStore:
    """Authorization-aware product entity store over Firestore + immutable GCS."""

    def __init__(self, documents, objects: ObjectStore, *,
                 encrypted_objects: HostedEncryptedObjectStore | None = None,
                 encrypted_fields: HostedPrivateFirestoreFields | None = None) -> None:
        # Explicit injection ONLY. Neither the local runtime nor hosted bootstrap
        # configures encryption implicitly. M12D covers block payloads ONLY;
        # private Firestore metadata and all other families still need migration.
        if encrypted_objects is not None and not isinstance(encrypted_objects, HostedEncryptedObjectStore):
            raise ValueError("HOSTED_ENCRYPTED_OBJECT_STORE_INVALID")
        if encrypted_fields is not None and not isinstance(encrypted_fields, HostedPrivateFirestoreFields):
            raise ValueError("HOSTED_ENCRYPTED_FIELDS_INVALID")
        # Pilot stays opt-in; local and normal hosted runtime never inject it.
        if encrypted_fields is not None and encrypted_objects is not None:
            audit_private_model_coverage()
        self.encrypted_fields = encrypted_fields
        self.encrypted_objects = encrypted_objects
        self.documents = documents
        self.objects = objects
        self.projects = HostedProjectRepository(documents)
        if encrypted_fields is not None and encrypted_objects is not None:
            self.projects.configure_private_titles(encrypted_fields.envelope)
        self.entities = HostedProjectEntityRepository(documents)
        self.immutable = ImmutableObjectPublisher(objects)
        self.clock = self.entities.state.clock

    def project_generation(self, project_id: str, user_id: str) -> int:
        row = self.projects.get_for_user(require_project_id(project_id), require_user_id(user_id))
        generation = int(row.get("generation") or 0)
        if generation < 1:
            raise RuntimeError("HOSTED_PROJECT_GENERATION_INVALID")
        return generation

    @staticmethod
    def _ids_with_entity(logical_entity: str, parent_ids: Mapping[str, str], entity_id: str) -> dict[str, str]:
        field, _ = _ID_FIELDS[logical_entity]
        ids = {str(k): str(v) for k, v in dict(parent_ids).items()}
        ids[field] = require_opaque_id(entity_id, label=field)
        return ids

    @staticmethod
    def _payload_with_identity(logical_entity: str, payload: Mapping[str, Any], entity_id: str) -> dict[str, Any]:
        field, _ = _ID_FIELDS[logical_entity]
        public = dict(payload)
        public.setdefault("id", entity_id)
        public.setdefault(field, entity_id)
        return public

    @staticmethod
    def _model(logical_entity: str):
        # Reuse the frozen Milestone-3 repository's model/authorization guard.
        return HostedProjectEntityRepository._model(logical_entity)

    def _collection_path(self, logical_entity: str, parent_ids: Mapping[str, str]) -> tuple[tuple[str, ...], dict[str, str]]:
        model = self._model(logical_entity)
        try:
            entity_field, _ = _ID_FIELDS[logical_entity]
        except KeyError as exc:
            raise HostedValidationError("HOSTED_ENTITY_ID_POLICY_MISSING") from exc
        fields = [name for _, name, _, _ in string.Formatter().parse(model.path_template) if name]
        if not fields or fields[-1] != entity_field:
            raise HostedValidationError("HOSTED_ENTITY_COLLECTION_POLICY_INVALID")
        ids = {str(k): str(v) for k, v in dict(parent_ids).items()}
        ids[entity_field] = "listing_placeholder"
        path = render_model_path(model, **ids)
        return require_collection_path(path[:-1]), ids

    def _authorize_and_project(
        self,
        tx,
        model,
        ids: Mapping[str, str],
        user_id: str,
        *,
        mutation: bool,
    ) -> tuple[tuple[str, ...], dict[str, Any]]:
        uid = require_user_id(user_id)
        self.entities._authorize(tx, model, ids, uid, mutation=mutation)
        pid = require_project_id(str(ids.get("project_id") or ""))
        project_path = self.projects._project_path(pid)
        project_row = tx.get(project_path)
        if project_row is None:
            # Authorization already fails closed for this case; retain the same
            # external failure if a provider driver observes an impossible race.
            from hosted.firestore.contracts import ProjectUnavailable

            raise ProjectUnavailable("PROJECT_NOT_FOUND")
        return project_path, dict(project_row.data)

    @staticmethod
    def _advance_project(tx, project_path: tuple[str, ...], project: dict[str, Any], *, expected_generation: int, stamp: str) -> None:
        current = int(project.get("generation") or 0)
        if current != int(expected_generation):
            raise DocumentConflict("HOSTED_GENERATION_CONFLICT")
        project["generation"] = current + 1
        project["updated_at"] = stamp
        tx.set(project_path, project)

    def _hybrid_payload(self, logical_entity: str, public: Mapping[str, Any]) -> tuple[dict[str, Any], bytes] | None:
        fields = _HYBRID_PAYLOAD_FIELDS.get(logical_entity)
        if not fields:
            return None
        body = {field: public.get(field) for field in fields if field in public}
        if not body:
            return None
        structured = {key: value for key, value in dict(public).items() if key not in fields}
        encoded = _json_gzip(body)
        if len(encoded) > _MAX_HYBRID_BYTES:
            raise ValueError("HOSTED_OBJECT_LIMIT_EXCEEDED")
        return structured, encoded

    def _hybrid_key(self, logical_entity: str, ids: Mapping[str, str], encoded: bytes) -> str:
        digest = hashlib.sha256(encoded).hexdigest()
        pid = str(ids["project_id"])
        if logical_entity == "manuscript_block":
            return block_payload_key(pid, str(ids["block_id"]), digest)
        if logical_entity == "cut":
            return cut_payload_key(pid, str(ids["cut_id"]), digest)
        if logical_entity == "checkpoint":
            return checkpoint_key(pid, str(ids["checkpoint_id"]), digest)
        if logical_entity == "review_snapshot":
            return review_snapshot_key(pid, str(ids["review_id"]), str(ids["snapshot_id"]), digest)
        raise ValueError("HOSTED_OBJECT_ENTITY_UNSUPPORTED")

    def _publish_hybrid(self, logical_entity: str, ids: Mapping[str, str], encoded: bytes,
                        *, user_id: str | None = None, operation: str | None = None):
        if self.encrypted_objects is not None and logical_entity in _HYBRID_PAYLOAD_FIELDS:
            if user_id is None or operation is None:
                raise HostedValidationError("HOSTED_ENCRYPTED_OBJECT_CONTEXT_REQUIRED")
            uid = require_user_id(user_id)
            pid = require_project_id(str(ids["project_id"]))
            model = self._model(logical_entity)
            # Enforce the canonical entity permission BEFORE GCS or Cloud KMS.
            self.documents.run_transaction(
                lambda tx: self._authorize_and_project(tx, model, ids, uid, mutation=True)
            )
            record_id = self._encrypted_hybrid_record_id(logical_entity, ids)
            ref = self.encrypted_objects.publish(
                project_id=pid, user_id=uid, entity=logical_entity,
                record_id=record_id, operation_id=_operation_id(operation),
                plaintext=encoded,
            )
            return {
                "object_name": ref.name, "object_generation": ref.generation,
                "object_sha256": ref.ciphertext_sha256, "object_size": ref.ciphertext_size,
                "encryption_format": _ENCRYPTED_FORMAT, "encryption_operation": operation,
            }
        key = self._hybrid_key(logical_entity, ids, encoded)
        expected_digest = hashlib.sha256(encoded).hexdigest()
        if require_hybrid_entity_object_key(logical_entity, ids, key) != expected_digest:
            raise RuntimeError("HOSTED_OBJECT_KEY_HASH_MISMATCH")
        publication = self.immutable.publish_bytes(
            key,
            encoded,
            max_bytes=_MAX_HYBRID_BYTES,
            content_type="application/gzip",
            metadata={"breadmaker-entity": logical_entity},
        )
        obj = publication.object
        return {
            "object_name": obj.name,
            "object_generation": obj.generation,
            "object_sha256": obj.sha256,
            "object_size": obj.size,
        }

    @staticmethod
    def _encrypted_hybrid_record_id(logical_entity: str, ids: Mapping[str, str]) -> str:
        # Preserve the existing manuscript-block ciphertext/AAD contract;
        # nested hybrids need the FULL path to avoid ID collisions across Reviews.
        if logical_entity == "manuscript_block":
            return str(ids["block_id"])
        model = HostedDomainStore._model(logical_entity)
        path = render_model_path(model, **dict(ids))
        return hashlib.sha256("/".join(path).encode("ascii")).hexdigest()

    def _hydrate(self, logical_entity: str, row: Mapping[str, Any], *,
                 ids: Mapping[str, str] | None = None, user_id: str | None = None) -> dict[str, Any]:
        if self.encrypted_fields is not None and self.encrypted_fields.protects(logical_entity):
            if ids is None or user_id is None:
                raise HostedValidationError("HOSTED_PRIVATE_FIRESTORE_CONTEXT_REQUIRED")
            public = self.encrypted_fields.open(logical_entity, ids, row)
        else:
            public = _public(row)
        fields = _HYBRID_PAYLOAD_FIELDS.get(logical_entity)
        if not fields:
            return public
        pointer = public.get("payload_object")
        if not isinstance(pointer, dict):
            if all(key in public for key in ("object_name", "object_generation", "object_size")):
                pointer = {
                    "object_name": public.get("object_name"),
                    "object_generation": public.get("object_generation"),
                    "object_size": public.get("object_size"),
                    "object_sha256": public.get("object_sha256"),
                }
        if self.encrypted_objects is not None and logical_entity in _HYBRID_PAYLOAD_FIELDS:
            # Fail closed on legacy plaintext and missing pointers, including
            # post-deployment reads from a different server instance.
            if (not isinstance(pointer, dict) or
                    pointer.get("encryption_format") != _ENCRYPTED_FORMAT or
                    not pointer.get("object_name") or ids is None or user_id is None):
                raise HostedValidationError("HOSTED_ENCRYPTED_BLOCK_REQUIRED")
            if logical_entity == "manuscript_block" and str(public.get("block_id")) != str(ids.get("block_id")):
                raise HostedValidationError("HOSTED_ENCRYPTED_BLOCK_ID_MISMATCH")
            operation = _operation_id(str(pointer.get("encryption_operation") or ""))
            ref = EncryptedObjectReference(
                name=str(pointer["object_name"]),
                generation=pointer["object_generation"],
                ciphertext_sha256=pointer["object_sha256"],
                ciphertext_size=pointer["object_size"],
            )
            if logical_entity == "manuscript_block":
                _check_encrypted_block_metadata(public)
            decoded = self.encrypted_objects.read(
                project_id=str(ids["project_id"]), user_id=require_user_id(user_id),
                entity=logical_entity,
                record_id=self._encrypted_hybrid_record_id(logical_entity, ids),
                operation_id=operation, reference=ref,
            )
            public.update(_json_gunzip(decoded))
            return public
        if not isinstance(pointer, dict) or not pointer.get("object_name"):
            return public
        try:
            encoded = b"".join(
                self.objects.read_chunks(
                    str(pointer["object_name"]),
                    max_bytes=_MAX_HYBRID_BYTES,
                    if_generation_match=int(pointer["object_generation"]),
                )
            )
        except ObjectNotFound as exc:
            raise RuntimeError("HOSTED_POINTER_OBJECT_MISSING") from exc
        digest = hashlib.sha256(encoded).hexdigest()
        if pointer.get("object_sha256") and digest != str(pointer["object_sha256"]):
            raise RuntimeError("HOSTED_POINTER_OBJECT_HASH_MISMATCH")
        if int(pointer.get("object_size") or -1) != len(encoded):
            raise RuntimeError("HOSTED_POINTER_OBJECT_SIZE_MISMATCH")
        public.update(_json_gunzip(encoded))
        return public

    def create(
        self,
        logical_entity: str,
        parent_ids: Mapping[str, str],
        payload: Mapping[str, Any],
        *,
        user_id: str,
        entity_id: str | None = None,
        operation: str | None = None,
    ) -> dict[str, Any]:
        uid = require_user_id(user_id)
        # Private workspaces reject unconverted model families before ID creation
        # or generic model validation. Unsupported configuration/secret payloads
        # must never enter an unencrypted persistence path.
        if self.encrypted_objects is not None and self.encrypted_fields is not None:
            require_private_entity_supported(logical_entity)
        field, generated = new_entity_id(logical_entity)
        eid = require_opaque_id(entity_id or generated, label=field)
        ids = self._ids_with_entity(logical_entity, parent_ids, eid)
        model = self._model(logical_entity)
        path = render_model_path(model, **ids)
        public = _canonical_payload(self._payload_with_identity(logical_entity, payload, eid))
        _reject_managed_create_fields(public)
        if (logical_entity == 'project_asset' and self.encrypted_objects is not None
                and self.encrypted_fields is not None):
            # Ordinary metadata-only entity.create must never publish a phantom
            # private image or adopt a plaintext GCS object. The caller must
            # have already created an authenticated immutable ciphertext object.
            required = {'api_path','name','sha256','object_name','object_generation',
                        'object_sha256','object_size','encryption_format','encryption_operation'}
            if not required.issubset(public) or public['encryption_format'] != _ENCRYPTED_FORMAT:
                raise HostedValidationError('HOSTED_PRIVATE_ASSET_POINTER_REQUIRED')
            from hosted.storage.assets import HostedAssetStore
            from security.image_security import HOSTED_DEFAULT_IMAGE_BYTES, inspect_hosted_image_bytes
            try:
                name = HostedAssetStore._asset_name(public['name'])
                if public['api_path'] != '/api/assets/' + name:
                    raise ValueError()
                digest = public['sha256']
                if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                    raise ValueError()
                ref = EncryptedObjectReference(
                    name=str(public['object_name']),
                    generation=int(public['object_generation']),
                    ciphertext_sha256=str(public['object_sha256']),
                    ciphertext_size=int(public['object_size']))
                payload_bytes = self.encrypted_objects.read(
                    project_id=str(ids['project_id']),user_id=uid,
                    entity=logical_entity,record_id=eid,
                    operation_id=require_opaque_id(public['encryption_operation'],label='encryption_operation'),
                    reference=ref)
                if hashlib.sha256(payload_bytes).hexdigest() != digest:
                    raise ValueError()
                inspect_hosted_image_bytes(payload_bytes, '.' + name.rsplit('.',1)[1],
                                           byte_limit=HOSTED_DEFAULT_IMAGE_BYTES)
            except Exception:
                raise HostedValidationError('HOSTED_PRIVATE_ASSET_POINTER_INVALID') from None
        op = _operation_id(operation or operation_id(f"create_{logical_entity}"))
        fingerprint = _fingerprint(public)
        stamp = self.clock.now_iso()
        # Idempotent replay is checked against the authorized plaintext view,
        # before generating a new random ciphertext. Never persist a digest of
        # the plaintext just to implement replay receipts.
        if self.encrypted_fields is not None and self.encrypted_fields.protects(logical_entity):
            try:
                old = self.entities.get(logical_entity, ids, user_id=uid)
            except DocumentNotFound:
                old = None
            if old is not None:
                if old.get("_bm_create_operation") == op:
                    existing_public = self._hydrate(logical_entity, old, ids=ids, user_id=uid)
                    if all(existing_public.get(k) == v for k, v in public.items()):
                        return existing_public
                raise DocumentAlreadyExists("HOSTED_DOCUMENT_ALREADY_EXISTS")
        expected_project_generation = self.project_generation(str(ids["project_id"]), uid)

        hybrid = self._hybrid_payload(logical_entity, public)
        if self.encrypted_objects is not None and logical_entity in _HYBRID_PAYLOAD_FIELDS and hybrid is None:
            raise HostedValidationError("HOSTED_ENCRYPTED_HYBRID_PAYLOAD_REQUIRED")
        if hybrid is not None:
            structured, encoded = hybrid
            if self.encrypted_objects is not None and logical_entity == "manuscript_block":
                _check_encrypted_block_metadata(structured)
            # Object-first, pointer-last.  A failed CAS can leave an immutable orphan;
            # it can never leave an authoritative pointer to unverified bytes.
            pointer = self._publish_hybrid(logical_entity, ids, encoded, user_id=uid, operation=op)
            stored_public = dict(structured)
            stored_public["payload_object"] = pointer
        else:
            stored_public = dict(public)

        if self.encrypted_objects is not None and logical_entity == "manuscript_block":
            _check_encrypted_block_metadata(stored_public)
            # The receipt stores a ciphertext-pointer fingerprint instead of an
            # unkeyed hash derived directly from manuscript text.
            fingerprint = _fingerprint(stored_public)
        if self.encrypted_fields is not None and self.encrypted_fields.protects(logical_entity):
            # A read-authorized participant is not necessarily allowed to write.
            # Prove mutation membership before making any KMS encryption call.
            self.documents.run_transaction(
                lambda tx: self._authorize_and_project(tx, model, ids, uid, mutation=True)
            )
            stored_public = self.encrypted_fields.seal(
                logical_entity, ids, stored_public if hybrid is not None else public)
            fingerprint = _fingerprint(stored_public)  # fingerprint of ciphertext only

        def mutate(tx):
            project_path, project = self._authorize_and_project(tx, model, ids, uid, mutation=True)
            existing = tx.get(path)
            if existing is not None:
                data = dict(existing.data)
                if data.get("_bm_create_operation") == op and data.get("_bm_create_fingerprint") == fingerprint:
                    return data
                raise DocumentAlreadyExists("HOSTED_DOCUMENT_ALREADY_EXISTS")
            data = dict(stored_public)
            data.setdefault("generation", 1)
            data.setdefault("created_at", stamp)
            data["updated_at"] = stamp
            data["_bm_create_operation"] = op
            data["_bm_create_fingerprint"] = fingerprint
            self._advance_project(
                tx,
                project_path,
                project,
                expected_generation=expected_project_generation,
                stamp=stamp,
            )
            tx.create(path, data)
            return data

        row = self.documents.run_transaction(mutate)
        return self._hydrate(logical_entity, row, ids=ids, user_id=uid)

    def create_batch_atomic(
        self,
        logical_entity: str,
        parent_ids: Mapping[str, str],
        payloads: list[Mapping[str, Any]],
        *,
        user_id: str,
        max_items: int = 400,
        operation: str | None = None,
    ) -> list[dict[str, Any]]:
        """Create a bounded Firestore-only entity batch atomically.

        This is intentionally unavailable for hybrid/object-backed entities because
        object publication is object-first and cannot be rolled back atomically with
        Firestore.  Annotation batches use this path so a validation/conflict failure
        never leaves a partially published set of annotations.
        """
        uid = require_user_id(user_id)
        rows = list(payloads)
        if len(rows) > int(max_items):
            raise HostedValidationError("HOSTED_BATCH_TOO_LARGE")
        if not rows:
            return []
        # A generic batch must not silently bypass the opt-in private field codec.
        if self.encrypted_fields is not None and self.encrypted_fields.protects(logical_entity):
            raise HostedValidationError("HOSTED_PRIVATE_FIRESTORE_BATCH_NOT_READY")
        model = self._model(logical_entity)
        if self.encrypted_objects is not None or self.encrypted_fields is not None:
            raise HostedValidationError('HOSTED_PRIVATE_BATCH_NOT_READY')
        if model.hosted_store != "firestore":
            raise HostedValidationError("HOSTED_BATCH_REQUIRES_FIRESTORE_ENTITY")
        expected_project_generation = self.project_generation(str(parent_ids.get("project_id") or ""), uid)
        stamp = self.clock.now_iso()
        prepared: list[tuple[tuple[str, ...], dict[str, str], dict[str, Any]]] = []
        for ordinal, payload in enumerate(rows):
            field, generated = new_entity_id(logical_entity)
            item_op = _operation_id(f'{operation}_{ordinal}') if operation else operation_id(f'batch_{logical_entity}')
            eid = require_opaque_id(stable_entity_id(_ID_FIELDS[logical_entity][1],item_op) if operation else generated, label=field)
            ids = self._ids_with_entity(logical_entity, parent_ids, eid)
            path = render_model_path(model, **ids)
            public = _canonical_payload(self._payload_with_identity(logical_entity, payload, eid))
            _reject_managed_create_fields(public)
            data = dict(public)
            data.setdefault("generation", 1)
            data.setdefault("created_at", stamp)
            data["updated_at"] = stamp
            data["_bm_create_operation"] = item_op
            data["_bm_create_fingerprint"] = _fingerprint(public)
            prepared.append((path, ids, data))

        def mutate(tx):
            project_path, project = self._authorize_and_project(tx, model, prepared[0][1], uid, mutation=True)
            existing_rows=[]
            for path, ids, data in prepared:
                # Keep authorization tied to every canonical path even though all
                # current batch callers share one project/collection policy.
                self.entities._authorize(tx, model, ids, uid, mutation=True)
                previous=tx.get(path)
                if previous is not None:
                    if (not operation or previous.data.get('_bm_create_operation')!=data['_bm_create_operation']
                            or previous.data.get('_bm_create_fingerprint')!=data['_bm_create_fingerprint']):
                        raise DocumentAlreadyExists("HOSTED_DOCUMENT_ALREADY_EXISTS")
                existing_rows.append(previous)
            if all(item is not None for item in existing_rows):
                return [dict(item.data) for item in existing_rows]
            if any(item is not None for item in existing_rows):
                # One Firestore transaction cannot partially commit a batch. A mixed
                # replay indicates a collision/corruption and must fail closed.
                raise DocumentConflict('HOSTED_BATCH_PARTIAL_REPLAY')
            self._advance_project(
                tx, project_path, project,
                expected_generation=expected_project_generation, stamp=stamp,
            )
            for path, _ids, data in prepared:
                tx.create(path, data)
            return [dict(data) for _path, _ids, data in prepared]

        created = self.documents.run_transaction(mutate)
        return [self._hydrate(logical_entity, row) for row in created]

    def delete_immutable_pointer(
        self,
        logical_entity: str,
        ids: Mapping[str, str],
        *,
        user_id: str,
    ) -> None:
        """Delete metadata for immutable content without deleting object bytes.

        The GCS generation remains immutable and becomes an orphan/retention-GC
        candidate.  This is the correct hosted equivalent of deleting a checkpoint
        from the product while preserving object-store immutability.
        """
        uid = require_user_id(user_id)
        ids = {str(k): str(v) for k, v in dict(ids).items()}
        model = self._model(logical_entity)
        if model.concurrency_policy not in {"immutable_create_only", "append_only"}:
            raise HostedValidationError("HOSTED_ENTITY_NOT_IMMUTABLE")
        path = render_model_path(model, **ids)
        expected_project_generation = self.project_generation(str(ids.get("project_id") or ""), uid)
        stamp = self.clock.now_iso()

        def mutate(tx):
            project_path, project = self._authorize_and_project(tx, model, ids, uid, mutation=True)
            if tx.get(path) is None:
                raise DocumentNotFound("HOSTED_DOCUMENT_NOT_FOUND")
            self._advance_project(
                tx, project_path, project,
                expected_generation=expected_project_generation, stamp=stamp,
            )
            tx.delete(path)

        self.documents.run_transaction(mutate)

    def get(self, logical_entity: str, ids: Mapping[str, str], *, user_id: str) -> dict[str, Any]:
        if self.encrypted_objects is not None and self.encrypted_fields is not None:
            require_private_entity_supported(logical_entity)
        row = self.entities.get(
            logical_entity,
            {str(k): str(v) for k, v in dict(ids).items()},
            user_id=require_user_id(user_id),
        )
        return self._hydrate(logical_entity, row, ids=ids, user_id=require_user_id(user_id))

    def list(
        self,
        logical_entity: str,
        parent_ids: Mapping[str, str],
        *,
        user_id: str,
        field: str | None = None,
        value: Any = None,
        order_by: tuple[str, ...] = ("position", "created_at", "id"),
    ) -> list[dict[str, Any]]:
        if self.encrypted_objects is not None and self.encrypted_fields is not None:
            require_private_entity_supported(logical_entity)
        uid = require_user_id(user_id)
        model = self._model(logical_entity)
        collection_path, auth_ids = self._collection_path(logical_entity, parent_ids)

        def read(tx):
            self.entities._authorize(tx, model, auth_ids, uid, mutation=False)
            return [dict(row.data) for row in tx.query_collection(collection_path, field=field, value=value)]

        if self.encrypted_fields is not None and self.encrypted_fields.protects(logical_entity):
            # Private fields cannot be queried in Firestore. Limit enumeration;
            # evaluate optional filters on the decrypted, authorized results.
            def read_private(tx):
                self.entities._authorize(tx, model, auth_ids, uid, mutation=False)
                found = tx.query_collection(collection_path)
                if len(found) > 512:
                    raise HostedValidationError("HOSTED_PRIVATE_FIRESTORE_LIST_TOO_LARGE")
                return [dict(row.data) for row in found]
            rows = self.documents.run_transaction(read_private)
            public = [self._hydrate(logical_entity, row,
                ids=self._ids_with_entity(logical_entity, parent_ids,
                    str(row.get(_ID_FIELDS[logical_entity][0]) or "")), user_id=uid)
                for row in rows]
            if field is not None:
                public = [row for row in public if row.get(field) == value]
        else:
            rows = self.documents.run_transaction(read)
            if self.encrypted_objects is not None and logical_entity == "manuscript_block":
                public = [self._hydrate(
                    logical_entity, row,
                    ids=self._ids_with_entity(logical_entity, parent_ids,
                        str(row.get("block_id") or row.get("id") or "")), user_id=uid,
                ) for row in rows]
            else:
                public = [self._hydrate(logical_entity, row) for row in rows]

        def key(row: Mapping[str, Any]):
            out = []
            for name in order_by:
                cell = row.get(name)
                if name == "position":
                    try:
                        cell = int(cell)
                    except (TypeError, ValueError):
                        cell = 0
                else:
                    cell = str(cell or "")
                out.append(cell)
            return tuple(out)

        return sorted(public, key=key)

    def update(
        self,
        logical_entity: str,
        ids: Mapping[str, str],
        patch: Mapping[str, Any],
        *,
        user_id: str,
        expected_generation: int | None = None,
        operation: str | None = None,
    ) -> dict[str, Any]:
        uid = require_user_id(user_id)
        ids = {str(k): str(v) for k, v in dict(ids).items()}
        model = self._model(logical_entity)
        if self.encrypted_objects is not None and self.encrypted_fields is not None:
            require_private_entity_supported(logical_entity)
        if model.concurrency_policy in {"immutable_create_only", "append_only"}:
            raise HostedValidationError("HOSTED_ENTITY_IMMUTABLE")
        path = render_model_path(model, **ids)
        current = self.entities.get(logical_entity, ids, user_id=uid)
        entity_generation = int(current.get("generation") or 0)
        if expected_generation is not None and entity_generation != int(expected_generation):
            raise DocumentConflict("HOSTED_GENERATION_CONFLICT")
        expected_project_generation = self.project_generation(str(ids["project_id"]), uid)
        op = _operation_id(operation or operation_id(f"update_{logical_entity}"))
        if self.encrypted_fields is not None and self.encrypted_fields.protects(logical_entity):
            # Replay was already committed: return only when the authorized
            # plaintext view agrees with the requested patch.
            if current.get("_bm_last_operation") == op:
                old_public = self._hydrate(logical_entity, current, ids=ids, user_id=uid)
                if all(old_public.get(k) == v for k, v in patch.items()):
                    return old_public
                raise DocumentConflict("HOSTED_PRIVATE_FIRESTORE_OPERATION_CONFLICT")

        merged_for_object = {**self._hydrate(logical_entity, current, ids=ids, user_id=uid), **dict(patch)}
        hybrid = self._hybrid_payload(logical_entity, merged_for_object)
        clean_patch = _canonical_payload(
            {
                str(k): v
                for k, v in dict(patch).items()
                if str(k) not in _MANAGED_MUTATION_FIELDS and not str(k).startswith("_bm_")
            }
        )
        if "generation" in patch or "created_at" in patch:
            raise HostedValidationError("HOSTED_GENERATION_FIELD_RESERVED")
        if hybrid is not None:
            structured_full, encoded = hybrid
            if self.encrypted_objects is not None and logical_entity == "manuscript_block":
                _check_encrypted_block_metadata({key: value for key, value in structured_full.items()
                                                 if key not in _MANAGED_MUTATION_FIELDS})
            structured_patch = {
                str(k): v
                for k, v in structured_full.items()
                if str(k) not in _MANAGED_MUTATION_FIELDS and not str(k).startswith("_bm_")
            }
            pointer = self._publish_hybrid(logical_entity, ids, encoded, user_id=uid, operation=op)
            clean_patch = _canonical_payload(structured_patch)
            clean_patch["payload_object"] = pointer
        if self.encrypted_objects is not None and logical_entity == "manuscript_block":
            _check_encrypted_block_metadata(clean_patch)
        if self.encrypted_fields is not None and self.encrypted_fields.protects(logical_entity):
            # Prove write authorization, then seal *outside* the Firestore
            # transaction callback. KMS must never run inside a retry loop.
            self.documents.run_transaction(
                lambda tx: self._authorize_and_project(tx, model, ids, uid, mutation=True)
            )
            content = {key: value for key, value in (
                clean_patch if hybrid is not None else merged_for_object).items()
                if key not in ("generation", "created_at", "updated_at")}
            clean_patch = {"_bm_private_fields": self.encrypted_fields.seal(
                logical_entity, ids, content)["_bm_private_fields"]}

        fingerprint = _fingerprint(clean_patch)
        stamp = self.clock.now_iso()

        def mutate(tx):
            project_path, project = self._authorize_and_project(tx, model, ids, uid, mutation=True)
            row = tx.get(path)
            if row is None:
                raise DocumentNotFound("HOSTED_DOCUMENT_NOT_FOUND")
            data = dict(row.data)
            if data.get("_bm_last_operation") == op and data.get("_bm_last_fingerprint") == fingerprint:
                return data
            current_generation = int(data.get("generation") or 0)
            if current_generation != entity_generation:
                raise DocumentConflict("HOSTED_GENERATION_CONFLICT")
            self._advance_project(
                tx,
                project_path,
                project,
                expected_generation=expected_project_generation,
                stamp=stamp,
            )
            data.update(clean_patch)
            data["generation"] = current_generation + 1
            data["updated_at"] = stamp
            data["_bm_last_operation"] = op
            data["_bm_last_fingerprint"] = fingerprint
            tx.set(path, data)
            return data

        row = self.documents.run_transaction(mutate)
        return self._hydrate(logical_entity, row, ids=ids, user_id=uid)

    def delete(
        self,
        logical_entity: str,
        ids: Mapping[str, str],
        *,
        user_id: str,
        expected_generation: int | None = None,
    ) -> None:
        if self.encrypted_objects is not None and self.encrypted_fields is not None:
            require_private_entity_supported(logical_entity)
        uid = require_user_id(user_id)
        ids = {str(k): str(v) for k, v in dict(ids).items()}
        model = self._model(logical_entity)
        if model.concurrency_policy in {"immutable_create_only", "append_only"}:
            raise HostedValidationError("HOSTED_ENTITY_IMMUTABLE")
        path = render_model_path(model, **ids)
        current = self.entities.get(logical_entity, ids, user_id=uid)
        entity_generation = int(current.get("generation") or 0)
        if expected_generation is not None and entity_generation != int(expected_generation):
            raise DocumentConflict("HOSTED_GENERATION_CONFLICT")
        expected_project_generation = self.project_generation(str(ids["project_id"]), uid)
        stamp = self.clock.now_iso()

        def mutate(tx):
            project_path, project = self._authorize_and_project(tx, model, ids, uid, mutation=True)
            row = tx.get(path)
            if row is None:
                return None
            if int(row.data.get("generation") or 0) != entity_generation:
                raise DocumentConflict("HOSTED_GENERATION_CONFLICT")
            self._advance_project(
                tx,
                project_path,
                project,
                expected_generation=expected_project_generation,
                stamp=stamp,
            )
            tx.delete(path)
            return None

        self.documents.run_transaction(mutate)


    def register_project_asset(
        self,
        *,
        project_id: str,
        user_id: str,
        api_path: str,
        name: str,
        digest: str,
        object_name: str,
        object_generation: int,
        object_size: int,
    ) -> dict[str, Any]:
        """Publish the authoritative Firestore asset record after object verification.

        The immutable object is created/verified by ``HostedAssetStore`` first.  This
        method is the M5 pointer-last publication point and advances project generation
        in the same transaction as the asset metadata create.  Re-uploading identical
        bytes is idempotent because both the document id and operation id derive from
        the full SHA-256 digest.
        """
        pid = require_project_id(project_id)
        uid = require_user_id(user_id)
        sha = str(digest or "").lower()
        if len(sha) != 64 or any(ch not in "0123456789abcdef" for ch in sha):
            raise HostedValidationError("HOSTED_ASSET_DIGEST_INVALID")
        asset_id = "asset_" + sha
        return self.create(
            "project_asset",
            {"project_id": pid},
            {
                "api_path": str(api_path),
                "name": str(name),
                "sha256": sha,
                "object_name": str(object_name),
                "object_generation": int(object_generation),
                "object_sha256": sha,
                "object_size": int(object_size),
            },
            user_id=uid,
            entity_id=asset_id,
            operation="asset_" + sha[:48],
        )

    def maybe_get(self, logical_entity: str, ids: Mapping[str, str], *, user_id: str) -> dict[str, Any] | None:
        try:
            return self.get(logical_entity, ids, user_id=user_id)
        except DocumentNotFound:
            return None
