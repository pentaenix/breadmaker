from __future__ import annotations

"""M12B staged AES-256-GCM envelope codec, NOT wired to persistence yet.

This module is hosted-only. Local Breadmaker never imports it, uses KMS,
requires credentials, or sends telemetry. Production activation remains gated
until every Firestore/GCS/backup writer has a proven encrypted boundary.
"""

import base64
import json
import re
import secrets
from typing import Mapping, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

FORMAT = "bm-hosted-envelope-v1"
MAX_PAYLOAD = 16 * 1024 * 1024
MAX_ENVELOPE_BYTES = 24 * 1024 * 1024
_KEY = re.compile(r"^projects/[a-z][a-z0-9-]{4,62}/locations/[a-z0-9-]+/keyRings/[A-Za-z0-9_-]{1,63}/cryptoKeys/[A-Za-z0-9_-]{1,63}$")
_FIELDS = frozenset({"format", "key_resource", "wrapped_key", "nonce", "ciphertext"})

class EnvelopeError(ValueError):
    """Only structural error codes may propagate to hosted callers/logs."""

class KeyWrapper(Protocol):
    key_resource: str
    def wrap(self, raw_key: bytes) -> bytes: ...
    def unwrap(self, wrapped_key: bytes) -> bytes: ...


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(value: object, *, limit: int) -> bytes:
    if not isinstance(value, str) or len(value) > 4 * ((limit + 2) // 3) + 4:
        raise EnvelopeError("HOSTED_ENVELOPE_FIELD_INVALID")
    try:
        return base64.b64decode(value, validate=True)
    except Exception:
        raise EnvelopeError("HOSTED_ENVELOPE_FIELD_INVALID") from None


def _aad(context: Mapping[str, str]) -> bytes:
    if not isinstance(context, Mapping) or set(context) != {"project_id", "entity", "record_id"}:
        raise EnvelopeError("HOSTED_ENVELOPE_CONTEXT_INVALID")
    for key, value in context.items():
        if not isinstance(value, str) or not 1 <= len(value) <= 160 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise EnvelopeError("HOSTED_ENVELOPE_CONTEXT_INVALID")
    if not re.fullmatch(r"[0-9a-f]{32}", context["project_id"]):
        raise EnvelopeError("HOSTED_ENVELOPE_CONTEXT_INVALID")
    return json.dumps({"v": FORMAT, **dict(context)}, sort_keys=True, separators=(",", ":")).encode("utf-8")


class HostedEnvelopeCodec:
    """One independent random DEK per payload, 96-bit nonce, contextual AAD.

    The exact logical project/entity/record is authenticated. KMS never sees
    manuscript bytes. Avoid exposing any exception arguments or the raw DEK.
    """
    def __init__(self, key_wrapper: KeyWrapper) -> None:
        name = getattr(key_wrapper, "key_resource", "")
        if not isinstance(name, str) or not _KEY.fullmatch(name):
            raise EnvelopeError("HOSTED_ENVELOPE_KEY_RESOURCE_INVALID")
        self._wrapper = key_wrapper
        self.key_resource = name

    def seal(self, plaintext: bytes, *, context: Mapping[str, str]) -> dict[str, str]:
        if not isinstance(plaintext, bytes) or len(plaintext) > MAX_PAYLOAD:
            raise EnvelopeError("HOSTED_ENVELOPE_PAYLOAD_INVALID")
        associated = _aad(context)
        dek = secrets.token_bytes(32)
        nonce = secrets.token_bytes(12)
        try:
            ciphertext = AESGCM(dek).encrypt(nonce, plaintext, associated)
            wrapped = self._wrapper.wrap(dek)
            if not isinstance(wrapped, bytes) or not 1 <= len(wrapped) <= 8192:
                raise EnvelopeError("HOSTED_ENVELOPE_WRAPPED_KEY_INVALID")
            return {"format": FORMAT, "key_resource": self.key_resource,
                    "wrapped_key": _b64(wrapped), "nonce": _b64(nonce),
                    "ciphertext": _b64(ciphertext)}
        except EnvelopeError:
            raise
        except Exception:
            raise EnvelopeError("HOSTED_ENVELOPE_SEAL_FAILED") from None

    def open(self, record: Mapping[str, str], *, context: Mapping[str, str]) -> bytes:
        associated = _aad(context)
        if (not isinstance(record, Mapping) or set(record) != _FIELDS or
                record.get("format") != FORMAT or record.get("key_resource") != self.key_resource):
            raise EnvelopeError("HOSTED_ENVELOPE_FORMAT_OR_KEY_MISMATCH")
        nonce = _unb64(record["nonce"], limit=12)
        wrapped = _unb64(record["wrapped_key"], limit=8192)
        payload = _unb64(record["ciphertext"], limit=MAX_PAYLOAD + 16)
        if len(nonce) != 12 or not 1 <= len(wrapped) <= 8192 or not 16 <= len(payload) <= MAX_PAYLOAD + 16:
            raise EnvelopeError("HOSTED_ENVELOPE_FIELD_INVALID")
        try:
            dek = self._wrapper.unwrap(wrapped)
            if not isinstance(dek, bytes) or len(dek) != 32:
                raise EnvelopeError("HOSTED_ENVELOPE_KEY_INVALID")
            return AESGCM(dek).decrypt(nonce, payload, associated)
        except (EnvelopeError, InvalidTag):
            raise EnvelopeError("HOSTED_ENVELOPE_AUTHENTICATION_FAILED") from None
        except Exception:
            raise EnvelopeError("HOSTED_ENVELOPE_DECRYPT_FAILED") from None
