from __future__ import annotations

"""M12G opt-in Firestore protection for user-authored structured project records.

Not part of the normal hosted runtime.  Encrypts *all* user-editable properties,
not only a handpicked list of strings, so unexpected private fields cannot leak.
Canonical authorization always runs in HostedDomainStore before this codec opens
stored bytes.  This is not E2EE: an IAM-authorized server can decrypt.
"""

import hashlib
import json
from string import Formatter
from typing import Any, Mapping

from hosted.firestore.contracts import HostedValidationError
from hosted.firestore.ids import require_opaque_id, require_project_id
from hosted.firestore.model_registry import model_for
from hosted.firestore.repositories import render_model_path
from .envelope import FORMAT, HostedEnvelopeCodec

PRIVATE_FIELD_NAME = "_bm_private_fields"
# Only structural record metadata and internal operation receipts may remain in
# Firestore. Other user-provided fields go into the authenticated envelope.
_OPERATIONAL_STORED = frozenset({
    "id", "generation", "created_at", "updated_at",
    "_bm_create_operation", "_bm_create_fingerprint", "_bm_last_operation",
    "_bm_last_fingerprint", PRIVATE_FIELD_NAME,
})
# Explicit allowlist of project-scoped Firestore-only models. Other models,
# notably hybrids, system/identity rows, history, and recovery remain out of
# scope. No runtime is permitted to infer automatic encryption from this list.
_IDENTITIES = {
    "task": "task_id", "task_milestone": "milestone_id",
    "review": "review_id", "review_comment": "comment_id",
    "review_comment_reply": "reply_id", "review_general_feedback": "feedback_id",
    "review_highlight": "highlight_id", "review_suggestion": "suggestion_id",
    "canvas": "canvas_id", "canvas_node": "node_id", "canvas_edge": "edge_id",
    "story_map": "map_id", "map_line": "line_id", "map_item": "item_id",
    "map_junction": "junction_id",
    # M12G: additional ordinary writer-authored Firestore records. Specialized
    # chapter CAS, system/identity, hybrid content and archive rows stay excluded.
    "document_tab": "tab_id", "scene": "scene_id",
    "comment_thread": "thread_id", "comment_message": "message_id",
    "questionnaire": "questionnaire_id", "questionnaire_question": "question_id",
    "cross_feature_reference": "reference_id", "markup_label": "label_id",
    "icon_marker": "icon_marker_id", "review_marker_label": "marker_label_id",
    "canvas_version": "version_id", "canvas_version_item": "item_id",
    # M12K: remaining independent writer-authored Firestore records. The
    # permission-bearing review_participant and system identities
    # still need dedicated designs and MUST NOT be encrypted through this list.
    "chapter": "chapter_id", "annotation": "annotation_id", "review_anchor": "anchor_id",
    "review_event": "event_id", "review_import_receipt": "import_id",
    "review_marker_assignment": "review_marker_id",
    "review_question_answer": "answer_id",
    "review_questionnaire_assignment": "questionnaire_id",
    "review_scope_chapter": "chapter_id", "review_scope_scene": "scene_id",
    # M12: review participant payloads are project-member-authorized today;
    # external reviewer sessions are not authoritative in this repository.
    "review_participant": "participant_id",
    # M12: hybrid metadata and authenticated GCS object pointers are sealed as
    # one private Firestore envelope. Their payload bytes use encrypted GCS.
    "cut": "cut_id", "checkpoint": "checkpoint_id",
    "review_snapshot": "snapshot_id",
    "hosted_revision_history": "revision_id",
    "project_asset": "asset_id",
    # M12T: user-specific state, path-bound to BOTH project and member.
    "member_bookmark": "user_id", "member_ui_state": "key",
}
# Preserve M12E's pilot encoding for Tasks. New families authenticate the
# entire exact canonical Firestore path (including all nested ancestor IDs).
_M12E_CONTEXT = frozenset({"task", "task_milestone"})
_MAX_PLAINTEXT = 300_000  # Base64 ciphertext and wrapped key stay comfortably under Firestore's 1 MiB limit.


