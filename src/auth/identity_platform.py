from __future__ import annotations

"""Hosted Google Identity Platform server-session boundary.

This module is safe to import in local/test processes: Firebase Admin is imported only
by ``build_firebase_admin_auth`` when Identity Platform is explicitly selected.
Breadmaker persists neither provider tokens nor credentials. External Identity Platform
UIDs are mapped to Breadmaker's stable internal ``usr_`` ids through the hosted account
identity store; email is metadata only and is never account authority.
"""

import hashlib
import secrets
import time
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, Callable, Mapping

from auth.accounts import AccountIdentityError, AccountIdentityService
from auth.sessions import AuthenticationError, SessionPrincipal


class IdentityPlatformError(AuthenticationError):
    """Stable non-secret Identity Platform/session failure."""


def _enrolled_factors(user: Any) -> list[Any]:
    multi = getattr(user, "multi_factor", None)
    factors = getattr(multi, "enrolled_factors", None) if multi is not None else None
    if factors is not None:
        return list(factors)
    # The pinned Python Admin SDK retains server-returned MFA metadata in
    # UserRecord._data but does not expose the multi_factor property. Read only
    # this trusted Admin lookup, never client claims or factor secrets.
    raw = getattr(user, "_data", None)
    entries = raw.get("mfaInfo") if isinstance(raw, Mapping) else None
    if not isinstance(entries, list):
        return []
    return [SimpleNamespace(
        factor_id="totp", uid=item["mfaEnrollmentId"],
        display_name=item.get("displayName", ""), enrollment_time=item.get("enrolledAt"),
    ) for item in entries if isinstance(item, Mapping)
        and isinstance(item.get("totpInfo"), Mapping)
        and isinstance(item.get("mfaEnrollmentId"), str) and item["mfaEnrollmentId"].strip()]


@dataclass(frozen=True)
class IdentityPlatformExchange:
    cookie: str
    principal: SessionPrincipal
    provider: str
    email_verified: bool


def _required(value: Any, code: str, *, maximum: int = 2048) -> str:
    result = str(value or "").strip()
    if not result or len(result) > maximum:
        raise IdentityPlatformError(code)
    return result


def _provider(claims: Mapping[str, Any]) -> str:
    firebase = claims.get("firebase")
    raw = str((firebase if isinstance(firebase, Mapping) else {}).get("sign_in_provider") or "").strip().lower()
    return {
        "google.com": "google",
        "apple.com": "apple",
        "password": "email_password",
    }.get(raw, raw or "identity_platform")[:80]


def _display_name(claims: Mapping[str, Any]) -> str:
    name = str(claims.get("name") or "").strip()[:160]
    if name:
        return name
    email = str(claims.get("email") or "").strip()
    if "@" in email:
        candidate = email.split("@", 1)[0].strip()[:160]
        if candidate:
            return candidate
    return "Writer"


