from __future__ import annotations

"""Lazy Cloud KMS key wrapper; imported only by an explicitly configured host.

No credentials, project or KMS setup is needed for local/offline Breadmaker.
Use a dedicated application runtime identity, never the backup/export/CI identity.
"""

from hosted.security.envelope import EnvelopeError


class GoogleKmsKeyWrapper:
    def __init__(self, key_resource: str, *, client=None) -> None:
        # Validate the resource without contacting Google. Codec checks it too.
        import re
        if not isinstance(key_resource, str) or not re.fullmatch(
            r"projects/[a-z][a-z0-9-]{4,62}/locations/[a-z0-9-]+/keyRings/[A-Za-z0-9_-]{1,63}/cryptoKeys/[A-Za-z0-9_-]{1,63}", key_resource
        ):
            raise EnvelopeError("HOSTED_ENVELOPE_KEY_RESOURCE_INVALID")
        self.key_resource = key_resource
        if client is None:
            try:
                from google.cloud import kms
                client = kms.KeyManagementServiceClient()
            except Exception:
                raise EnvelopeError("HOSTED_KMS_UNAVAILABLE") from None
        self._client = client

    @staticmethod
    def _crc(data: bytes) -> int:
        try:
            import google_crc32c
            return int(google_crc32c.value(data))
        except ImportError:
            raise EnvelopeError("HOSTED_KMS_CRC_LIBRARY_REQUIRED") from None

    def wrap(self, raw_key: bytes) -> bytes:
        if not isinstance(raw_key, bytes) or len(raw_key) != 32:
            raise EnvelopeError("HOSTED_KMS_KEY_SIZE_INVALID")
        try:
            response = self._client.encrypt(request={"name": self.key_resource, "plaintext": raw_key,
                "plaintext_crc32c": self._crc(raw_key)})
            result = bytes(response.ciphertext)
            if not response.verified_plaintext_crc32c or response.ciphertext_crc32c != self._crc(result):
                raise EnvelopeError("HOSTED_KMS_INTEGRITY_FAILED")
            return result
        except EnvelopeError:
            raise
        except Exception:
            raise EnvelopeError("HOSTED_KMS_WRAP_FAILED") from None

    def unwrap(self, wrapped_key: bytes) -> bytes:
        if not isinstance(wrapped_key, bytes) or not 1 <= len(wrapped_key) <= 8192:
            raise EnvelopeError("HOSTED_KMS_CIPHERTEXT_INVALID")
        try:
            response = self._client.decrypt(request={"name": self.key_resource, "ciphertext": wrapped_key,
                "ciphertext_crc32c": self._crc(wrapped_key)})
            plaintext = bytes(response.plaintext)
            if response.plaintext_crc32c != self._crc(plaintext):
                raise EnvelopeError("HOSTED_KMS_INTEGRITY_FAILED")
            return plaintext
        except EnvelopeError:
            raise
        except Exception:
            raise EnvelopeError("HOSTED_KMS_UNWRAP_FAILED") from None
