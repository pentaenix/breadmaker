from __future__ import annotations

"""Provider-neutral account/security policy for Identity Platform.

The browser performs interactive credential/MFA operations with Firebase Auth in
memory.  Breadmaker never receives passwords, OAuth access/refresh tokens, TOTP
secrets, or TOTP verification codes.  For every sensitive Breadmaker-side operation,
the server independently verifies a freshly-authenticated ID token and requires it to
belong to the exact Identity Platform UID already bound to the current Breadmaker
session.
"""

import time
from dataclasses import dataclass
from typing import Any, Mapping

from auth.accounts import AccountIdentityError, AccountIdentityService
from auth.identity_platform import IdentityPlatformError, IdentityPlatformSessionService, _enrolled_factors
from auth.sessions import SessionPrincipal


_PROVIDER_NAMES = {
    "google.com": "google",
    "apple.com": "apple",
    "password": "email_password",
}


@dataclass(frozen=True)
class FreshIdentityProof:
    external_uid: str
    provider: str
    auth_time: float
    email: str
    email_verified: bool


def _provider_name(provider_id: str) -> str:
    value = str(provider_id or "").strip().lower()
    return _PROVIDER_NAMES.get(value, value[:80] or "identity_platform")


def _factor_dict(factor: Any) -> dict[str, Any]:
    factor_id = str(getattr(factor, "factor_id", "") or "").strip().lower()
    uid = str(getattr(factor, "uid", "") or "").strip()
    display = str(getattr(factor, "display_name", "") or "").strip()[:160]
    enrolled_at = getattr(factor, "enrollment_time", None)
    # Only whitelisted Admin enrollment metadata reaches this representation.
    return {
        "id": uid,
        "type": "totp" if factor_id == "totp" else factor_id,
        "display_name": display,
        "enrollment_time": str(enrolled_at or ""),
    }


def _provider_dict(info: Any) -> dict[str, Any]:
    provider_id = str(getattr(info, "provider_id", "") or "").strip().lower()
    return {
        "provider_id": provider_id,
        "provider": _provider_name(provider_id),
        "provider_uid": str(getattr(info, "uid", "") or "")[:1024],
        "email": str(getattr(info, "email", "") or "")[:320],
        "display_name": str(getattr(info, "display_name", "") or "")[:160],
    }