class IdentityPlatformAdmissionStore:
    """Firestore-backed admission with operator grants or one-time invitations.

    Operators create ``identity_platform_invites/<sha256(token)>`` documents out of
    band. Only the SHA-256 digest is persisted. The first successful exchange consumes
    the invite and creates a durable admission keyed by the external UID digest in the
    same transaction. Existing admitted UIDs never need the invitation again.
    """

    def __init__(self, documents, *, now: Callable[[], float] = time.time):
        self.documents = documents
        self.now = now

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(str(value).encode("utf-8")).hexdigest()

    @classmethod
    def invitation_path(cls, token: str) -> tuple[str, str]:
        value = _required(token, "PRIVATE_ALPHA_INVITATION_REQUIRED", maximum=512)
        return ("identity_platform_invites", cls._digest(value))

    @classmethod
    def admission_path(cls, external_uid: str) -> tuple[str, str]:
        uid = _required(external_uid, "IDENTITY_UID_REQUIRED", maximum=256)
        return ("identity_platform_admissions", cls._digest(uid))

    @staticmethod
    def normalize_greenlist_email(email: str) -> str:
        value = str(email or "").strip().lower()
        if len(value) > 320 or value.count('@') != 1 or any(c.isspace() or ord(c) < 32 for c in value):
            return ""
        local, domain = value.split('@')
        return value if local and domain else ""

    @classmethod
    def greenlist_path(cls, email: str) -> tuple[str, str]:
        value = cls.normalize_greenlist_email(email)
        if not value:
            raise IdentityPlatformError("PRIVATE_ALPHA_ADMISSION_REQUIRED")
        return ("identity_platform_greenlist", cls._digest(value))

    def _active_greenlist(self, store, digest: str, uid: str) -> bool:
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            return False
        grant = store.get(("identity_platform_greenlist", digest))
        if grant is None:
            return False
        data = grant.data
        email = self.normalize_greenlist_email(data.get("email"))
        try:
            expires = float(data.get("expires_at_epoch") or 0.0)
        except (TypeError, ValueError):
            return False
        return bool(email and self._digest(email) == digest and data.get("status") == "active"
                    and data.get("bound_uid") == uid and (expires == 0 or self.now() < expires))

    def is_admitted(self, external_uid: str) -> bool:
        uid = _required(external_uid, "IDENTITY_UID_REQUIRED", maximum=256)
        row = self.documents.get(self.admission_path(uid))
        if row is None or row.data.get("external_uid") != uid or row.data.get("status") != "active":
            return False
        digest = row.data.get("greenlist_digest")
        return self._active_greenlist(self.documents, digest, uid) if digest else True

    def require_or_consume(self, external_uid: str, invitation_token: str | None, *,
                           email: str = "", email_verified: bool = False, provider: str = "") -> None:
        uid = _required(external_uid, "IDENTITY_UID_REQUIRED", maximum=256)
        admission_path = self.admission_path(uid)
        token = str(invitation_token or "").strip()
        invite_path = self.invitation_path(token) if token else None
        now = float(self.now())

        def mutate(tx):
            admitted = tx.get(admission_path)
            if admitted is not None:
                data = dict(admitted.data)
                if str(data.get("external_uid") or "") != uid:
                    raise IdentityPlatformError("PRIVATE_ALPHA_ADMISSION_HASH_COLLISION")
                if str(data.get("status") or "") != "active":
                    raise IdentityPlatformError("PRIVATE_ALPHA_ADMISSION_DISABLED")
                if data.get("greenlist_digest") and not self._active_greenlist(tx, data["greenlist_digest"], uid):
                    raise IdentityPlatformError("PRIVATE_ALPHA_ADMISSION_DISABLED")
                return None
            if invite_path is None:
                # Only verified server-token claims reach this path. The email is
                # a one-time operator grant, NEVER an account lookup/merge key.
                normalized = self.normalize_greenlist_email(email)
                if email_verified is not True or not normalized or provider not in {"google", "email_password"}:
                    raise IdentityPlatformError("PRIVATE_ALPHA_ADMISSION_REQUIRED")
                path = self.greenlist_path(normalized)
                grant = tx.get(path)
                entry = dict(grant.data) if grant is not None else {}
                try:
                    expires = float(entry.get("expires_at_epoch") or 0.0)
                except (TypeError, ValueError):
                    raise IdentityPlatformError("PRIVATE_ALPHA_ADMISSION_REQUIRED") from None
                allowed = entry.get("allowed_providers")
                if (entry.get("status") != "active" or entry.get("email") != normalized
                        or not isinstance(allowed, list) or provider not in allowed
                        or entry.get("bound_uid") not in (None, "", uid)
                        or not (expires == 0 or now < expires)):
                    raise IdentityPlatformError("PRIVATE_ALPHA_ADMISSION_REQUIRED")
                entry.update({"bound_uid": uid, "admitted_at_epoch": now})
                tx.set(path, entry)
                tx.create(admission_path, {
                    "external_uid": uid, "status": "active", "admitted_at_epoch": now,
                    "greenlist_digest": path[-1],
                })
                return None
            invitation = tx.get(invite_path)
            if invitation is None:
                raise IdentityPlatformError("PRIVATE_ALPHA_INVITATION_INVALID")
            data = dict(invitation.data)
            if str(data.get("status") or "active") != "active" or data.get("used_by_uid"):
                raise IdentityPlatformError("PRIVATE_ALPHA_INVITATION_INVALID")
            expires = float(data.get("expires_at_epoch") or 0.0)
            if expires and now >= expires:
                raise IdentityPlatformError("PRIVATE_ALPHA_INVITATION_EXPIRED")
            data.update({"status": "consumed", "used_by_uid": uid, "used_at_epoch": now})
            tx.set(invite_path, data)
            tx.create(admission_path, {
                "external_uid": uid,
                "status": "active",
                "admitted_at_epoch": now,
                "invitation_digest": invite_path[-1],
            })
            return None

        self.documents.run_transaction(mutate)


