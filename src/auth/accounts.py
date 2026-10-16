from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class AccountIdentityError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExternalIdentity:
    issuer: str
    subject: str
    user_id: str
    provider: str
    email: str
    email_verified: bool
    created_at: float
    updated_at: float

    def public(self) -> dict[str, Any]:
        return {
            'issuer': self.issuer,
            'subject': self.subject,
            'user_id': self.user_id,
            'provider': self.provider,
            'email': self.email,
            'email_verified': self.email_verified,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }


class AccountIdentityService(Protocol):
    enabled: bool
    def resolve_external_identity(self, issuer: str, subject: str) -> ExternalIdentity | None: ...
    def resolve_or_create_external_identity(
        self,
        issuer: str,
        subject: str,
        *,
        provider: str,
        display_name: str,
        email: str = '',
        email_verified: bool = False,
    ) -> ExternalIdentity: ...
    def identities_for_user(self, user_id: str) -> list[ExternalIdentity]: ...
    def primary_identity_for_user(self, user_id: str) -> ExternalIdentity | None: ...
    def link_external_identity_to_user(
        self,
        user_id: str,
        issuer: str,
        subject: str,
        *,
        provider: str,
        email: str = '',
        email_verified: bool = False,
    ) -> ExternalIdentity: ...
    def unlink_external_identity_from_user(self, user_id: str, issuer: str, subject: str) -> ExternalIdentity: ...
    def metadata(self) -> dict[str, Any]: ...


class NullAccountIdentityService:
    """Local Breadmaker has no account-identity store and never needs one."""

    enabled = False

    def resolve_external_identity(self, issuer, subject):
        return None

    def resolve_or_create_external_identity(self, issuer, subject, *, provider, display_name, email='', email_verified=False):
        raise AccountIdentityError('ACCOUNT_IDENTITY_DISABLED')

    def identities_for_user(self, user_id):
        return []

    def primary_identity_for_user(self, user_id):
        return None

    def link_external_identity_to_user(self, user_id, issuer, subject, *, provider, email='', email_verified=False):
        raise AccountIdentityError('ACCOUNT_IDENTITY_DISABLED')

    def unlink_external_identity_from_user(self, user_id, issuer, subject):
        raise AccountIdentityError('ACCOUNT_IDENTITY_DISABLED')

    def metadata(self):
        return {'enabled': False, 'storage': 'none', 'identity_key': ['issuer', 'subject']}


