from __future__ import annotations

"""Serverless hosted runtime services for Milestone 5.

The classes in this module intentionally depend only on the hosted Firestore/GCS
adapters plus environment-neutral runtime/auth contracts.  They never import the local
workspace, SQLite storage package, project directories, or local home-directory state.
"""

import base64
import hashlib
import os
import secrets
import time
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from auth.accounts import AccountIdentityError, ExternalIdentity, NullAccountIdentityService
from auth.identity import Actor, AuthorizationError, permissions_for_role
from auth.sessions import AuthenticationError
from hosted.domain_store import HostedDomainStore, operation_id
from hosted.idempotency import stable_project_id
from hosted.firestore import (
    HostedProjectRepository as FirestoreProjectRepository,
    HostedStructuredRepository,
    HostedUserRepository,
)
from hosted.firestore.contracts import DocumentConflict, DocumentNotFound, HostedAuthorizationError, HostedValidationError, ProjectUnavailable
from hosted.firestore.ids import identity_binding_id, new_user_id, require_opaque_id, require_project_id, require_user_id
from hosted.storage.assets import HostedAssetStore
from hosted.storage.encrypted_objects import EncryptedObjectReference
from hosted.security.envelope import FORMAT as _PRIVATE_ENVELOPE_FORMAT
from hosted.storage.contracts import ObjectStore
from hosted.storage.keys import user_avatar_key
from hosted.storage.publication import HostedHybridObjectPublisher, ImmutableObjectPublisher
from runtime.contracts import ProjectRequestContext, StoredAsset
from security.image_security import HOSTED_DEFAULT_IMAGE_BYTES, UnsafeImage, inspect_hosted_image_bytes


_DEV_USER_ID = "usr_00000000000000000000000000000001"
_AVATAR_LIMIT = 5 * 1024 * 1024
_REVISION_LIMIT = 32 * 1024 * 1024


def _clean_public(row: Mapping[str, Any]) -> dict[str, Any]:
    return {str(k): v for k, v in dict(row).items() if not str(k).startswith("_bm_")}


def _project_public(row: Mapping[str, Any], *, owner_user_id: str | None = None) -> dict[str, Any]:
    data = _clean_public(row)
    pid = str(data.get("project_id") or "")
    title = str(data.get("title") or "Untitled")
    out = {
        **data,
        "id": pid,
        "project_id": pid,
        "name": title,
        "title": title,
    }
    if owner_user_id:
        out["owner_user_id"] = owner_user_id
    return out