class HostedPrivateFirestoreFields:
    """Fail-closed codec, explicitly injected into HostedDomainStore.

    User data may be arbitrarily structured JSON; if it is not operational
    identity/generation metadata it is sealed. No query/index for private fields
    may be performed at the provider layer.
    """

    def __init__(self, envelope: HostedEnvelopeCodec) -> None:
        if not isinstance(envelope, HostedEnvelopeCodec):
            raise HostedValidationError("HOSTED_PRIVATE_FIRESTORE_CODEC_INVALID")
        self.envelope = envelope

    @staticmethod
    def protects(logical_entity: str) -> bool:
        return logical_entity in _IDENTITIES

    @staticmethod
    def _context(logical_entity: str, ids: Mapping[str, str]) -> dict[str, str]:
        try:
            field = _IDENTITIES[logical_entity]
            project_id = require_project_id(ids["project_id"])
            record_id = require_opaque_id(ids[field], label=field)
            if logical_entity in _M12E_CONTEXT:
                # Retain the existing Task/milestone AAD contract verbatim.
                if logical_entity == "task_milestone":
                    parent_task = require_opaque_id(ids["task_id"], label="task_id")
                    record_id = parent_task + "__" + record_id
                    if len(record_id) > 160:
                        raise ValueError("nested path too long")
            else:
                model = model_for(logical_entity)
                if model.hosted_store not in {"firestore", "hybrid"} or not model.path_template.startswith("projects/{project_id}/"):
                    raise ValueError("unsupported private model")
                # render_model_path validates every required ancestor, rejects
                # extraneous IDs and enforces opaque/path-safe identifiers.
                path = render_model_path(model, **dict(ids))
                # SHA is of NON-CONTENT opaque document IDs, not manuscript
                # text. The authenticated entity and project remain explicit.
                record_id = hashlib.sha256("/".join(path).encode("ascii")).hexdigest()
        except (KeyError, TypeError, ValueError, HostedValidationError):
            raise HostedValidationError("HOSTED_PRIVATE_FIRESTORE_CONTEXT_INVALID") from None
        return {"project_id": project_id, "entity": logical_entity, "record_id": record_id}

    @staticmethod
    def _identity(logical_entity: str, ids: Mapping[str, str]) -> dict[str, str]:
        field = _IDENTITIES[logical_entity]
        identity = require_opaque_id(str(ids[field]), label=field)
        return {"id": identity, field: identity}

    @staticmethod
    def _canonical_path_ids(logical_entity: str) -> frozenset[str]:
        if logical_entity in _M12E_CONTEXT:
            return frozenset()  # preserve the prior Task/milestone payload contract
        template = model_for(logical_entity).path_template
        return frozenset(name for _, name, _, _ in Formatter().parse(template) if name)

    def seal(self, logical_entity: str, ids: Mapping[str, str], data: Mapping[str, Any]) -> dict[str, Any]:
        context = self._context(logical_entity, ids)
        identity = self._identity(logical_entity, ids)
        content = dict(data)
        if any(str(name).startswith("_bm_") for name in content):
            raise HostedValidationError("HOSTED_PRIVATE_FIRESTORE_RESERVED_FIELD")
        for field, expected in identity.items():
            if field in content and content[field] != expected:
                raise HostedValidationError("HOSTED_PRIVATE_FIRESTORE_IDENTITY_MISMATCH")
            content.pop(field, None)
        # Nested foreign keys are authoritative only in the document path,
        # never from a user-controlled encrypted object. Reject mismatches and
        # remove redundant matching path IDs from the private payload.
        for field in self._canonical_path_ids(logical_entity):
            if field in content and content[field] != ids[field]:
                raise HostedValidationError("HOSTED_PRIVATE_FIRESTORE_IDENTITY_MISMATCH")
            content.pop(field, None)
        if any(field in content for field in ("generation", "created_at", "updated_at")):
            raise HostedValidationError("HOSTED_PRIVATE_FIRESTORE_MANAGED_FIELD")
        try:
            encoded = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                                 allow_nan=False).encode("utf-8")
        except (TypeError, ValueError):
            raise HostedValidationError("HOSTED_PRIVATE_FIRESTORE_JSON_INVALID") from None
        if len(encoded) > _MAX_PLAINTEXT:
            raise HostedValidationError("HOSTED_PRIVATE_FIRESTORE_TOO_LARGE")
        try:
            sealed = self.envelope.seal(encoded, context=context)
        except Exception:
            raise HostedValidationError("HOSTED_PRIVATE_FIRESTORE_ENCRYPT_FAILED") from None
        # Document schema contains no manuscript/task strings, filenames, or
        # unkeyed hashes of those strings. The envelope must fit Firestore limits.
        return {**identity, PRIVATE_FIELD_NAME: sealed}

    def open(self, logical_entity: str, ids: Mapping[str, str], data: Mapping[str, Any]) -> dict[str, Any]:
        context = self._context(logical_entity, ids)
        identity = self._identity(logical_entity, ids)
        row = dict(data)
        if set(row) - (_OPERATIONAL_STORED | {_IDENTITIES[logical_entity]}):
            raise HostedValidationError("HOSTED_PRIVATE_FIRESTORE_PLAINTEXT_FORBIDDEN")
        if any(row.get(field) != expected for field, expected in identity.items()):
            raise HostedValidationError("HOSTED_PRIVATE_FIRESTORE_IDENTITY_MISMATCH")
        envelope = row.get(PRIVATE_FIELD_NAME)
        if not isinstance(envelope, Mapping) or envelope.get("format") != FORMAT:
            raise HostedValidationError("HOSTED_PRIVATE_FIRESTORE_ENVELOPE_REQUIRED")
        try:
            payload = self.envelope.open(envelope, context=context)
            if len(payload) > _MAX_PLAINTEXT:
                raise ValueError()
            content = json.loads(payload.decode("utf-8"))
        except Exception:
            raise HostedValidationError("HOSTED_PRIVATE_FIRESTORE_DECRYPT_FAILED") from None
        if not isinstance(content, dict) or any(
            name in content or str(name).startswith("_bm_")
            for name in ("id", _IDENTITIES[logical_entity], "generation", "created_at", "updated_at")
        ):
            raise HostedValidationError("HOSTED_PRIVATE_FIRESTORE_PAYLOAD_INVALID")
        if any(str(name).startswith("_bm_") for name in content):
            raise HostedValidationError("HOSTED_PRIVATE_FIRESTORE_PAYLOAD_INVALID")
        if set(content) & self._canonical_path_ids(logical_entity):
            raise HostedValidationError("HOSTED_PRIVATE_FIRESTORE_PAYLOAD_INVALID")
        # Never expose internal ciphertext and operation receipts to consumers.
        result = {name: value for name, value in row.items() if not str(name).startswith("_bm_")}
        result.update(content)
        return result
