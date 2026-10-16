from __future__ import annotations

import hashlib
import json
import string
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .contracts import (
    DocumentAlreadyExists,
    DocumentConflict,
    DocumentNotFound,
    DocumentStore,
    HostedAuthorizationError,
    HostedValidationError,
    ModelContract,
    ProjectUnavailable,
    StoredDocument,
    Transaction,
    require_document_path,
)
from .ids import identity_binding_id, new_project_id, new_user_id, require_opaque_id, require_project_id, require_user_id
from .model_registry import model_for

_RESERVED_PREFIX = "_bm_"
_ROLES = {"owner", "editor"}
_GENERIC_ENTITY_DENYLIST = {
    "project",
    "project_membership",
    "hosted_model_metadata",
    "recovery_state",
    "hosted_user_profile",
    "identity_binding",
}
_SECRET_FIELD_FRAGMENTS = {"password", "client_secret", "refresh_token", "access_token", "private_key", "credential"}
_MANAGED_CREATE_FIELDS = {"generation", "created_at", "updated_at"}


class UtcClock:
    def now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class HostedEntity:
    logical_entity: str
    path: tuple[str, ...]
    data: Mapping[str, Any]


def _canonical_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    for key in out:
        normalized = str(key).lower()
        if normalized.startswith(_RESERVED_PREFIX):
            raise HostedValidationError("HOSTED_RESERVED_FIELD")
        if any(fragment in normalized for fragment in _SECRET_FIELD_FRAGMENTS):
            raise HostedValidationError("HOSTED_SECRET_FIELD_FORBIDDEN")
    try:
        json.dumps(out, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise HostedValidationError("HOSTED_DOCUMENT_NOT_JSON") from exc
    return out


def _reject_managed_create_fields(payload: Mapping[str, Any]) -> None:
    if set(map(str, payload)) & _MANAGED_CREATE_FIELDS:
        raise HostedValidationError("HOSTED_MANAGED_FIELD_RESERVED")


def _fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _operation_id(value: str) -> str:
    return require_opaque_id(value, label="operation_id")


def _entity_id(value: str, label: str) -> str:
    if label == "project_id":
        return require_project_id(value)
    if label == "user_id":
        return require_user_id(value)
    return require_opaque_id(value, label=label)


def render_model_path(model: ModelContract, **ids: str) -> tuple[str, ...]:
    fields = [name for _, name, _, _ in string.Formatter().parse(model.path_template) if name]
    missing = [field for field in fields if field not in ids]
    extra = [key for key in ids if key not in fields]
    if missing or extra:
        raise HostedValidationError("HOSTED_MODEL_PATH_ARGUMENTS_INVALID")
    rendered = model.path_template
    for field in fields:
        rendered = rendered.replace("{" + field + "}", _entity_id(ids[field], field))
    return require_document_path(tuple(rendered.split("/")))


class HostedStructuredRepository:
    """CAS/idempotency primitive shared by hosted structured repositories."""

    def __init__(self, store: DocumentStore, *, clock: Any | None = None) -> None:
        self.store = store
        self.clock = clock or UtcClock()

    def get(self, logical_entity: str, **ids: str) -> HostedEntity:
        model = model_for(logical_entity)
        path = render_model_path(model, **ids)
        row = self.store.get(path)
        if row is None:
            raise DocumentNotFound("HOSTED_DOCUMENT_NOT_FOUND")
        return HostedEntity(logical_entity, path, dict(row.data))

    def create(self, logical_entity: str, payload: Mapping[str, Any], *, operation_id: str, **ids: str) -> HostedEntity:
        model = model_for(logical_entity)
        path = render_model_path(model, **ids)
        public = _canonical_payload(payload)
        _reject_managed_create_fields(public)
        op = _operation_id(operation_id)
        fingerprint = _fingerprint(public)
        stamp = self.clock.now_iso()

        def mutate(tx: Transaction) -> HostedEntity:
            existing = tx.get(path)
            if existing is not None:
                data = dict(existing.data)
                if data.get("_bm_create_operation") == op and data.get("_bm_create_fingerprint") == fingerprint:
                    return HostedEntity(logical_entity, path, data)
                raise DocumentAlreadyExists("HOSTED_DOCUMENT_ALREADY_EXISTS")
            data = dict(public)
            data.setdefault("generation", 1)
            data.setdefault("created_at", stamp)
            data["updated_at"] = stamp
            data["_bm_create_operation"] = op
            data["_bm_create_fingerprint"] = fingerprint
            tx.create(path, data)
            return HostedEntity(logical_entity, path, data)

        return self.store.run_transaction(mutate)

    def update(
        self,
        logical_entity: str,
        patch: Mapping[str, Any],
        *,
        expected_generation: int,
        operation_id: str,
        **ids: str,
    ) -> HostedEntity:
        model = model_for(logical_entity)
        if model.concurrency_policy in {"immutable_create_only", "append_only"}:
            raise HostedValidationError("HOSTED_ENTITY_IMMUTABLE")
        path = render_model_path(model, **ids)
        public = _canonical_payload(patch)
        if "generation" in public or "created_at" in public:
            raise HostedValidationError("HOSTED_GENERATION_FIELD_RESERVED")
        expected = int(expected_generation)
        if expected < 1:
            raise HostedValidationError("HOSTED_GENERATION_INVALID")
        op = _operation_id(operation_id)
        patch_fingerprint = _fingerprint(public)
        stamp = self.clock.now_iso()

        def mutate(tx: Transaction) -> HostedEntity:
            existing = tx.get(path)
            if existing is None:
                raise DocumentNotFound("HOSTED_DOCUMENT_NOT_FOUND")
            data = dict(existing.data)
            if data.get("_bm_last_operation") == op and data.get("_bm_last_fingerprint") == patch_fingerprint:
                return HostedEntity(logical_entity, path, data)
            current = int(data.get("generation") or 0)
            if current != expected:
                raise DocumentConflict("HOSTED_GENERATION_CONFLICT")
            data.update(public)
            data["generation"] = current + 1
            data["updated_at"] = stamp
            data["_bm_last_operation"] = op
            data["_bm_last_fingerprint"] = patch_fingerprint
            tx.set(path, data)
            return HostedEntity(logical_entity, path, data)

        return self.store.run_transaction(mutate)

    def delete(self, logical_entity: str, *, expected_generation: int, **ids: str) -> None:
        model = model_for(logical_entity)
        if model.concurrency_policy in {"immutable_create_only", "append_only"}:
            raise HostedValidationError("HOSTED_ENTITY_IMMUTABLE")
        path = render_model_path(model, **ids)
        expected = int(expected_generation)

        def mutate(tx: Transaction) -> None:
            existing = tx.get(path)
            if existing is None:
                return None
            current = int(existing.data.get("generation") or 0)
            if current != expected:
                raise DocumentConflict("HOSTED_GENERATION_CONFLICT")
            tx.delete(path)
            return None

        self.store.run_transaction(mutate)


class HostedUserRepository:
    def __init__(self, state: HostedStructuredRepository) -> None:
        self.state = state

    def create(self, *, display_name: str, operation_id: str, user_id: str | None = None) -> dict[str, Any]:
        uid = require_user_id(user_id) if user_id is not None else new_user_id()
        name = str(display_name).strip()
        if not name or len(name) > 160:
            raise HostedValidationError("HOSTED_DISPLAY_NAME_INVALID")
        return dict(self.state.create("hosted_user_profile", {"user_id": uid, "display_name": name, "status": "active"}, operation_id=operation_id, user_id=uid).data)

    def get_self(self, user_id: str) -> dict[str, Any]:
        uid = require_user_id(user_id)
        return dict(self.state.get("hosted_user_profile", user_id=uid).data)

    def update_self(self, user_id: str, patch: Mapping[str, Any], *, expected_generation: int, operation_id: str) -> dict[str, Any]:
        uid = require_user_id(user_id)
        allowed = {"display_name", "last_project_id", "locale"}
        clean = {str(k): v for k, v in patch.items() if str(k) in allowed}
        if set(map(str, patch)) - allowed:
            raise HostedValidationError("HOSTED_USER_PATCH_FIELD_FORBIDDEN")
        return dict(self.state.update("hosted_user_profile", clean, expected_generation=expected_generation, operation_id=operation_id, user_id=uid).data)


class HostedIdentityBindingRepository:
    def __init__(self, state: HostedStructuredRepository) -> None:
        self.state = state
        self.store = state.store

    def bind(self, provider_uid: str, user_id: str, *, operation_id: str) -> dict[str, Any]:
        uid = require_user_id(user_id)
        binding = identity_binding_id(provider_uid)
        op = _operation_id(operation_id)
        provider_uid = str(provider_uid)
        path = render_model_path(model_for("identity_binding"), uid_hash=binding)
        user_path = render_model_path(model_for("hosted_user_profile"), user_id=uid)
        stamp = self.state.clock.now_iso()
        fingerprint = _fingerprint({"provider_uid": provider_uid, "user_id": uid, "status": "active"})

        def mutate(tx: Transaction) -> dict[str, Any]:
            if tx.get(user_path) is None:
                raise DocumentNotFound("HOSTED_USER_NOT_FOUND")
            existing = tx.get(path)
            if existing is not None:
                data = dict(existing.data)
                if data.get("provider_uid") != provider_uid:
                    raise HostedAuthorizationError("HOSTED_IDENTITY_BINDING_HASH_COLLISION")
                if data.get("user_id") == uid and data.get("_bm_create_operation") == op:
                    return data
                if data.get("user_id") != uid:
                    raise HostedAuthorizationError("HOSTED_IDENTITY_ALREADY_BOUND")
                return data
            data = {
                "provider_uid": provider_uid,
                "user_id": uid,
                "status": "active",
                "generation": 1,
                "created_at": stamp,
                "updated_at": stamp,
                "_bm_create_operation": op,
                "_bm_create_fingerprint": fingerprint,
            }
            tx.create(path, data)
            return data

        return self.store.run_transaction(mutate)

    def resolve(self, provider_uid: str) -> str | None:
        binding = identity_binding_id(provider_uid)
        try:
            row = self.state.get("identity_binding", uid_hash=binding)
        except DocumentNotFound:
            return None
        exact = str(row.data.get("provider_uid") or "")
        if exact != str(provider_uid):
            raise HostedAuthorizationError("HOSTED_IDENTITY_BINDING_HASH_COLLISION")
        user_id = str(row.data.get("user_id") or "")
        return require_user_id(user_id)


class HostedProjectRepository:
    """Firestore-shaped project/membership authority with fail-closed lookups."""

    def __init__(self, store: DocumentStore, *, clock: Any | None = None) -> None:
        self.store = store
        self.clock = clock or UtcClock()
        # Only injected by an explicit dual-codec private pilot. No local or
        # default hosted project requires KMS to create or open a project.
        self._private_titles = None

    def configure_private_titles(self, envelope) -> None:
        from hosted.security.envelope import HostedEnvelopeCodec
        if not isinstance(envelope, HostedEnvelopeCodec):
            raise HostedValidationError("HOSTED_PRIVATE_TITLE_CODEC_INVALID")
        if self._private_titles is not None and self._private_titles is not envelope:
            raise HostedValidationError("HOSTED_PRIVATE_TITLE_CODEC_CHANGED")
        self._private_titles = envelope

    @staticmethod
    def _title_context(pid: str) -> dict[str, str]:
        return {'project_id': pid, 'entity': 'project', 'record_id': pid}

    def _seal_title(self, pid: str, title: str) -> dict[str, Any]:
        if self._private_titles is None:
            return {'title': title}
        try:
            sealed = self._private_titles.seal(
                json.dumps({'title': title}, separators=(',', ':'), ensure_ascii=False).encode('utf-8'),
                context=self._title_context(pid))
        except Exception:
            raise HostedValidationError('HOSTED_PRIVATE_TITLE_ENCRYPT_FAILED') from None
        return {'title': 'Private project', '_bm_private_title': sealed}

    def _open_project(self, pid: str, raw: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(raw)
        envelope = data.get('_bm_private_title')
        if self._private_titles is None:
            if envelope is not None:
                raise HostedValidationError('HOSTED_PRIVATE_TITLE_CODEC_REQUIRED')
            return data
        if not isinstance(envelope, Mapping) or data.get('title') != 'Private project':
            raise HostedValidationError('HOSTED_PRIVATE_TITLE_REQUIRED')
        try:
            decoded = json.loads(self._private_titles.open(
                envelope, context=self._title_context(pid)).decode('utf-8'))
            title = decoded['title']
            if set(decoded) != {'title'} or not isinstance(title, str) or not 1 <= len(title) <= 240:
                raise ValueError()
        except Exception:
            raise HostedValidationError('HOSTED_PRIVATE_TITLE_DECRYPT_FAILED') from None
        data.pop('_bm_private_title', None)
        data['title'] = title
        return data

    @staticmethod
    def _project_path(project_id: str) -> tuple[str, ...]:
        return ("projects", require_project_id(project_id))

    @staticmethod
    def _member_path(project_id: str, user_id: str) -> tuple[str, ...]:
        return ("projects", require_project_id(project_id), "members", require_user_id(user_id))

    def _require_member(self, tx: Transaction, project_id: str, user_id: str, *, owner: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
        project = tx.get(self._project_path(project_id))
        member = tx.get(self._member_path(project_id, user_id))
        if project is None or member is None or str(project.data.get("status") or "active") != "active":
            raise ProjectUnavailable("PROJECT_NOT_FOUND")
        member_data = dict(member.data)
        if owner and member_data.get("role") != "owner":
            raise HostedAuthorizationError("PROJECT_PERMISSION_DENIED")
        return dict(project.data), member_data

    def create(
        self,
        title: str,
        *,
        owner_user_id: str,
        operation_id: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        pid = require_project_id(project_id) if project_id is not None else new_project_id()
        uid = require_user_id(owner_user_id)
        op = _operation_id(operation_id)
        name = str(title).strip()
        if not name or len(name) > 240:
            raise HostedValidationError("HOSTED_PROJECT_TITLE_INVALID")
        stamp = self.clock.now_iso()
        stored_title = self._seal_title(pid, name)
        fingerprint = _fingerprint({"project_id": pid, "owner_user_id": uid,
                                    **stored_title})
        project_path = self._project_path(pid)
        member_path = self._member_path(pid, uid)
        owner_member_id = "member_" + secrets_token()
        # Replays must validate the old plaintext outside the transaction;
        # KMS calls inside retryable Firestore callbacks are forbidden.
        if self._private_titles is not None:
            try:
                previous = self.get_for_user(pid, uid)
            except ProjectUnavailable:
                previous = None
            if previous is not None and previous.get('_bm_create_operation') == op:
                if previous.get('title') != name:
                    raise DocumentAlreadyExists('HOSTED_PROJECT_ALREADY_EXISTS')
                return previous

        def mutate(tx: Transaction) -> dict[str, Any]:
            user_path = render_model_path(model_for("hosted_user_profile"), user_id=uid)
            if tx.get(user_path) is None:
                raise DocumentNotFound("HOSTED_USER_NOT_FOUND")
            existing = tx.get(project_path)
            if existing is not None:
                project = dict(existing.data)
                owner = tx.get(member_path)
                if (
                    project.get("_bm_create_operation") == op
                    and (self._private_titles is not None or project.get("_bm_create_fingerprint") == fingerprint)
                    and owner is not None
                    and owner.data.get("role") == "owner"
                ):
                    return project
                raise DocumentAlreadyExists("HOSTED_PROJECT_ALREADY_EXISTS")
            if tx.get(member_path) is not None:
                raise DocumentAlreadyExists("HOSTED_PROJECT_MEMBERSHIP_ALREADY_EXISTS")
            project = {
                "project_id": pid,
                **stored_title,
                "status": "active",
                "generation": 1,
                "created_at": stamp,
                "updated_at": stamp,
                "_bm_create_operation": op,
                "_bm_create_fingerprint": fingerprint,
            }
            member = {
                "user_id": uid,
                "member_id": owner_member_id,
                "role": "owner",
                "generation": 1,
                "created_at": stamp,
                "updated_at": stamp,
                "last_opened_at": stamp,
                "_bm_create_operation": op,
            }
            tx.create(project_path, project)
            tx.create(member_path, member)
            return project

        restored = self._open_project(pid, self.store.run_transaction(mutate))
        # Concurrent idempotent creates may race past the preflight. Never
        # return a project created by the same operation with different content.
        if self._private_titles is not None and restored.get('title') != name:
            raise DocumentAlreadyExists('HOSTED_PROJECT_ALREADY_EXISTS')
        return restored

    def get_for_user(self, project_id: str, user_id: str) -> dict[str, Any]:
        pid = require_project_id(project_id)
        uid = require_user_id(user_id)

        def read(tx: Transaction) -> dict[str, Any]:
            project, _ = self._require_member(tx, pid, uid)
            return project

        return self._open_project(pid, self.store.run_transaction(read))

    def membership(self, project_id: str, user_id: str) -> dict[str, Any] | None:
        pid = require_project_id(project_id)
        uid = require_user_id(user_id)
        row = self.store.get(self._member_path(pid, uid))
        return dict(row.data) if row is not None else None

    def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        uid = require_user_id(user_id)
        memberships = self.store.query_collection_group("members", field="user_id", value=uid)
        projects: list[dict[str, Any]] = []
        for membership in memberships:
            if len(membership.path) < 4 or membership.path[-2] != "members":
                continue
            pid = membership.path[-3]
            try:
                projects.append(self.get_for_user(pid, uid))
            except ProjectUnavailable:
                continue
        return sorted(projects, key=lambda row: (str(row.get("updated_at") or ""), str(row.get("project_id") or "")), reverse=True)

    def update(self, project_id: str, user_id: str, patch: Mapping[str, Any], *, expected_generation: int, operation_id: str) -> dict[str, Any]:
        pid = require_project_id(project_id)
        uid = require_user_id(user_id)
        clean = _canonical_payload(patch)
        allowed = {"title"}
        if set(clean) - allowed:
            raise HostedValidationError("HOSTED_PROJECT_PATCH_FIELD_FORBIDDEN")
        op = _operation_id(operation_id)
        stored_patch = self._seal_title(pid, str(clean['title'])) if 'title' in clean else {}
        fingerprint = _fingerprint(stored_patch)
        stamp = self.clock.now_iso()
        if self._private_titles is not None:
            prior = self.get_for_user(pid, uid)
            if prior.get('_bm_last_operation') == op:
                if prior.get('title') != clean.get('title'):
                    raise DocumentConflict('HOSTED_PRIVATE_TITLE_OPERATION_CONFLICT')
                return prior

        def mutate(tx: Transaction) -> dict[str, Any]:
            project, member = self._require_member(tx, pid, uid)
            if member.get("role") not in _ROLES:
                raise HostedAuthorizationError("PROJECT_PERMISSION_DENIED")
            if project.get("_bm_last_operation") == op and project.get("_bm_last_fingerprint") == fingerprint:
                return project
            current = int(project.get("generation") or 0)
            if current != int(expected_generation):
                raise DocumentConflict("HOSTED_GENERATION_CONFLICT")
            project.update(stored_patch)
            project["generation"] = current + 1
            project["updated_at"] = stamp
            project["_bm_last_operation"] = op
            project["_bm_last_fingerprint"] = fingerprint
            tx.set(self._project_path(pid), project)
            return project

        return self._open_project(pid, self.store.run_transaction(mutate))

    def add_member(
        self,
        project_id: str,
        actor_user_id: str,
        target_user_id: str,
        role: str,
        *,
        expected_project_generation: int,
        operation_id: str,
    ) -> dict[str, Any]:
        pid = require_project_id(project_id)
        actor = require_user_id(actor_user_id)
        target = require_user_id(target_user_id)
        role = str(role)
        if role != "editor":
            raise HostedValidationError("PROJECT_MEMBER_ROLE_INVALID")
        op = _operation_id(operation_id)
        stamp = self.clock.now_iso()
        member_path = self._member_path(pid, target)
        target_user_path = render_model_path(model_for("hosted_user_profile"), user_id=target)
        member_id = "member_" + secrets_token()

        def mutate(tx: Transaction) -> dict[str, Any]:
            project, _ = self._require_member(tx, pid, actor, owner=True)
            if tx.get(target_user_path) is None:
                raise DocumentNotFound("HOSTED_USER_NOT_FOUND")
            existing = tx.get(member_path)
            if existing is not None:
                data = dict(existing.data)
                if data.get("_bm_create_operation") == op and data.get("role") == role:
                    return data
                raise DocumentAlreadyExists("PROJECT_MEMBER_EXISTS")
            current = int(project.get("generation") or 0)
            if current != int(expected_project_generation):
                raise DocumentConflict("HOSTED_GENERATION_CONFLICT")
            member = {
                "user_id": target,
                "member_id": member_id,
                "role": role,
                "generation": 1,
                "created_at": stamp,
                "updated_at": stamp,
                "last_opened_at": "",
                "_bm_create_operation": op,
            }
            project["generation"] = current + 1
            project["updated_at"] = stamp
            tx.create(member_path, member)
            tx.set(self._project_path(pid), project)
            return member

        return self.store.run_transaction(mutate)

    def remove_member(
        self,
        project_id: str,
        actor_user_id: str,
        target_user_id: str,
        *,
        expected_project_generation: int,
    ) -> None:
        pid = require_project_id(project_id)
        actor = require_user_id(actor_user_id)
        target = require_user_id(target_user_id)
        member_path = self._member_path(pid, target)
        stamp = self.clock.now_iso()

        def mutate(tx: Transaction) -> None:
            project, _ = self._require_member(tx, pid, actor, owner=True)
            target_member = tx.get(member_path)
            if target_member is None:
                return None
            if target_member.data.get("role") == "owner":
                raise HostedAuthorizationError("PROJECT_OWNER_REMOVE_FORBIDDEN")
            current = int(project.get("generation") or 0)
            if current != int(expected_project_generation):
                raise DocumentConflict("HOSTED_GENERATION_CONFLICT")
            project["generation"] = current + 1
            project["updated_at"] = stamp
            tx.delete(member_path)
            tx.set(self._project_path(pid), project)
            return None

        self.store.run_transaction(mutate)

    def tombstone(self, project_id: str, owner_user_id: str, *, expected_generation: int, operation_id: str) -> dict[str, Any]:
        pid = require_project_id(project_id)
        uid = require_user_id(owner_user_id)
        op = _operation_id(operation_id)
        stamp = self.clock.now_iso()

        def mutate(tx: Transaction) -> dict[str, Any]:
            project_row = tx.get(self._project_path(pid))
            member_row = tx.get(self._member_path(pid, uid))
            if project_row is None or member_row is None or member_row.data.get("role") != "owner":
                raise ProjectUnavailable("PROJECT_NOT_FOUND")
            project = dict(project_row.data)
            if project.get("status") == "deleted" and project.get("_bm_last_operation") == op:
                return project
            if project.get("status") != "active":
                raise ProjectUnavailable("PROJECT_NOT_FOUND")
            current = int(project.get("generation") or 0)
            if current != int(expected_generation):
                raise DocumentConflict("HOSTED_GENERATION_CONFLICT")
            project["status"] = "deleted"
            project["deleted_at"] = stamp
            project["generation"] = current + 1
            project["updated_at"] = stamp
            project["_bm_last_operation"] = op
            tx.set(self._project_path(pid), project)
            return project

        return self._open_project(pid, self.store.run_transaction(mutate))


class HostedProjectEntityRepository:
    """Authorization-aware CRUD for structured project child entities."""

    def __init__(self, store: DocumentStore, *, clock: Any | None = None) -> None:
        self.store = store
        self.state = HostedStructuredRepository(store, clock=clock)
        self.projects = HostedProjectRepository(store, clock=clock)

    @staticmethod
    def _model(logical_entity: str) -> ModelContract:
        model = model_for(logical_entity)
        if model.logical_entity in _GENERIC_ENTITY_DENYLIST or not model.path_template.startswith("projects/{project_id}/"):
            raise HostedValidationError("HOSTED_ENTITY_REQUIRES_SPECIALIZED_REPOSITORY")
        return model

    def _authorize(self, tx: Transaction, model: ModelContract, ids: Mapping[str, str], user_id: str, *, mutation: bool) -> None:
        if model.authorization_policy == "system_only":
            raise HostedAuthorizationError("HOSTED_SYSTEM_ONLY_ENTITY")
        if model.authorization_policy == "user_self":
            target = ids.get("user_id")
            if not target or require_user_id(str(target)) != require_user_id(user_id):
                raise HostedAuthorizationError("HOSTED_USER_SELF_REQUIRED")
            return
        project_id = ids.get("project_id")
        if not project_id:
            raise HostedValidationError("HOSTED_PROJECT_ID_REQUIRED")
        owner = model.authorization_policy == "project_owner"
        try:
            self.projects._require_member(tx, str(project_id), user_id, owner=owner)
            return
        except ProjectUnavailable:
            if model.authorization_policy != "project_member_or_review_participant":
                raise
            # The frozen Milestone-2 contract deliberately does not equate a review
            # participant record with a Breadmaker user account. External reviewer
            # access must later present a server-issued reviewer session bound to the
            # review + participant + active snapshot. Milestone 3 has no such session
            # authority yet, so the structured repository fails closed rather than
            # inventing user_id/email based reviewer authorization.
            raise ProjectUnavailable("PROJECT_NOT_FOUND")

    def create(self, logical_entity: str, ids: Mapping[str, str], payload: Mapping[str, Any], *, user_id: str, operation_id: str) -> dict[str, Any]:
        model = self._model(logical_entity)
        path = render_model_path(model, **dict(ids))
        public = _canonical_payload(payload)
        _reject_managed_create_fields(public)
        op = _operation_id(operation_id)
        fingerprint = _fingerprint(public)
        stamp = self.state.clock.now_iso()

        def mutate(tx: Transaction) -> dict[str, Any]:
            self._authorize(tx, model, ids, require_user_id(user_id), mutation=True)
            existing = tx.get(path)
            if existing is not None:
                data = dict(existing.data)
                if data.get("_bm_create_operation") == op and data.get("_bm_create_fingerprint") == fingerprint:
                    return data
                raise DocumentAlreadyExists("HOSTED_DOCUMENT_ALREADY_EXISTS")
            data = dict(public)
            data.setdefault("generation", 1)
            data.setdefault("created_at", stamp)
            data["updated_at"] = stamp
            data["_bm_create_operation"] = op
            data["_bm_create_fingerprint"] = fingerprint
            tx.create(path, data)
            return data

        return self.store.run_transaction(mutate)

    def get(self, logical_entity: str, ids: Mapping[str, str], *, user_id: str) -> dict[str, Any]:
        model = self._model(logical_entity)
        path = render_model_path(model, **dict(ids))

        def read(tx: Transaction) -> dict[str, Any]:
            self._authorize(tx, model, ids, require_user_id(user_id), mutation=False)
            row = tx.get(path)
            if row is None:
                raise DocumentNotFound("HOSTED_DOCUMENT_NOT_FOUND")
            return dict(row.data)

        return self.store.run_transaction(read)

    def update(self, logical_entity: str, ids: Mapping[str, str], patch: Mapping[str, Any], *, user_id: str, expected_generation: int, operation_id: str) -> dict[str, Any]:
        model = self._model(logical_entity)
        if model.concurrency_policy in {"immutable_create_only", "append_only"}:
            raise HostedValidationError("HOSTED_ENTITY_IMMUTABLE")
        path = render_model_path(model, **dict(ids))
        public = _canonical_payload(patch)
        if "generation" in public or "created_at" in public:
            raise HostedValidationError("HOSTED_GENERATION_FIELD_RESERVED")
        op = _operation_id(operation_id)
        fingerprint = _fingerprint(public)
        stamp = self.state.clock.now_iso()

        def mutate(tx: Transaction) -> dict[str, Any]:
            self._authorize(tx, model, ids, require_user_id(user_id), mutation=True)
            row = tx.get(path)
            if row is None:
                raise DocumentNotFound("HOSTED_DOCUMENT_NOT_FOUND")
            data = dict(row.data)
            if data.get("_bm_last_operation") == op and data.get("_bm_last_fingerprint") == fingerprint:
                return data
            current = int(data.get("generation") or 0)
            if current != int(expected_generation):
                raise DocumentConflict("HOSTED_GENERATION_CONFLICT")
            data.update(public)
            data["generation"] = current + 1
            data["updated_at"] = stamp
            data["_bm_last_operation"] = op
            data["_bm_last_fingerprint"] = fingerprint
            tx.set(path, data)
            return data

        return self.store.run_transaction(mutate)

    def delete(self, logical_entity: str, ids: Mapping[str, str], *, user_id: str, expected_generation: int) -> None:
        model = self._model(logical_entity)
        if model.concurrency_policy in {"immutable_create_only", "append_only"}:
            raise HostedValidationError("HOSTED_ENTITY_IMMUTABLE")
        path = render_model_path(model, **dict(ids))

        def mutate(tx: Transaction) -> None:
            self._authorize(tx, model, ids, require_user_id(user_id), mutation=True)
            row = tx.get(path)
            if row is None:
                return None
            if int(row.data.get("generation") or 0) != int(expected_generation):
                raise DocumentConflict("HOSTED_GENERATION_CONFLICT")
            tx.delete(path)
            return None

        self.store.run_transaction(mutate)


def secrets_token() -> str:
    # Imported here rather than exposing UUID/path-shaped identity to hosted data.
    import secrets
    return secrets.token_hex(16)