class HostedCloudAccountIdentityService:
    """Firestore-backed external identity registry.

    Provider credentials/tokens are never stored here.  The binding key is the exact
    (issuer, subject) tuple hashed into the frozen identity-binding document id.  Email
    remains metadata and is never an account lookup key.
    """

    enabled = True

    def __init__(self, documents) -> None:
        self.documents = documents
        self.state = HostedStructuredRepository(documents)
        self.users = HostedUserRepository(self.state)

    @staticmethod
    def _provider_uid(issuer: str, subject: str) -> tuple[str, str, str]:
        issuer = str(issuer or "").strip()
        subject = str(subject or "").strip()
        if not issuer or len(issuer) > 2048:
            raise AccountIdentityError("IDENTITY_ISSUER_REQUIRED")
        if not subject or len(subject) > 1024:
            raise AccountIdentityError("IDENTITY_SUBJECT_REQUIRED")
        return issuer, subject, issuer + "\n" + subject

    @staticmethod
    def _identity(data: Mapping[str, Any]) -> ExternalIdentity:
        return ExternalIdentity(
            issuer=str(data.get("issuer") or ""),
            subject=str(data.get("subject") or ""),
            user_id=str(data.get("user_id") or ""),
            provider=str(data.get("provider") or ""),
            email=str(data.get("email") or ""),
            email_verified=bool(data.get("email_verified")),
            created_at=float(data.get("created_at_epoch") or 0.0),
            updated_at=float(data.get("updated_at_epoch") or 0.0),
        )

    def _path(self, provider_uid: str) -> tuple[str, ...]:
        return ("identity_bindings", identity_binding_id(provider_uid))

    def resolve_external_identity(self, issuer: str, subject: str) -> ExternalIdentity | None:
        issuer, subject, provider_uid = self._provider_uid(issuer, subject)
        row = self.documents.get(self._path(provider_uid))
        if row is None:
            return None
        data = dict(row.data)
        if str(data.get("issuer")) != issuer or str(data.get("subject")) != subject:
            raise AccountIdentityError("ACCOUNT_IDENTITY_HASH_COLLISION")
        uid = require_user_id(str(data.get("user_id") or ""))
        try:
            self.users.get_self(uid)
        except DocumentNotFound as exc:
            raise AccountIdentityError("ACCOUNT_IDENTITY_USER_MISSING") from exc
        return self._identity(data)

    def resolve_or_create_external_identity(
        self, issuer: str, subject: str, *, provider: str, display_name: str,
        email: str = "", email_verified: bool = False,
    ) -> ExternalIdentity:
        issuer, subject, provider_uid = self._provider_uid(issuer, subject)
        provider = str(provider or "").strip()[:80]
        if not provider:
            raise AccountIdentityError("IDENTITY_PROVIDER_REQUIRED")
        display_name = str(display_name or "").strip()[:160] or "Writer"
        email = str(email or "").strip()[:320]
        path = self._path(provider_uid)
        now = time.time()

        def mutate(tx):
            existing = tx.get(path)
            if existing is not None:
                data = dict(existing.data)
                if str(data.get("issuer")) != issuer or str(data.get("subject")) != subject:
                    raise AccountIdentityError("ACCOUNT_IDENTITY_HASH_COLLISION")
                data["provider"] = provider
                data["email"] = email
                data["email_verified"] = bool(email_verified)
                data["updated_at_epoch"] = now
                tx.set(path, data)
                return data
            uid = new_user_id()
            user_path = ("users", uid)
            user = {
                "user_id": uid,
                "display_name": display_name,
                "status": "active",
                "generation": 1,
                "created_at": _iso_now(),
                "updated_at": _iso_now(),
                "_bm_create_operation": operation_id("account_user"),
            }
            binding = {
                "provider_uid": provider_uid,
                "issuer": issuer,
                "subject": subject,
                "user_id": uid,
                "provider": provider,
                "email": email,
                "email_verified": bool(email_verified),
                "status": "active",
                "generation": 1,
                "created_at_epoch": now,
                "updated_at_epoch": now,
                "created_at": _iso_now(),
                "updated_at": _iso_now(),
            }
            tx.create(user_path, user)
            tx.create(path, binding)
            return binding

        return self._identity(self.documents.run_transaction(mutate))

    def identities_for_user(self, user_id: str) -> list[ExternalIdentity]:
        uid = require_user_id(user_id)
        rows = self.documents.query_collection(("identity_bindings",), field="user_id", value=uid)
        return [self._identity(row.data) for row in rows]

    def primary_identity_for_user(self, user_id: str) -> ExternalIdentity | None:
        rows = sorted(self.identities_for_user(user_id), key=lambda item: (item.created_at, item.issuer, item.subject))
        return rows[0] if rows else None

    def link_external_identity_to_user(
        self, user_id: str, issuer: str, subject: str, *, provider: str,
        email: str = "", email_verified: bool = False,
    ) -> ExternalIdentity:
        uid = require_user_id(user_id)
        issuer, subject, provider_uid = self._provider_uid(issuer, subject)
        provider = str(provider or "").strip()[:80]
        if not provider:
            raise AccountIdentityError("IDENTITY_PROVIDER_REQUIRED")
        path = self._path(provider_uid)
        now = time.time()

        def mutate(tx):
            if tx.get(("users", uid)) is None:
                raise AccountIdentityError("ACCOUNT_IDENTITY_USER_MISSING")
            existing = tx.get(path)
            if existing is not None:
                data = dict(existing.data)
                if str(data.get("user_id")) != uid:
                    raise AccountIdentityError("ACCOUNT_IDENTITY_ALREADY_LINKED")
                return data
            data = {
                "provider_uid": provider_uid,
                "issuer": issuer,
                "subject": subject,
                "user_id": uid,
                "provider": provider,
                "email": str(email or "").strip()[:320],
                "email_verified": bool(email_verified),
                "status": "active",
                "generation": 1,
                "created_at_epoch": now,
                "updated_at_epoch": now,
                "created_at": _iso_now(),
                "updated_at": _iso_now(),
            }
            tx.create(path, data)
            return data

        return self._identity(self.documents.run_transaction(mutate))

    def unlink_external_identity_from_user(self, user_id: str, issuer: str, subject: str) -> ExternalIdentity:
        uid = require_user_id(user_id)
        issuer, subject, provider_uid = self._provider_uid(issuer, subject)
        path = self._path(provider_uid)

        def mutate(tx):
            row = tx.get(path)
            if row is None or str(row.data.get("user_id")) != uid:
                raise AccountIdentityError("ACCOUNT_IDENTITY_NOT_FOUND")
            # Never orphan an account from its only verified identity.
            all_rows = tx.query_collection(("identity_bindings",), field="user_id", value=uid)
            if len(all_rows) <= 1:
                raise AccountIdentityError("ACCOUNT_IDENTITY_LAST_LINK_FORBIDDEN")
            data = dict(row.data)
            tx.delete(path)
            return data

        return self._identity(self.documents.run_transaction(mutate))

    def metadata(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "storage": "firestore_identity_registry",
            "identity_key": ["issuer", "subject"],
            "email_authoritative": False,
            "stores_credentials": False,
            "explicit_dual_proof_linking": True,
            "link_by_email": False,
            "existing_user_merge_on_link": False,
        }


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class HostedProjectContextProvider:
    def __init__(self, projects: FirestoreProjectRepository, users: HostedUserRepository, user_id_provider: Callable[[], str], *, kind: str = "account"):
        self.projects = projects
        self.users = users
        self.user_id_provider = user_id_provider
        self.kind = str(kind)
        self._current: ContextVar[ProjectRequestContext | None] = ContextVar("breadmaker_hosted_project_context", default=None)
        self.project_target_provider: Callable[[str], str | None] | None = None

    def current(self):
        return self._current.get()

    @asynccontextmanager
    async def bind_async(self, project_id: str | None = None, *, user_id: str | None = None, require_membership: bool = True):
        from anyio import to_thread
        def resolve():
            with self.bind(project_id,user_id=user_id,require_membership=require_membership) as context:
                return context
        context = await to_thread.run_sync(resolve)
        token = self._current.set(context)
        try:
            yield context
        finally:
            self._current.reset(token)

    @contextmanager
    def bind(self, project_id: str | None = None, *, user_id: str | None = None, require_membership: bool = True):
        uid = require_user_id(user_id or self.user_id_provider())
        if user_id and uid != require_user_id(self.user_id_provider()):
            raise AuthorizationError("HOSTED_IDENTITY_MISMATCH")
        pid = str(project_id or (self.project_target_provider(uid) if self.project_target_provider else "") or "")
        if not pid:
            if require_membership:
                raise AuthorizationError("PROJECT_MEMBERSHIP_REQUIRED")
            profile = self.users.get_self(uid)
            actor = Actor(uid, "", str(profile.get("display_name") or "Writer"), self.kind, "", ())
            context = ProjectRequestContext("", actor)
            token = self._current.set(context)
            try:
                yield context
            finally:
                self._current.reset(token)
            return
        try:
            project = self.projects.get_for_user(pid, uid)
            membership = self.projects.membership(pid, uid)
        except (ProjectUnavailable, DocumentNotFound) as exc:
            raise AuthorizationError("PROJECT_MEMBERSHIP_REQUIRED") from exc
        if require_membership and membership is None:
            raise AuthorizationError("PROJECT_MEMBERSHIP_REQUIRED")
        profile = self.users.get_self(uid)
        role = str((membership or {}).get("role") or "")
        actor = Actor(
            uid,
            str((membership or {}).get("member_id") or ""),
            str(profile.get("display_name") or "Writer"),
            self.kind,
            role,
            permissions_for_role(role),
        )
        context = ProjectRequestContext(str(project.get("project_id") or pid), actor)
        token = self._current.set(context)
        try:
            yield context
        finally:
            self._current.reset(token)