class IdentityPlatformSecurityService:
    def __init__(
        self,
        sessions: IdentityPlatformSessionService,
        accounts: AccountIdentityService,
        *,
        now=time.time,
        fresh_auth_seconds: int = 300,
    ) -> None:
        self.sessions = sessions
        self.accounts = accounts
        self.now = now
        self.fresh_auth_seconds = int(fresh_auth_seconds)
        if self.fresh_auth_seconds < 60 or self.fresh_auth_seconds > 900:
            raise IdentityPlatformError("IDENTITY_PLATFORM_AUTH_AGE_INVALID")

    def _external_uid(self, principal: SessionPrincipal) -> str:
        matches = [
            identity for identity in self.accounts.identities_for_user(principal.user_id)
            if identity.issuer == self.sessions.issuer
        ]
        if len(matches) != 1:
            raise IdentityPlatformError("ACCOUNT_IDENTITY_INVALID")
        return str(matches[0].subject)

    def _get_user(self, external_uid: str):
        try:
            return self.sessions.admin_auth.get_user(external_uid)
        except Exception as exc:
            raise IdentityPlatformError("IDENTITY_PLATFORM_USER_LOOKUP_FAILED") from exc

    @staticmethod
    def _providers(user: Any) -> list[dict[str, Any]]:
        rows = [_provider_dict(item) for item in (getattr(user, "provider_data", None) or [])]
        # Firebase may expose the password provider as the primary email/password
        # credential without a provider_data row.  The authoritative presence test is
        # still provider_data when available; fall back to ``password_hash`` only for
        # Admin test doubles/legacy records where provider metadata is incomplete.
        if not any(row["provider_id"] == "password" for row in rows):
            password_hash = getattr(user, "password_hash", None)
            if password_hash:
                rows.append({
                    "provider_id": "password",
                    "provider": "email_password",
                    "provider_uid": str(getattr(user, "uid", "") or ""),
                    "email": str(getattr(user, "email", "") or "")[:320],
                    "display_name": str(getattr(user, "display_name", "") or "")[:160],
                })
        rows.sort(key=lambda row: (row["provider_id"], row["provider_uid"]))
        return rows

    @staticmethod
    def _factors(user: Any) -> list[dict[str, Any]]:
        factors = _enrolled_factors(user)
        return [_factor_dict(item) for item in (factors or [])]

    @classmethod
    def _has_totp(cls, user: Any) -> bool:
        return any(item.get("type") == "totp" for item in cls._factors(user))

    def enforce_exchange_policy(self, claims: Mapping[str, Any], *, provider: str) -> None:
        """Enforce provider/MFA policy before Breadmaker mints a session cookie."""
        external_uid = str(claims.get("uid") or claims.get("sub") or "").strip()
        if not external_uid:
            raise IdentityPlatformError("IDENTITY_UID_REQUIRED")
        user = self._get_user(external_uid)
        if bool(getattr(user, "disabled", False)):
            raise IdentityPlatformError("IDENTITY_PLATFORM_USER_DISABLED")
        if provider == "email_password":
            if claims.get("email_verified") is not True:
                raise IdentityPlatformError("VERIFIED_EMAIL_REQUIRED")
            if not self._has_totp(user):
                raise IdentityPlatformError("TOTP_ENROLLMENT_REQUIRED")

    def verify_fresh_proof(
        self,
        principal: SessionPrincipal,
        id_token: str,
        *,
        purpose: str,
    ) -> FreshIdentityProof:
        value = str(id_token or "").strip()
        if not value or len(value) > 16384:
            raise IdentityPlatformError("IDENTITY_PLATFORM_ID_TOKEN_REQUIRED")
        expected_uid = self._external_uid(principal)
        try:
            claims = self.sessions.admin_auth.verify_id_token(value, check_revoked=True)
        except Exception as exc:
            raise IdentityPlatformError("IDENTITY_PLATFORM_ID_TOKEN_INVALID") from exc
        actual_uid = str(claims.get("uid") or claims.get("sub") or "").strip()
        if not actual_uid or actual_uid != expected_uid:
            raise IdentityPlatformError("IDENTITY_PLATFORM_REAUTH_USER_MISMATCH")
        now = float(self.now())
        auth_time = float(claims.get("auth_time") or 0.0)
        if auth_time <= 0 or auth_time > now + 60 or now - auth_time > self.fresh_auth_seconds:
            raise IdentityPlatformError("RECENT_AUTH_REQUIRED")
        firebase_claim = claims.get("firebase")
        provider = _provider_name(str((firebase_claim if isinstance(firebase_claim, Mapping) else {}).get("sign_in_provider") or ""))
        if provider == "email_password" and claims.get("email_verified") is not True:
            raise IdentityPlatformError("VERIFIED_EMAIL_REQUIRED")
        # Purpose is deliberately allow-listed so arbitrary browser text never becomes
        # a policy capability.
        allowed = {
            "link_identity", "unlink_identity", "change_password", "enable_totp",
            "replace_totp", "disable_optional_totp", "sign_out_all_sessions",
            "delete_account", "security_refresh",
        }
        if str(purpose or "") not in allowed:
            raise IdentityPlatformError("SECURITY_PURPOSE_INVALID")
        return FreshIdentityProof(
            external_uid=actual_uid,
            provider=provider,
            auth_time=auth_time,
            email=str(claims.get("email") or "")[:320],
            email_verified=claims.get("email_verified") is True,
        )

    def snapshot(self, principal: SessionPrincipal) -> dict[str, Any]:
        uid = self._external_uid(principal)
        user = self._get_user(uid)
        providers = self._providers(user)
        factors = self._factors(user)
        password_provider_present = any(row["provider_id"] == "password" for row in providers)
        totp = [row for row in factors if row.get("type") == "totp"]
        identities = []
        current_provider = str(principal.auth_method or '').strip().lower()
        for index, row in enumerate(providers):
            identities.append({
                "subject": row["provider_id"],
                "provider_id": row["provider_id"],
                "provider": row["provider"],
                "email": row["email"] or str(getattr(user, "email", "") or "")[:320],
                "email_verified": bool(getattr(user, "email_verified", False)),
                "primary": row["provider"] == current_provider if current_provider in {item["provider"] for item in providers} else index == 0,
                "can_unlink": len(providers) > 1,
            })
        current_session = self.sessions.sessions_for_user(principal.user_id)
        return {
            "auth_mode": "identity_platform",
            "identity_platform_uid_bound": True,
            "identities": identities,
            "mfa": {
                "methods": totp,
                "totp_enrolled": bool(totp),
                "totp_mandatory": password_provider_present,
                "totp_can_disable": bool(totp) and not password_provider_present,
                "provider_owned": True,
            },
            "sessions": current_session,
            "current_session_id": principal.session_id,
            "selective_session_revocation_supported": False,
            "fresh_reauthentication_required": True,
            "fresh_reauthentication_seconds": self.fresh_auth_seconds,
            "email_is_authority": False,
            "provider_tokens_persisted": False,
        }

    def sync_after_provider_mutation(
        self,
        principal: SessionPrincipal,
        id_token: str,
        *,
        purpose: str,
    ) -> dict[str, Any]:
        proof = self.verify_fresh_proof(principal, id_token, purpose=purpose)
        user = self._get_user(proof.external_uid)
        providers = self._providers(user)
        if not providers:
            raise IdentityPlatformError("IDENTITY_PLATFORM_LAST_PROVIDER_FORBIDDEN")
        # Email/password is allowed only with verified email + TOTP.
        if any(row["provider_id"] == "password" for row in providers):
            if not bool(getattr(user, "email_verified", False)):
                raise IdentityPlatformError("VERIFIED_EMAIL_REQUIRED")
            if not self._has_totp(user):
                raise IdentityPlatformError("TOTP_ENROLLMENT_REQUIRED")
        return self.snapshot(principal)