class _FirebaseAdminAuthAdapter:
    def __init__(self, auth_module: Any, app: Any):
        self._auth = auth_module
        self._app = app

    def verify_id_token(self, token: str, *, check_revoked: bool = True):
        return self._auth.verify_id_token(token, app=self._app, check_revoked=check_revoked)

    def create_session_cookie(self, token: str, *, expires_in: timedelta):
        return self._auth.create_session_cookie(token, expires_in=expires_in, app=self._app)

    def verify_session_cookie(self, cookie: str, *, check_revoked: bool = True):
        return self._auth.verify_session_cookie(cookie, app=self._app, check_revoked=check_revoked)

    def revoke_refresh_tokens(self, uid: str) -> None:
        self._auth.revoke_refresh_tokens(uid, app=self._app)

    def get_user(self, uid: str):
        return self._auth.get_user(uid, app=self._app)


def build_firebase_admin_auth(*, project_id: str | None = None):
    """Build the Firebase Admin auth adapter lazily from ADC.

    No JSON service-account key is accepted or loaded here. Google-hosted deployments
    use Application Default Credentials from the runtime service account.
    """
    try:
        import firebase_admin
        from firebase_admin import auth
    except ImportError as exc:  # pragma: no cover - exercised only in hosted packaging
        raise RuntimeError("IDENTITY_PLATFORM_ADMIN_SDK_MISSING") from exc
    options = {"projectId": str(project_id)} if project_id else None
    try:
        app = firebase_admin.get_app("breadmaker-identity-platform")
    except ValueError:
        app = firebase_admin.initialize_app(options=options, name="breadmaker-identity-platform")
    return _FirebaseAdminAuthAdapter(auth, app)