class HostedIdentityProvider:
    def __init__(self, documents, objects: ObjectStore, projects: FirestoreProjectRepository, context: HostedProjectContextProvider, user_id_provider: Callable[[], str], *, kind: str):
        self.documents = documents
        self.objects = objects
        self.state = HostedStructuredRepository(documents)
        self.users = HostedUserRepository(self.state)
        self.projects = projects
        self.context = context
        self.user_id_provider = user_id_provider
        self.kind = str(kind)

    def request_user_id(self, explicit_user_id: str | None = None) -> str:
        # Hosted identity is always derived from the server-selected developer identity
        # or the bound server session. ``explicit_user_id`` exists only for the shared
        # runtime port used by local mode; browser/request values are never authority in
        # hosted mode and therefore must not be compared, trusted, or used for lookup.
        return require_user_id(self.user_id_provider())

    def active_profile_id(self) -> str:
        return self.request_user_id()

    def current_profile(self, user_id: str | None = None) -> dict[str, Any]:
        uid = self.request_user_id(user_id)
        out = _clean_public(self.users.get_self(uid))
        out["kind"] = self.kind
        return out

    def profiles(self) -> list[dict[str, Any]]:
        return [self.current_profile()]

    def create_profile(self, display_name: str) -> dict[str, Any]:
        raise RuntimeError("HOSTED_PROFILE_CREATION_DISABLED")

    def activate_profile(self, user_id: str) -> None:
        self.request_user_id(user_id)

    def update_profile(self, display_name: str, user_id: str | None = None) -> dict[str, Any]:
        uid = self.request_user_id(user_id)
        current = self.users.get_self(uid)
        result = self.users.update_self(
            uid,
            {"display_name": str(display_name or "").strip()[:160] or "Writer"},
            expected_generation=int(current.get("generation") or 0),
            operation_id=operation_id("profile_update"),
        )
        out = _clean_public(result); out["kind"] = self.kind
        return out

    def avatar_bytes(self, user_id: str) -> bytes | None:
        uid = self.request_user_id(user_id)
        profile = self.users.get_self(uid)
        pointer = profile.get("avatar_object")
        if not isinstance(pointer, dict) or not pointer.get("object_name"):
            return None
        return b"".join(self.objects.read_chunks(
            str(pointer["object_name"]), max_bytes=_AVATAR_LIMIT,
            if_generation_match=int(pointer["object_generation"]),
        ))

    def save_avatar(self, payload: bytes, user_id: str | None = None) -> dict[str, Any]:
        uid = self.request_user_id(user_id)
        data = bytes(payload)
        try:
            info = inspect_hosted_image_bytes(data, ".webp", byte_limit=_AVATAR_LIMIT)
        except UnsafeImage as exc:
            raise ValueError("AVATAR_BYTES_UNSAFE") from exc
        if info.extension != ".webp":
            raise ValueError("HOSTED_AVATAR_WEBP_REQUIRED")
        digest = hashlib.sha256(data).hexdigest()
        revision = "avatar_" + digest[:24]
        publication = ImmutableObjectPublisher(self.objects).publish_bytes(
            user_avatar_key(uid, revision), data, max_bytes=_AVATAR_LIMIT,
            content_type="image/webp", metadata={"breadmaker-entity": "user_avatar"},
        )
        profile = self.users.get_self(uid)
        pointer = {
            "object_name": publication.object.name,
            "object_generation": publication.object.generation,
            "object_sha256": publication.object.sha256,
            "object_size": publication.object.size,
        }
        row = self.state.update(
            "hosted_user_profile",
            {"avatar_revision": revision, "avatar_object": pointer},
            expected_generation=int(profile.get("generation") or 0),
            operation_id=operation_id("avatar_save"), user_id=uid,
        )
        out = _clean_public(row.data); out["kind"] = self.kind
        return out

    def remove_avatar(self, user_id: str | None = None) -> dict[str, Any]:
        uid = self.request_user_id(user_id)
        profile = self.users.get_self(uid)
        row = self.state.update(
            "hosted_user_profile", {"avatar_revision": "", "avatar_object": None},
            expected_generation=int(profile.get("generation") or 0),
            operation_id=operation_id("avatar_remove"), user_id=uid,
        )
        out = _clean_public(row.data); out["kind"] = self.kind
        return out

    def current_actor(self, project_id: str | None = None, *, require_membership: bool = True, user_id: str | None = None) -> Actor:
        uid = self.request_user_id(user_id)
        current = self.context.current()
        if current is not None and (not project_id or current.project_id == str(project_id)):
            return current.actor
        if not project_id:
            if require_membership:
                target = self.context.project_target_provider(uid) if self.context.project_target_provider else None
                if not target:
                    raise AuthorizationError("PROJECT_MEMBERSHIP_REQUIRED")
                project_id = target
            else:
                profile = self.users.get_self(uid)
                return Actor(uid, "", str(profile.get("display_name") or "Writer"), self.kind, "", ())
        try:
            self.projects.get_for_user(str(project_id), uid)
        except ProjectUnavailable as exc:
            raise AuthorizationError("PROJECT_MEMBERSHIP_REQUIRED") from exc
        membership = self.projects.membership(str(project_id), uid)
        if require_membership and membership is None:
            raise AuthorizationError("PROJECT_MEMBERSHIP_REQUIRED")
        profile = self.users.get_self(uid)
        role = str((membership or {}).get("role") or "")
        return Actor(uid, str((membership or {}).get("member_id") or ""), str(profile.get("display_name") or "Writer"), self.kind, role, permissions_for_role(role))


class HostedAuthorizationService:
    def __init__(self, identity: HostedIdentityProvider):
        self.identity = identity

    def can(self, permission: str, project_id: str | None = None) -> bool:
        try:
            return str(permission) in self.identity.current_actor(project_id).permissions
        except Exception:
            return False

    def require(self, permission: str, project_id: str | None = None) -> Actor:
        actor = self.identity.current_actor(project_id)
        if str(permission) not in actor.permissions:
            raise AuthorizationError("PROJECT_PERMISSION_DENIED")
        return actor