class HostedAccountIdentityStore:
    """Hosted-only external identity registry.

    This registry stores no passwords, OAuth tokens, TOTP secrets, recovery codes, or
    provider credentials. It maps one externally verified OIDC identity key
    ``(issuer, subject)`` to Breadmaker's existing stable internal ``usr_`` id.

    Email is metadata only. In particular, resolve/create never searches by email, so
    two providers/subjects that report the same email remain distinct accounts unless
    the explicit dual-proof account-linking flow attaches the second verified identity.

    Linking never merges two existing Breadmaker users. If a secondary external
    identity is already attached to another ``usr_`` account, the operation fails
    closed so identity linking cannot become an accidental project/data merge.
    """

    enabled = True
    _VERSION = 1

    def __init__(self, workspace):
        self.workspace = workspace
        self.root = Path(workspace.root) / 'account-identities'
        self.index_path = self.root / 'index.json'
        self._lock = threading.RLock()

    @staticmethod
    def _clean_required(value: str, code: str, max_length: int) -> str:
        result = str(value or '').strip()
        if not result or len(result) > max_length:
            raise AccountIdentityError(code)
        return result

    @classmethod
    def _key(cls, issuer: str, subject: str) -> tuple[str, str]:
        return (
            cls._clean_required(issuer, 'IDENTITY_ISSUER_REQUIRED', 2048),
            cls._clean_required(subject, 'IDENTITY_SUBJECT_REQUIRED', 1024),
        )

    def _empty(self) -> dict[str, Any]:
        return {'version': self._VERSION, 'identities': []}

    def _read_locked(self) -> dict[str, Any]:
        if not self.index_path.is_file():
            return self._empty()
        try:
            raw = json.loads(self.index_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            raise AccountIdentityError('ACCOUNT_IDENTITY_STORE_INVALID') from exc
        if not isinstance(raw, dict) or int(raw.get('version') or 0) != self._VERSION or not isinstance(raw.get('identities'), list):
            raise AccountIdentityError('ACCOUNT_IDENTITY_STORE_INVALID')

        seen: set[tuple[str, str]] = set()
        normalized: list[dict[str, Any]] = []
        for item in raw['identities']:
            if not isinstance(item, dict):
                raise AccountIdentityError('ACCOUNT_IDENTITY_STORE_INVALID')
            issuer, subject = self._key(item.get('issuer', ''), item.get('subject', ''))
            key = (issuer, subject)
            if key in seen:
                raise AccountIdentityError('ACCOUNT_IDENTITY_DUPLICATE')
            seen.add(key)
            user_id = str(item.get('user_id') or '').strip()
            if not user_id.startswith('usr_'):
                raise AccountIdentityError('ACCOUNT_IDENTITY_USER_INVALID')
            normalized.append({
                'issuer': issuer,
                'subject': subject,
                'user_id': user_id,
                'provider': str(item.get('provider') or '').strip()[:80],
                'email': str(item.get('email') or '').strip()[:320],
                'email_verified': bool(item.get('email_verified')),
                'created_at': float(item.get('created_at') or 0.0),
                'updated_at': float(item.get('updated_at') or 0.0),
            })
        return {'version': self._VERSION, 'identities': normalized}

    def _write_locked(self, payload: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix='identities-', suffix='.json', dir=self.root)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self.index_path)
            try:
                os.chmod(self.index_path, 0o600)
            except OSError:
                pass
            try:
                dfd = os.open(str(self.root), os.O_RDONLY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
            except (OSError, AttributeError):
                pass
        finally:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass

    @staticmethod
    def _identity(item: dict[str, Any]) -> ExternalIdentity:
        return ExternalIdentity(
            issuer=str(item['issuer']),
            subject=str(item['subject']),
            user_id=str(item['user_id']),
            provider=str(item.get('provider') or ''),
            email=str(item.get('email') or ''),
            email_verified=bool(item.get('email_verified')),
            created_at=float(item.get('created_at') or 0.0),
            updated_at=float(item.get('updated_at') or 0.0),
        )

    def resolve_external_identity(self, issuer: str, subject: str) -> ExternalIdentity | None:
        issuer, subject = self._key(issuer, subject)
        with self._lock:
            payload = self._read_locked()
            row = next((item for item in payload['identities'] if item['issuer'] == issuer and item['subject'] == subject), None)
            if row is None:
                return None
            # Fail closed if identity metadata points at a user record that no longer
            # exists. Never silently reassign an external identity to another profile.
            try:
                self.workspace.local_profile(str(row['user_id']))
            except Exception as exc:
                raise AccountIdentityError('ACCOUNT_IDENTITY_USER_MISSING') from exc
            return self._identity(row)

    def resolve_or_create_external_identity(
        self,
        issuer: str,
        subject: str,
        *,
        provider: str,
        display_name: str,
        email: str = '',
        email_verified: bool = False,
    ) -> ExternalIdentity:
        issuer, subject = self._key(issuer, subject)
        provider = self._clean_required(provider, 'IDENTITY_PROVIDER_REQUIRED', 80)
        name = str(display_name or '').strip()[:80] or 'Writer'
        email_value = str(email or '').strip()[:320]
        verified = bool(email_verified)
        now = time.time()

        with self._lock:
            payload = self._read_locked()
            existing = next((item for item in payload['identities'] if item['issuer'] == issuer and item['subject'] == subject), None)
            if existing is not None:
                try:
                    self.workspace.local_profile(str(existing['user_id']))
                except Exception as exc:
                    raise AccountIdentityError('ACCOUNT_IDENTITY_USER_MISSING') from exc
                # Provider/email are non-authoritative profile metadata. Updating them
                # never changes the account mapping or searches for another user by email.
                existing['provider'] = provider
                existing['email'] = email_value
                existing['email_verified'] = verified
                existing['updated_at'] = now
                self._write_locked(payload)
                return self._identity(existing)

            profile = self.workspace.create_local_profile(name)
            row = {
                'issuer': issuer,
                'subject': subject,
                'user_id': str(profile['user_id']),
                'provider': provider,
                'email': email_value,
                'email_verified': verified,
                'created_at': now,
                'updated_at': now,
            }
            payload['identities'].append(row)
            self._write_locked(payload)
            return self._identity(row)

    def identities_for_user(self, user_id: str) -> list[ExternalIdentity]:
        uid = str(user_id or '').strip()
        if not uid.startswith('usr_'):
            return []
        with self._lock:
            payload = self._read_locked()
            return [self._identity(item) for item in payload['identities'] if item['user_id'] == uid]


    def primary_identity_for_user(self, user_id: str) -> ExternalIdentity | None:
        """Return the stable external identity used as the hosted/Breadmaker link primary.

        The first identity attached to a Breadmaker user is canonical. Explicit linking
        only appends secondaries, so this deterministic choice remains stable without
        trusting provider profile metadata or email addresses.
        """
        identities = self.identities_for_user(user_id)
        if not identities:
            return None
        return min(identities, key=lambda item: (item.created_at, item.subject))

    def link_external_identity_to_user(
        self,
        user_id: str,
        issuer: str,
        subject: str,
        *,
        provider: str,
        email: str = '',
        email_verified: bool = False,
    ) -> ExternalIdentity:
        """Attach one already-proven external identity to an existing Breadmaker user.

        This method deliberately cannot create or merge Breadmaker users. The caller
        must have independently proven both identities before invoking it. Existing
        mappings owned by another ``usr_`` id are conflicts, even when email matches.
        """
        uid = self._clean_required(user_id, 'ACCOUNT_IDENTITY_USER_REQUIRED', 256)
        if not uid.startswith('usr_'):
            raise AccountIdentityError('ACCOUNT_IDENTITY_USER_INVALID')
        issuer, subject = self._key(issuer, subject)
        provider = self._clean_required(provider, 'IDENTITY_PROVIDER_REQUIRED', 80)
        email_value = str(email or '').strip()[:320]
        verified = bool(email_verified)
        now = time.time()

        with self._lock:
            try:
                self.workspace.local_profile(uid)
            except Exception as exc:
                raise AccountIdentityError('ACCOUNT_IDENTITY_USER_MISSING') from exc
            payload = self._read_locked()
            existing = next((item for item in payload['identities'] if item['issuer'] == issuer and item['subject'] == subject), None)
            if existing is not None:
                if str(existing['user_id']) != uid:
                    raise AccountIdentityError('ACCOUNT_IDENTITY_LINK_CONFLICT')
                # Idempotent retry after a provider-side link succeeded. Profile
                # metadata may refresh, but ownership of the identity never changes.
                existing['provider'] = provider
                if email_value:
                    existing['email'] = email_value
                    existing['email_verified'] = verified
                existing['updated_at'] = now
                self._write_locked(payload)
                return self._identity(existing)

            row = {
                'issuer': issuer,
                'subject': subject,
                'user_id': uid,
                'provider': provider,
                'email': email_value,
                'email_verified': verified,
                'created_at': now,
                'updated_at': now,
            }
            payload['identities'].append(row)
            self._write_locked(payload)
            return self._identity(row)


    def unlink_external_identity_from_user(self, user_id: str, issuer: str, subject: str) -> ExternalIdentity:
        """Remove one secondary identity mapping from this Breadmaker user.

        Provider mutation is intentionally owned by the Checkpoint 10 security service,
        which uses this operation as the reversible local half of unlinking. This method
        refuses reassignment, last-identity removal, and missing mappings; if provider
        mutation later fails, the service restores this exact identity to the same user.
        """
        uid = self._clean_required(user_id, 'ACCOUNT_IDENTITY_USER_REQUIRED', 256)
        if not uid.startswith('usr_'):
            raise AccountIdentityError('ACCOUNT_IDENTITY_USER_INVALID')
        issuer, subject = self._key(issuer, subject)
        with self._lock:
            payload = self._read_locked()
            owned = [item for item in payload['identities'] if item['user_id'] == uid]
            if len(owned) <= 1:
                raise AccountIdentityError('ACCOUNT_IDENTITY_LAST_IDENTITY')
            row = next((item for item in owned if item['issuer'] == issuer and item['subject'] == subject), None)
            if row is None:
                existing = next((item for item in payload['identities'] if item['issuer'] == issuer and item['subject'] == subject), None)
                if existing is not None:
                    raise AccountIdentityError('ACCOUNT_IDENTITY_LINK_CONFLICT')
                raise AccountIdentityError('ACCOUNT_IDENTITY_NOT_FOUND')
            payload['identities'].remove(row)
            self._write_locked(payload)
            return self._identity(row)

    def metadata(self):
        return {
            'enabled': True,
            'storage': 'hosted_identity_registry',
            'identity_key': ['issuer', 'subject'],
            'email_authoritative': False,
            'stores_credentials': False,
            'explicit_dual_proof_linking': True,
            'link_by_email': False,
            'existing_user_merge_on_link': False,
        }