class IdentityPlatformSessionService:
    """Server-cookie SessionService backed by Google Identity Platform/Firebase Auth."""

    requires_authentication = True
    test_bootstrap_enabled = False

    def __init__(
        self,
        admin_auth: Any,
        accounts: AccountIdentityService,
        admission: IdentityPlatformAdmissionStore,
        *,
        project_id: str,
        environment: str,
        now: Callable[[], float] = time.time,
        max_auth_age_seconds: int = 300,
        max_age_seconds: int = 5 * 24 * 60 * 60,
    ) -> None:
        self.admin_auth = admin_auth
        self.accounts = accounts
        self.admission = admission
        self.project_id = _required(project_id, "IDENTITY_PLATFORM_PROJECT_REQUIRED", maximum=256)
        self.environment = str(environment or "development").strip().lower()
        self.now = now
        self.max_auth_age_seconds = int(max_auth_age_seconds)
        self.max_age_seconds = int(max_age_seconds)
        if not (60 <= self.max_auth_age_seconds <= 900):
            raise IdentityPlatformError("IDENTITY_PLATFORM_AUTH_AGE_INVALID")
        if not (300 <= self.max_age_seconds <= 14 * 24 * 60 * 60):
            raise IdentityPlatformError("IDENTITY_PLATFORM_SESSION_DURATION_INVALID")
        explicit_secure = str(__import__("os").environ.get("BREADMAKER_SESSION_COOKIE_SECURE") or "").strip().lower()
        if explicit_secure in {"1", "true", "yes"}:
            self.cookie_secure = True
        elif explicit_secure in {"0", "false", "no"}:
            self.cookie_secure = False
        else:
            self.cookie_secure = self.environment not in {"test", "development", "dev", "local"}
        if self.environment in {"staging", "production"} and not self.cookie_secure:
            raise RuntimeError("SESSION_COOKIE_SECURE_REQUIRED")
        self.cookie_name = "__Host-breadmaker_session" if self.cookie_secure else "breadmaker_session"
        self.issuer = f"identity-platform://{self.project_id}"
        self._current: ContextVar[SessionPrincipal | None] = ContextVar("breadmaker_identity_platform_principal", default=None)

    def _claims_uid(self, claims: Mapping[str, Any]) -> str:
        return _required(claims.get("uid") or claims.get("sub"), "IDENTITY_UID_REQUIRED", maximum=256)

    def _existing_principal(self, claims: Mapping[str, Any], cookie: str) -> SessionPrincipal:
        external_uid = self._claims_uid(claims)
        if not self.admission.is_admitted(external_uid):
            raise IdentityPlatformError("PRIVATE_ALPHA_ADMISSION_REQUIRED")
        try:
            identity = self.accounts.resolve_external_identity(self.issuer, external_uid)
        except AccountIdentityError as exc:
            raise IdentityPlatformError("ACCOUNT_IDENTITY_INVALID") from exc
        if identity is None:
            raise IdentityPlatformError("ACCOUNT_IDENTITY_NOT_FOUND")
        issued = float(claims.get("iat") or self.now())
        expires = float(claims.get("exp") or (issued + self.max_age_seconds))
        authenticated = float(claims.get("auth_time") or issued)
        digest = hashlib.sha256(str(cookie).encode("utf-8")).hexdigest()[:32]
        return SessionPrincipal(
            "ses_" + digest,
            identity.user_id,
            _provider(claims),
            issued,
            expires,
            authenticated_at=authenticated,
        )

    def _enforce_provider_policy(self, claims: Mapping[str, Any], *, provider: str) -> None:
        # Social providers are accepted without mandatory MFA unless the Identity
        # Platform account itself has enrolled MFA, in which case Firebase requires
        # that challenge before an ID token can be obtained. Email/password has the
        # stronger Breadmaker policy and must prove an enrolled TOTP factor.
        if provider != "email_password":
            return
        external_uid = self._claims_uid(claims)
        try:
            user = self.admin_auth.get_user(external_uid)
        except Exception as exc:
            raise IdentityPlatformError("IDENTITY_PLATFORM_USER_LOOKUP_FAILED") from exc
        if bool(getattr(user, "disabled", False)):
            raise IdentityPlatformError("IDENTITY_PLATFORM_USER_DISABLED")
        factors = _enrolled_factors(user)
        has_totp = any(str(getattr(item, "factor_id", "") or "").strip().lower() == "totp" for item in (factors or []))
        if not has_totp:
            raise IdentityPlatformError("TOTP_ENROLLMENT_REQUIRED")

    def current(self):
        return self._current.get()

    @asynccontextmanager
    async def bind_async(self, token: str | None):
        # Perform the SAME revocation/admission checks on every request, off the
        # ASGI loop. Bind the result in the requesting task, not in the worker.
        from anyio import to_thread
        def resolve():
            with self.bind(token) as principal:
                return principal
        principal = await to_thread.run_sync(resolve)
        handle = self._current.set(principal)
        try:
            yield principal
        finally:
            self._current.reset(handle)

    @contextmanager
    def bind(self, token: str | None):
        principal = None
        value = str(token or "")
        if value:
            try:
                claims = self.admin_auth.verify_session_cookie(value, check_revoked=True)
                principal = self._existing_principal(claims, value)
            except IdentityPlatformError:
                raise
            except Exception as exc:
                raise IdentityPlatformError("IDENTITY_PLATFORM_SESSION_INVALID") from exc
        handle = self._current.set(principal)
        try:
            yield principal
        finally:
            self._current.reset(handle)

    def exchange_id_token(self, id_token: str, *, invitation_token: str | None = None) -> IdentityPlatformExchange:
        token = _required(id_token, "IDENTITY_PLATFORM_ID_TOKEN_REQUIRED", maximum=16384)
        try:
            claims = self.admin_auth.verify_id_token(token, check_revoked=True)
        except Exception as exc:
            raise IdentityPlatformError("IDENTITY_PLATFORM_ID_TOKEN_INVALID") from exc
        external_uid = self._claims_uid(claims)
        now = float(self.now())
        auth_time = float(claims.get("auth_time") or 0.0)
        if auth_time <= 0 or auth_time > now + 60 or now - auth_time > self.max_auth_age_seconds:
            raise IdentityPlatformError("RECENT_AUTH_REQUIRED")
        provider = _provider(claims)
        verified = claims.get("email_verified") is True
        if provider == "email_password" and not verified:
            raise IdentityPlatformError("VERIFIED_EMAIL_REQUIRED")
        self._enforce_provider_policy(claims, provider=provider)
        self.admission.require_or_consume(
            external_uid, invitation_token, email=claims.get("email"),
            email_verified=verified, provider=provider,
        )
        try:
            identity = self.accounts.resolve_or_create_external_identity(
                self.issuer,
                external_uid,
                provider=provider,
                display_name=_display_name(claims),
                email=str(claims.get("email") or "")[:320],
                email_verified=verified,
            )
        except AccountIdentityError as exc:
            raise IdentityPlatformError("ACCOUNT_IDENTITY_INVALID") from exc
        try:
            cookie = self.admin_auth.create_session_cookie(token, expires_in=timedelta(seconds=self.max_age_seconds))
        except Exception as exc:
            raise IdentityPlatformError("IDENTITY_PLATFORM_SESSION_CREATE_FAILED") from exc
        issued = now
        principal = SessionPrincipal(
            "ses_" + hashlib.sha256(str(cookie).encode("utf-8")).hexdigest()[:32],
            identity.user_id,
            provider,
            issued,
            issued + self.max_age_seconds,
            authenticated_at=auth_time,
        )
        return IdentityPlatformExchange(str(cookie), principal, provider, verified)

    # Identity Platform owns session-cookie issuance. Shared opaque-session mutation
    # methods stay disabled so provider authority cannot accidentally fall back to the
    # old process-local registry.
    def issue(self, user_id, *, auth_method, external_subject=None, authenticated_at=None):
        raise IdentityPlatformError("IDENTITY_PLATFORM_DIRECT_SESSION_ISSUANCE_DISABLED")

    def replace(self, token, user_id, *, auth_method, authenticated_at=None):
        raise IdentityPlatformError("IDENTITY_PLATFORM_DIRECT_SESSION_ISSUANCE_DISABLED")

    def revoke(self, token):
        # Ordinary logout is cookie deletion. Global provider revocation is explicit.
        return None

    def revoke_session_id(self, user_id: str, session_id: str) -> bool:
        return False

    def revoke_user(self, user_id: str, *, except_session_id: str | None = None) -> int:
        if except_session_id:
            raise IdentityPlatformError("IDENTITY_PLATFORM_SELECTIVE_REVOCATION_UNSUPPORTED")
        count = 0
        for identity in self.accounts.identities_for_user(str(user_id)):
            if identity.issuer != self.issuer:
                continue
            try:
                self.admin_auth.revoke_refresh_tokens(identity.subject)
            except Exception as exc:
                raise IdentityPlatformError("IDENTITY_PLATFORM_REVOCATION_FAILED") from exc
            count += 1
        return count

    def sessions_for_user(self, user_id: str) -> list[dict[str, Any]]:
        current = self.current()
        if current is None or current.user_id != str(user_id):
            return []
        return [{
            "session_id": current.session_id,
            "auth_method": current.auth_method,
            "authenticated_at": current.authenticated_at,
            "issued_at": current.issued_at,
            "absolute_expires_at": current.absolute_expires_at,
            "provider_managed": True,
        }]

    def active_session_ids(self) -> set[str]:
        current = self.current()
        return {current.session_id} if current is not None else set()

    def verify_test_secret(self, candidate: str) -> bool:
        return False

    def metadata(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "auth_mode": "identity_platform",
            "server_side": True,
            "provider": "google_identity_platform",
            "external_identity_key": "identity_platform_uid",
            "email_authoritative": False,
            "opaque_cookie": False,
            "provider_signed_session_cookie": True,
            "cookie_http_only": True,
            "cookie_secure": bool(self.cookie_secure),
            "cookie_same_site": "lax",
            "cookie_name": self.cookie_name,
            "absolute_seconds": self.max_age_seconds,
            "max_auth_age_seconds": self.max_auth_age_seconds,
            "check_revoked": True,
            "client_persistence": "in_memory",
            "provider_tokens_persisted": False,
            "private_alpha_admission": True,
            "test_bootstrap": False,
        }


def build_identity_platform_session_service(documents, accounts: AccountIdentityService, *, environment: str, project_id: str):
    admin = build_firebase_admin_auth(project_id=project_id)
    admission = IdentityPlatformAdmissionStore(documents)
    return IdentityPlatformSessionService(
        admin,
        accounts,
        admission,
        project_id=project_id,
        environment=environment,
    )


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_matches(cookie_value: str | None, header_value: str | None) -> bool:
    left = str(cookie_value or "")
    right = str(header_value or "")
    return bool(left and right and len(left) <= 256 and len(right) <= 256 and secrets.compare_digest(left, right))