class HostedCloudProjectRepository:
    def __init__(self, documents, objects: ObjectStore, authority: FirestoreProjectRepository,
                 users: HostedUserRepository, user_id_provider: Callable[[], str], *,
                 encrypted_objects=None, encrypted_fields=None):
        self.documents = documents
        self.objects = objects
        self.authority = authority
        self.users = users
        self.user_id_provider = user_id_provider
        self._private_mode = encrypted_fields is not None and encrypted_objects is not None
        self._member_state_codec = encrypted_fields if self._private_mode else None
        if self._private_mode:
            authority.configure_private_titles(encrypted_fields.envelope)
        # Explicit test/review-only injection. The hosted runtime never supplies
        # this until all data families, exports and recovery are encrypted.
        self.domain = HostedDomainStore(
            documents, objects, encrypted_objects=encrypted_objects,
            encrypted_fields=encrypted_fields,
        )

    def _uid(self, value: str | None = None) -> str:
        uid = require_user_id(value or self.user_id_provider())
        if value and uid != require_user_id(self.user_id_provider()):
            # Repository callers may explicitly address another user only in owner
            # membership methods; project catalog selection is self-scoped.
            return uid
        return uid

    def _owner(self, project_id: str) -> str | None:
        rows = self.documents.query_collection(("projects", require_project_id(project_id), "members"))
        for row in rows:
            if row.data.get("role") == "owner":
                return str(row.data.get("user_id") or "") or None
        return None

    def _view(self, row: Mapping[str, Any]) -> dict[str, Any]:
        pid = str(row.get("project_id") or "")
        return _project_public(row, owner_user_id=self._owner(pid) if pid else None)

    def init(self) -> None:
        return None

    def list(self, user_id: str | None = None) -> list[dict[str, Any]]:
        uid = self._uid(user_id)
        return [self._view(row) for row in self.authority.list_for_user(uid)]

    def active_id(self) -> str | None:
        return self.profile_project_target(self._uid())

    def active(self) -> dict[str, Any] | None:
        pid = self.active_id()
        return self.get(pid) if pid else None

    def get(self, project_id: str) -> dict[str, Any]:
        uid = self._uid()
        return self._view(self.authority.get_for_user(require_project_id(project_id), uid))

    def _update_last_project(self, uid: str, project_id: str | None) -> None:
        current = self.users.get_self(uid)
        self.users.update_self(
            uid, {"last_project_id": str(project_id or "")},
            expected_generation=int(current.get("generation") or 0),
            operation_id=operation_id("project_target"),
        )

    def _seed_project(self, project_id: str, uid: str) -> None:
        # Constant operation names are scoped by the immutable project path; a retry
        # from another replica can complete a partially seeded project, or revisit a
        # completed seed without advancing generation twice.
        tab = self.domain.create("document_tab", {"project_id": project_id}, {"title": "Manuscript", "kind": "manuscript", "position": 0}, user_id=uid, entity_id="tab_manuscript", operation="seed_manuscript_tab")
        chapter = self.domain.create("chapter", {"project_id": project_id}, {"title": "Chapter 1", "position": 0, "tab_id": tab["id"]}, user_id=uid, entity_id="chapter_1", operation="seed_first_chapter")
        self.domain.create(
            "manuscript_block", {"project_id": project_id},
            {"chapter_id": chapter["id"], "tab_id": tab["id"], "position": 0, "content_html": "", "text_content": ""},
            user_id=uid, entity_id="b_1", operation="seed_first_block",
        )

    def create(self, name: str, *, activate: bool = True, owner_user_id: str | None = None, idempotency_operation: str | None = None) -> dict[str, Any]:
        uid = self._uid(owner_user_id)
        op=idempotency_operation or operation_id("project_create")
        pid=stable_project_id(idempotency_operation) if idempotency_operation else None
        row = self.authority.create(str(name), owner_user_id=uid, operation_id=op, project_id=pid)
        pid = str(row["project_id"])
        try:
            self._seed_project(pid, uid)
        except Exception:
            # Never leave a newly failed project visible as healthy authority.
            latest = self.authority.get_for_user(pid, uid)
            try:self.authority.tombstone(pid, uid, expected_generation=int(latest.get("generation") or 0), operation_id=operation_id("seed_fail"))
            except Exception:pass
            raise
        if activate:
            self._update_last_project(uid, pid)
        return self._view(self.authority.get_for_user(pid, uid))

    def activate(self, project_id: str, *, user_id: str | None = None) -> dict[str, Any]:
        uid = self._uid(user_id)
        row = self.authority.get_for_user(project_id, uid)
        member_path = ("projects", require_project_id(project_id), "members", uid)
        stamp = _iso_now()
        def touch(tx):
            member = tx.get(member_path)
            if member is None:raise ProjectUnavailable("PROJECT_NOT_FOUND")
            data=dict(member.data);data["last_opened_at"]=stamp;data["updated_at"]=stamp;tx.set(member_path,data)
        self.documents.run_transaction(touch)
        self._update_last_project(uid, project_id)
        return self._view(row)

    def rename(self, project_id: str, name: str) -> dict[str, Any]:
        uid = self._uid()
        current = self.authority.get_for_user(project_id, uid)
        row = self.authority.update(project_id, uid, {"title": str(name)}, expected_generation=int(current.get("generation") or 0), operation_id=operation_id("project_rename"))
        return self._view(row)

    def delete(self, project_id: str, *, permanent: bool = False) -> Any:
        uid = self._uid()
        current = self.authority.get_for_user(project_id, uid)
        row = self.authority.tombstone(project_id, uid, expected_generation=int(current.get("generation") or 0), operation_id=operation_id("project_delete"))
        if self.profile_project_target(uid) == project_id:
            remaining = self.list(uid)
            self._update_last_project(uid, str(remaining[0]["id"]) if remaining else None)
        return self._view(row)

    def members(self, project_id: str) -> list[dict[str, Any]]:
        uid = self._uid(); self.authority.get_for_user(project_id, uid)
        rows=self.documents.query_collection(("projects",require_project_id(project_id),"members"))
        out=[]
        for row in rows:
            data=_clean_public(row.data)
            try:profile=self.users.get_self(str(data.get("user_id") or ""));data["display_name"]=str(profile.get("display_name") or "Writer")
            except Exception:data["display_name"]="Writer"
            out.append(data)
        return out

    def membership(self, project_id: str, user_id: str) -> dict[str, Any] | None:
        # Membership lookup itself is safe only for a member of the project.
        self.authority.get_for_user(project_id, self._uid())
        row=self.authority.membership(project_id, require_user_id(user_id))
        return _clean_public(row) if row else None

    def add_member(self, project_id: str, user_id: str, role: str) -> dict[str, Any]:
        actor=self._uid();current=self.authority.get_for_user(project_id,actor)
        row=self.authority.add_member(project_id,actor,require_user_id(user_id),role,expected_project_generation=int(current.get("generation") or 0),operation_id=operation_id("member_add"))
        return _clean_public(row)

    def remove_member(self, project_id: str, user_id: str) -> Any:
        actor=self._uid();current=self.authority.get_for_user(project_id,actor)
        return self.authority.remove_member(project_id,actor,require_user_id(user_id),expected_project_generation=int(current.get("generation") or 0))

    def profile_project_target(self, user_id: str) -> str | None:
        uid=require_user_id(user_id)
        profile=self.users.get_self(uid);target=str(profile.get("last_project_id") or "")
        if target:
            try:self.authority.get_for_user(target,uid);return target
            except ProjectUnavailable:pass
        rows=self.authority.list_for_user(uid)
        return str(rows[0].get("project_id") or "") if rows else None

    def _member_state_path(self, project_id: str, uid: str, *, key: str | None = None) -> tuple[str, ...]:
        pid=require_project_id(project_id);user=require_user_id(uid)
        if key is None:return ("projects",pid,"members",user,"state","bookmark")
        return ("projects",pid,"members",user,"ui_state",require_opaque_id(key,label="key"))

    def _member_state_model(self, key: str | None) -> str:
        return 'member_bookmark' if key is None else 'member_ui_state'

    def _member_state_ids(self, project_id: str, uid: str, key: str | None) -> dict[str, str]:
        ids = {'project_id': project_id, 'user_id': uid}
        if key is not None:
            ids['key'] = require_opaque_id(key, label='key')
        return ids

    def member_state(self, project_id: str, *, key: str | None = None, user_id: str | None = None) -> dict[str, Any] | None:
        uid = self._uid(user_id); pid = require_project_id(project_id)
        # A supplied user_id is not a privilege escalation: private member
        # state is always scoped to the authenticated caller, never a project
        # owner's view of another participant's private preferences.
        if self._private_mode and uid != self._uid():
            raise HostedAuthorizationError('HOSTED_PRIVATE_MEMBER_STATE_OTHER_USER_DENIED')
        self.authority.get_for_user(pid, uid)
        path = self._member_state_path(pid, uid, key=key)
        row = self.documents.get(path)
        if row is None:
            return None
        if self._private_mode:
            data = self._member_state_codec.open(self._member_state_model(key),
                self._member_state_ids(pid, uid, key), row.data)
            return _clean_public(data)
        return _clean_public(row.data)

    def member_states(self, project_id: str, keys: list[str], *, user_id: str | None = None) -> dict[str, Any]:
        """One authorized snapshot/BatchGet, never one authority read per key."""
        uid=self._uid(user_id);pid=require_project_id(project_id)
        if self._private_mode and uid!=self._uid():
            raise HostedAuthorizationError('HOSTED_PRIVATE_MEMBER_STATE_OTHER_USER_DENIED')
        unique=list(dict.fromkeys(require_opaque_id(key,label='key') for key in keys))
        if len(unique)>64:raise HostedValidationError('HOSTED_MEMBER_STATE_BATCH_TOO_LARGE')
        def read(tx):
            self.domain.entities._authorize(tx,self.domain._model('member_ui_state'),
                {'project_id':pid,'user_id':uid,'key':unique[0] if unique else 'empty'},uid,mutation=False)
            paths=[self._member_state_path(pid,uid,key=key) for key in unique]
            if not paths:return []
            getter=getattr(tx,'get_many',None)
            return getter(paths) if getter is not None else [tx.get(path) for path in paths]
        rows=self.documents.run_transaction(read)
        values={}
        for key,row in zip(unique,rows):
            if row is None:values[key]=None;continue
            data=(self._member_state_codec.open(self._member_state_model(key),self._member_state_ids(pid,uid,key),row.data)
                if self._private_mode else row.data)
            public=_clean_public(data);values[key]=public.get('value',public)
        return values

    def put_member_state(self, project_id: str, payload: Mapping[str, Any], *, key: str | None = None, user_id: str | None = None) -> dict[str, Any]:
        uid = self._uid(user_id); pid = require_project_id(project_id)
        # A supplied user_id is not a privilege escalation: private member
        # state is always scoped to the authenticated caller, never a project
        # owner's view of another participant's private preferences.
        if self._private_mode and uid != self._uid():
            raise HostedAuthorizationError('HOSTED_PRIVATE_MEMBER_STATE_OTHER_USER_DENIED')
        self.authority.get_for_user(pid, uid)
        path = self._member_state_path(pid, uid, key=key)
        stamp = _iso_now()
        # Managed/crypto fields are never supplied by callers. In private mode,
        # reject rather than quietly accepting an attempt to smuggle authority.
        if not isinstance(payload, Mapping):
            raise HostedValidationError('HOSTED_MEMBER_STATE_PAYLOAD_INVALID')
        if self._private_mode and any(str(k).startswith('_bm_') for k in payload):
            raise HostedValidationError('HOSTED_PRIVATE_MEMBER_STATE_RESERVED_FIELD')
        clean = {str(k): v for k, v in dict(payload).items()
                 if not str(k).startswith('_bm_') and str(k) not in {'generation', 'created_at', 'updated_at'}}
        model = self._member_state_model(key)
        ids = self._member_state_ids(pid, uid, key)
        identity = uid if key is None else ids['key']
        if self._private_mode:
            # KMS encryption can perform external I/O. NEVER run it inside a
            # Firestore transaction callback (Firestore may replay callbacks).
            # Prepare the sealed update outside the transaction and use the
            # provider's monotonic document revision for an in-transaction CAS.
            for attempt in range(3):
                old = self.documents.get(path)
                previous = self._member_state_codec.open(model, ids, old.data) if old else {}
                content = {k: v for k, v in previous.items()
                           if k not in {'id', 'user_id', 'key', 'generation', 'created_at', 'updated_at'}}
                content.update(clean)
                sealed = self._member_state_codec.seal(model, ids, {**content, 'id': identity})
                data = {**sealed,
                        'generation': int(old.data.get('generation') or 0) + 1 if old else 1,
                        'created_at': old.data.get('created_at', stamp) if old else stamp,
                        'updated_at': stamp}
                expected_revision = old.revision if old is not None else None

                def private_commit(tx):
                    # Recheck current project status and membership in the CAS;
                    # a revoked/deleted project cannot accept an in-flight save.
                    self.authority._require_member(tx, pid, uid)
                    latest = tx.get(path)
                    if (latest.revision if latest is not None else None) != expected_revision:
                        raise DocumentConflict('HOSTED_PRIVATE_MEMBER_STATE_CONFLICT')
                    if latest is None:
                        tx.create(path, data)
                    else:
                        tx.set(path, data)
                    return data
                try:
                    committed = self.documents.run_transaction(private_commit)
                    return _clean_public(self._member_state_codec.open(model, ids, committed))
                except DocumentConflict:
                    if attempt == 2:
                        raise
            raise DocumentConflict('HOSTED_PRIVATE_MEMBER_STATE_CONFLICT')

        def mutate(tx):
            if tx.get(('projects', pid, 'members', uid)) is None:
                raise ProjectUnavailable('PROJECT_NOT_FOUND')
            row = tx.get(path)
            data = dict(row.data) if row else {'generation': 0, 'created_at': stamp}
            data.update(clean)
            data['generation'] = int(data.get('generation') or 0) + 1
            data['updated_at'] = stamp
            if row is None: tx.create(path, data)
            else: tx.set(path, data)
            return data
        return _clean_public(self.documents.run_transaction(mutate))

    def delete_member_state(self, project_id: str, *, key: str | None = None, user_id: str | None = None) -> None:
        uid = self._uid(user_id); pid = require_project_id(project_id)
        # A supplied user_id is not a privilege escalation: private member
        # state is always scoped to the authenticated caller, never a project
        # owner's view of another participant's private preferences.
        if self._private_mode and uid != self._uid():
            raise HostedAuthorizationError('HOSTED_PRIVATE_MEMBER_STATE_OTHER_USER_DENIED')
        self.authority.get_for_user(pid, uid)
        path = self._member_state_path(pid, uid, key=key)
        def mutate(tx):
            if tx.get(('projects', pid, 'members', uid)) is None:
                raise ProjectUnavailable('PROJECT_NOT_FOUND')
            tx.delete(path)
        self.documents.run_transaction(mutate)

    def recovery_candidates(self) -> list[dict[str, Any]]:
        return []

    def list_trash(self, user_id: str | None = None) -> list[dict[str, Any]]:
        uid=self._uid(user_id)
        memberships=self.documents.query_collection_group("members",field="user_id",value=uid)
        out=[]
        for membership in memberships:
            if len(membership.path)<4:continue
            pid=str(membership.path[-3])
            project=self.documents.get(("projects",pid))
            if project is not None and str(project.data.get("status") or "") == "deleted":
                out.append(self._view(self.authority._open_project(pid, project.data)))
        return sorted(out,key=lambda row:str(row.get("deleted_at") or ""),reverse=True)

    def restore_from_trash(self, trash_id: str, *, user_id: str | None = None) -> dict[str, Any]:
        uid=self._uid(user_id);pid=require_project_id(trash_id);project_path=("projects",pid);member_path=("projects",pid,"members",uid);stamp=_iso_now()
        def mutate(tx):
            project=tx.get(project_path);member=tx.get(member_path)
            if project is None or member is None or member.data.get("role")!="owner" or project.data.get("status")!="deleted":
                raise ProjectUnavailable("PROJECT_NOT_FOUND")
            data=dict(project.data);data["status"]="active";data.pop("deleted_at",None);data["generation"]=int(data.get("generation") or 0)+1;data["updated_at"]=stamp;tx.set(project_path,data);return data
        row=self.documents.run_transaction(mutate);self._update_last_project(uid,pid);return self._view(self.authority._open_project(pid, row))


