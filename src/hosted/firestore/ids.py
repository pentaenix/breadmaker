from __future__ import annotations

import hashlib
import re
import secrets

from .contracts import HostedValidationError

_PROJECT = re.compile(r"^[0-9a-f]{32}$")
_USER = re.compile(r"^usr_[0-9a-f]{32}$")
_OPAQUE = re.compile(r"^[A-Za-z0-9_-]{1,160}$")


def new_project_id() -> str:
    return secrets.token_hex(16)


def new_user_id() -> str:
    return "usr_" + secrets.token_hex(16)


def require_project_id(value: str) -> str:
    value = str(value)
    if not _PROJECT.fullmatch(value):
        raise HostedValidationError("HOSTED_PROJECT_ID_INVALID")
    return value


def require_user_id(value: str) -> str:
    value = str(value)
    if not _USER.fullmatch(value):
        raise HostedValidationError("HOSTED_USER_ID_INVALID")
    return value


def require_opaque_id(value: str, *, label: str = "id") -> str:
    value = str(value)
    if not _OPAQUE.fullmatch(value):
        raise HostedValidationError(f"HOSTED_{label.upper()}_INVALID")
    return value


def identity_binding_id(provider_uid: str) -> str:
    value = str(provider_uid)
    if not value or len(value) > 4096:
        raise HostedValidationError("HOSTED_PROVIDER_UID_INVALID")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