class HostedCommittedAssetStore:
    """AssetStore whose committed authority is Firestore + immutable GCS.

    ``HostedAssetStore`` validates and publishes object bytes.  This M5 wrapper then
    publishes the project-scoped Firestore metadata through ``HostedDomainStore``; a
    blob without that record is an orphan candidate rather than a committed asset.
    """
    def __init__(self, documents, objects: ObjectStore, user_id_provider: Callable[[], str], project_id_provider: Callable[[], str],
                 *, encrypted_fields=None, encrypted_objects=None):
        self.documents=documents
        self.objects=objects
        self.user_id_provider=user_id_provider
        self.project_id_provider=project_id_provider
        if (encrypted_fields is None) != (encrypted_objects is None):
            raise ValueError('HOSTED_PRIVATE_ASSET_BOTH_CODECS_REQUIRED')
        self.private = encrypted_fields is not None
        self.domain=HostedDomainStore(documents,objects,
                                      encrypted_fields=encrypted_fields,
                                      encrypted_objects=encrypted_objects)
        self.raw=HostedAssetStore(objects,project_id=project_id_provider)

    @staticmethod
    def _name(value: str) -> str:
        text=str(value or "").strip()
        if text.startswith("/api/assets/"):
            text=text.removeprefix("/api/assets/")
        return text

    def _record(self, name_or_api_path: str) -> dict[str, Any]:
        pid=require_project_id(self.project_id_provider())
        uid=require_user_id(self.user_id_provider())
        name=self._name(name_or_api_path)
        rows=self.domain.list("project_asset",{"project_id":pid},user_id=uid,field="name",value=name)
        if not rows:
            raise FileNotFoundError(name)
        if len(rows)!=1:
            raise RuntimeError("PROJECT_ASSET_METADATA_COLLISION")
        return rows[0]

    def exists(self, name_or_api_path: str) -> bool:
        try:
            self._record(name_or_api_path)
            return True
        except FileNotFoundError:
            return False

    def read_blob(self, name_or_api_path: str) -> bytes:
        row=self._record(name_or_api_path)
        if self.private:
            pid=require_project_id(self.project_id_provider())
            uid=require_user_id(self.user_id_provider())
            name=self._name(name_or_api_path)
            # The filename is an independently random stable URL, not a digest
            # of a confidential image. The authoritative mapping is SEALED.
            if (row.get('name') != name or row.get('api_path') != '/api/assets/' + name
                    or row.get('encryption_format') != _PRIVATE_ENVELOPE_FORMAT):
                raise ValueError('HOSTED_PRIVATE_ASSET_METADATA_INVALID')
            aid=require_opaque_id(row.get('asset_id'),label='asset_id')
            ref=EncryptedObjectReference(str(row['object_name']),int(row['object_generation']),
                                          str(row['object_sha256']),int(row['object_size']))
            body=self.domain.encrypted_objects.read(
                project_id=pid,user_id=uid,entity='project_asset',record_id=aid,
                operation_id=require_opaque_id(row['encryption_operation'],label='encryption_operation'),
                reference=ref)
            if hashlib.sha256(body).hexdigest() != row.get('sha256'):
                raise ValueError('HOSTED_PRIVATE_ASSET_HASH_MISMATCH')
            extension='.'+name.rsplit('.',1)[-1].lower()
            try:
                inspect_hosted_image_bytes(body, extension, byte_limit=HOSTED_DEFAULT_IMAGE_BYTES)
            except UnsafeImage as exc:
                raise ValueError('ASSET_BYTES_UNSAFE') from exc
            return body
        object_name=str(row.get("object_name") or "")
        generation=int(row.get("object_generation") or -1)
        expected_sha=str(row.get("sha256") or "").lower()
        expected_size=int(row.get("object_size") or -1)
        meta=self.objects.stat(object_name)
        if meta is None or int(meta.generation)!=generation:
            raise FileNotFoundError(str(row.get("name") or name_or_api_path))
        if str(meta.sha256)!=expected_sha or int(meta.size)!=expected_size:
            raise ValueError("ASSET_POINTER_INTEGRITY_MISMATCH")
        payload=b"".join(self.objects.read_chunks(
            object_name,max_bytes=max(expected_size,1),if_generation_match=generation,
        ))
        digest=hashlib.sha256(payload).hexdigest()
        if digest!=expected_sha or len(payload)!=expected_size:
            raise ValueError("ASSET_CONTENT_IDENTITY_MISMATCH")
        ext='.'+str(row.get("name") or '').rsplit('.',1)[1].lower() if '.' in str(row.get("name") or '') else ''
        try:
            inspect_hosted_image_bytes(payload,ext,byte_limit=max(expected_size,1))
        except UnsafeImage as exc:
            raise ValueError("ASSET_BYTES_UNSAFE") from exc
        return payload

    def put_blob(self, blob: bytes, ext: str) -> StoredAsset:
        if self.private:
            payload=bytes(blob)
            try:
                info=inspect_hosted_image_bytes(payload, ext, byte_limit=HOSTED_DEFAULT_IMAGE_BYTES)
            except UnsafeImage as exc:
                raise ValueError('ASSET_BYTES_UNSAFE') from exc
            pid=require_project_id(self.project_id_provider())
            uid=require_user_id(self.user_id_provider())
            self.domain.projects.get_for_user(pid,uid)
            asset_id='asset_'+secrets.token_hex(16)
            # Retain img_<24 hex>.<ext> as the existing browser-facing format
            # while removing the deterministic plaintext-hash filename leak.
            name='img_'+secrets.token_hex(12)+info.extension
            object_operation=operation_id('private_asset')
            ref=self.domain.encrypted_objects.publish(
                project_id=pid,user_id=uid,entity='project_asset',record_id=asset_id,
                operation_id=object_operation,plaintext=payload)
            digest=hashlib.sha256(payload).hexdigest()
            self.domain.create('project_asset',{'project_id':pid},
                {'api_path':'/api/assets/'+name,'name':name,'sha256':digest,
                 'object_name':ref.name,'object_generation':ref.generation,
                 'object_sha256':ref.ciphertext_sha256,'object_size':ref.ciphertext_size,
                 'encryption_format':_PRIVATE_ENVELOPE_FORMAT,
                 'encryption_operation':object_operation,'media_type':info.media_type},
                user_id=uid,entity_id=asset_id,operation=operation_id('asset_record'))
            return StoredAsset('/api/assets/'+name,name,digest,True,
                               object_name=ref.name,object_generation=ref.generation)
        # Non-private adapter must reject private-project roots BEFORE the
        # legacy plaintext GCS publisher can create an immutable orphan.
        self.domain.projects.get_for_user(
            require_project_id(self.project_id_provider()),
            require_user_id(self.user_id_provider()))
        saved=self.raw.put_blob(blob,ext)
        if not saved.object_name or saved.object_generation is None:
            raise RuntimeError("HOSTED_ASSET_PUBLICATION_INCOMPLETE")
        meta=self.objects.stat(saved.object_name)
        if meta is None or int(meta.generation)!=int(saved.object_generation):
            raise RuntimeError("HOSTED_ASSET_PUBLICATION_INCOMPLETE")
        self.domain.register_project_asset(
            project_id=require_project_id(self.project_id_provider()),
            user_id=require_user_id(self.user_id_provider()),
            api_path=saved.api_path,
            name=saved.name,
            digest=saved.digest,
            object_name=meta.name,
            object_generation=meta.generation,
            object_size=meta.size,
        )
        return saved

    def discard_uncommitted(self, asset: StoredAsset) -> None:
        # Object-first publication intentionally leaves verified immutable bytes as an
        # orphan if the Firestore commit fails. Retention-aware GC owns deletion.
        return None


class HostedHistoryStore:
    def __init__(self, documents, objects: ObjectStore, user_id_provider: Callable[[], str], project_id_provider: Callable[[], str],
                 *, encrypted_fields=None, encrypted_objects=None):
        self.documents=documents;self.objects=objects;self.user_id_provider=user_id_provider;self.project_id_provider=project_id_provider
        if (encrypted_fields is None) != (encrypted_objects is None):
            raise ValueError('HOSTED_PRIVATE_HISTORY_BOTH_CODECS_REQUIRED')
        # Legacy constructor/path remains byte-compatible at the API boundary.
        # Only an explicit paired injection selects the independently authorized
        # domain-backed encrypted revision path; runtime.hosted never injects it.
        self.private = encrypted_fields is not None
        self.domain = (HostedDomainStore(documents, objects,
                       encrypted_fields=encrypted_fields,
                       encrypted_objects=encrypted_objects) if self.private else None)
        self.publisher=HostedHybridObjectPublisher(objects,documents)
        self.projects=self.domain.projects if self.private else FirestoreProjectRepository(documents)

    @staticmethod
    def _private_view(row: Mapping[str, Any]) -> dict[str, Any]:
        # Never expose the compressed/base64 revision body from listing APIs.
        return {k: v for k, v in _clean_public(row).items() if k != 'envelope_b64'}

    def list_revisions(self) -> list[dict[str, Any]]:
        uid=require_user_id(self.user_id_provider());pid=require_project_id(self.project_id_provider());self.projects.get_for_user(pid,uid)
        if self.private:
            rows=self.domain.list('hosted_revision_history', {'project_id':pid}, user_id=uid,
                                  order_by=('project_generation','created_at','id'))
            return [self._private_view(row) for row in sorted(rows,
                    key=lambda r:int(r.get('project_generation') or 0),reverse=True)]
        rows=self.documents.query_collection(("projects",pid,"revisions"));return [_clean_public(row.data) for row in sorted(rows,key=lambda r:int(r.data.get("project_generation") or 0),reverse=True)]

    def create_revision(self, envelope: bytes, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        uid=require_user_id(self.user_id_provider());pid=require_project_id(self.project_id_provider());project=self.projects.get_for_user(pid,uid);rid="revision_"+secrets.token_hex(12)
        if self.private:
            body=bytes(envelope)
            if len(body)>_REVISION_LIMIT:
                raise ValueError('HOSTED_PRIVATE_HISTORY_TOO_LARGE')
            stored=self.domain.create('hosted_revision_history', {'project_id':pid},
                {**dict(metadata or {}), 'project_generation':int(project['generation']),
                 'envelope_b64':base64.b64encode(body).decode('ascii')},
                user_id=uid,entity_id=rid,operation=operation_id('revision'))
            return self._private_view(stored)
        return _clean_public(self.publisher.publish_revision(project_id=pid,user_id=uid,revision_id=rid,expected_project_generation=int(project.get("generation") or 0),envelope=bytes(envelope),revision_metadata=dict(metadata or {}),operation_id=operation_id("revision"),max_bytes=_REVISION_LIMIT))

    def read_revision(self, revision_id: str) -> tuple[dict[str, Any], bytes]:
        uid=require_user_id(self.user_id_provider());pid=require_project_id(self.project_id_provider());self.projects.get_for_user(pid,uid)
        if self.private:
            row=self.domain.get('hosted_revision_history',
                                {'project_id':pid,'revision_id':require_opaque_id(revision_id,label='revision_id')},user_id=uid)
            try:
                body=base64.b64decode(row['envelope_b64'], validate=True)
            except Exception:
                raise RuntimeError('HOSTED_PRIVATE_HISTORY_BYTES_INVALID') from None
            if len(body)>_REVISION_LIMIT:
                raise RuntimeError('HOSTED_PRIVATE_HISTORY_TOO_LARGE')
            return self._private_view(row),body
        rid=str(revision_id or '').strip();row=self.documents.get(("projects",pid,"revisions",rid))
        if row is None:raise DocumentNotFound("HOSTED_REVISION_NOT_FOUND")
        data=dict(row.data);name=str(data.get("object_name") or '');generation=int(data.get("object_generation") or 0);expected_size=int(data.get("object_size") or -1);expected_sha=str(data.get("object_sha256") or '')
        stat=self.objects.stat(name) if name else None
        if stat is None or stat.generation!=generation or stat.size!=expected_size or stat.sha256!=expected_sha:
            raise RuntimeError("HOSTED_REVISION_POINTER_INVALID")
        digest=hashlib.sha256();chunks=[];size=0
        for chunk in self.objects.read_chunks(name,max_bytes=_REVISION_LIMIT,if_generation_match=generation):
            data_chunk=bytes(chunk);size+=len(data_chunk);digest.update(data_chunk);chunks.append(data_chunk)
        if size!=expected_size or digest.hexdigest()!=expected_sha:raise RuntimeError("HOSTED_REVISION_INTEGRITY_MISMATCH")
        return _clean_public(data),b''.join(chunks)

    def sync_remote(self, project_id: str, revision_id: str | None = None) -> Any:
        return {"ok":True,"project_id":str(project_id),"revision_id":revision_id,"remote":"authoritative"}
    def retention_settings(self) -> dict[str, Any]:return {"policy":"hosted-managed","editable":False}
    def update_retention(self, value: str) -> dict[str, Any]:raise RuntimeError("HOSTED_RETENTION_MANAGED")
    def run_maintenance(self, project_id: str) -> Any:return {"ok":True,"project_id":str(project_id),"scheduled":False}
    def repair_local_from_destination(self, destination_id: str, *, project_id: str, revision_id: str | None = None) -> Any:raise RuntimeError("HOSTED_LOCAL_REPAIR_UNAVAILABLE")
    @contextmanager
    def remote_privacy_barrier(self):yield


class HostedBackupStore:
    """M5 deliberately has no local/remote backup destination compatibility layer.

    Hosted durable history is already cloud-authoritative. Destination backups are a
    later hosted product milestone and must not reintroduce server filesystem state.
    """
    @contextmanager
    def privacy_barrier(self):yield
    def _unavailable(self,*_args,**_kwargs):raise RuntimeError("HOSTED_BACKUP_UNAVAILABLE_UNTIL_M9")
    erase_known_managed_data=_unavailable;catch_up=_unavailable;destinations=_unavailable;add_destination=_unavailable;update_destination=_unavailable;remove_destination=_unavailable;destination_backups=_unavailable;discover_destination_backups=_unavailable;google_oauth_config=_unavailable;begin_google_oauth=_unavailable;complete_google_oauth=_unavailable;fail_google_oauth=_unavailable;google_oauth_status=_unavailable;add_webdav_destination=_unavailable;reconnect_webdav_destination=_unavailable


class HostedProjectUsageService:
    def __init__(self, documents, objects: ObjectStore):self.documents=documents;self.objects=objects
    def _owned_projects(self, owner_user_id: str) -> list[str]:
        uid=require_user_id(owner_user_id);rows=self.documents.query_collection_group("members",field="user_id",value=uid);out=[]
        for row in rows:
            if row.data.get("role")!="owner" or len(row.path)<4:continue
            pid=str(row.path[-3]);project=self.documents.get(("projects",pid))
            if project is not None and str(project.data.get("status") or "active")=="active":out.append(pid)
        return out
    def owner_project_count(self, owner_user_id: str) -> int:return len(self._owned_projects(owner_user_id))
    def project_storage_bytes(self, project_id: str, *, stop_after: int | None = None) -> int:
        pid=require_project_id(project_id);total=0
        for row in self.objects.list_prefix(f"projects/{pid}/"):
            total+=int(row.size)
            if stop_after is not None and total>int(stop_after):break
        return total
    def owner_storage_bytes(self, owner_user_id: str, *, stop_after: int | None = None) -> int:
        total=0
        for pid in self._owned_projects(owner_user_id):
            remaining=None if stop_after is None else max(0,int(stop_after)-total)
            total+=self.project_storage_bytes(pid,stop_after=remaining)
            if stop_after is not None and total>int(stop_after):break
        return total


class HostedCapabilityProvider:
    def __init__(self, mode: str, auth_mode: str, capabilities: Mapping[str, bool], metadata: Mapping[str, Any]):
        self.mode=str(mode);self.auth_mode=str(auth_mode);self._capabilities=dict(capabilities);self._metadata=dict(metadata)
    def public(self)->dict[str,bool]:return dict(self._capabilities)
    def metadata(self)->dict[str,Any]:return dict(self._metadata)
    def supports(self,capability:str)->bool:return bool(self._capabilities.get(str(capability),False))


def ensure_hosted_user(documents, *, user_id: str, display_name: str) -> dict[str, Any]:
    users=HostedUserRepository(HostedStructuredRepository(documents));uid=require_user_id(user_id)
    try:return users.get_self(uid)
    except DocumentNotFound:return users.create(display_name=display_name,operation_id="hosted_bootstrap_user",user_id=uid)


__all__ = [
    "HostedCloudAccountIdentityService","HostedProjectContextProvider","HostedIdentityProvider",
    "HostedAuthorizationService","HostedCloudProjectRepository","HostedCommittedAssetStore","HostedHistoryStore",
    "HostedBackupStore","HostedProjectUsageService","HostedCapabilityProvider","ensure_hosted_user",
    "NullAccountIdentityService","_DEV_USER_ID",
]
