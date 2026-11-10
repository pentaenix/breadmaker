"""Uncomposed fixed-object GCS adapter; requires an injected approved SDK bucket.

No ADC/client creation, credential paths, endpoint override, list, history,
separate delete operation or automatic retries. GCS replacing an existing object
requires overwrite/delete permission semantics even without a delete request.
Bucket review, IAM, versioning, soft delete and retention are activation gates:
using one live object name does NOT prove old provider versions are unretained.
RPC timeouts and elapsed checks do not forcibly cancel credential refresh or
other injected callbacks; future keyless identity composition must bound those.
"""
from __future__ import annotations

import base64
import hashlib
import time

from .inventory import MAX_PAYLOAD_BYTES
from .inventory_publication import (
    OBJECT_NAME, STORE_SECONDS, InventoryResource, InventoryPublicationError,
    InventoryWriteConflict, InventoryWriteUncertain, VersionedInventory,
    decode_inventory, require_generation, utc_now,
)


class _Deadline:
    def __init__(self, timeout, clock):
        if type(timeout) not in (int, float) or not 0 < timeout <= STORE_SECONDS:
            raise InventoryPublicationError("INVENTORY_STORE_TIMEOUT_INVALID")
        self.clock, self.started, self.timeout = clock, clock(), timeout

    def remaining(self):
        elapsed = self.clock() - self.started
        if not 0 <= elapsed < self.timeout:
            raise InventoryPublicationError("INVENTORY_STORE_EXPIRED")
        return self.timeout - elapsed


def _md5(payload):
    # Provider transport integrity only, not authentication or a content ID.
    return base64.b64encode(hashlib.md5(payload, usedforsecurity=False).digest()).decode("ascii")


def _is_conflict(error):
    try:
        from google.api_core.exceptions import PreconditionFailed
        return isinstance(error, PreconditionFailed)
    except ImportError:
        return False


class GoogleInventoryLatestStore:
    """All calls are pinned to one reviewed bucket and OBJECT_NAME.

    The injected bucket is a trusted SDK dependency, not a browser argument.
    Its name convention and endpoint check are guardrails, not IAM proof.
    """
    def __init__(self, resource, *, bucket, now=utc_now, monotonic=time.monotonic):
        try:
            if (type(resource) is not InventoryResource or bucket.name != resource.bucket
                    or bucket.client._connection.API_BASE_URL != "https://storage.googleapis.com"
                    or not callable(now) or not callable(monotonic)):
                raise ValueError()
        except Exception:
            raise InventoryPublicationError("INVENTORY_STORE_CONFIGURATION_INVALID") from None
        self._bucket, self._now, self._clock = bucket, now, monotonic

    def read_latest(self, *, timeout):
        try:
            budget = _Deadline(timeout, self._clock)
            blob = self._bucket.get_blob(OBJECT_NAME, timeout=budget.remaining(), retry=None)
            budget.remaining()
            if blob is None:
                return None
            generation, size = require_generation(blob.generation), blob.size
            if (blob.name != OBJECT_NAME or type(size) is not int or not 0 < size <= MAX_PAYLOAD_BYTES
                    or blob.content_type not in ("application/json", "application/json; charset=utf-8")
                    or blob.content_encoding not in (None, "identity") or blob.metadata not in (None, {})):
                raise ValueError()
            # One excess byte detects inconsistent metadata. Generation binding
            # prevents a concurrent overwrite changing the bytes after metadata.
            payload = blob.download_as_bytes(start=0, end=size, if_generation_match=generation,
                raw_download=True, single_shot_download=True, checksum=None,
                timeout=budget.remaining(), retry=None)
            budget.remaining()
            if type(payload) is not bytes or len(payload) != size or blob.md5_hash != _md5(payload):
                raise ValueError()
            decode_inventory(payload, now=self._now())
            return VersionedInventory(generation, payload)
        except Exception:
            raise InventoryPublicationError("INVENTORY_STORE_UNAVAILABLE") from None

    def replace_latest(self, payload, *, if_generation_match, timeout):
        # Validation precedes any provider call, even when used outside coordinator.
        require_generation(if_generation_match, allow_zero=True)
        decode_inventory(payload, now=self._now())
        budget = _Deadline(timeout, self._clock)
        try:
            blob = self._bucket.blob(OBJECT_NAME)
            blob.cache_control = "no-store, max-age=0"
            blob.metadata = None
            digest = _md5(payload)
            blob.md5_hash = digest
            # <=16KiB is always multipart in the pinned SDK. Send the MD5 in
            # original metadata for server integrity checking, with checksum=None
            # to avoid SDK automatic corruption-cleanup deletion. No ACL changes.
            blob.upload_from_string(payload, content_type="application/json", checksum=None,
                if_generation_match=if_generation_match, timeout=budget.remaining(), retry=None)
            budget.remaining()
            generation = require_generation(blob.generation)
            if (generation == if_generation_match or type(blob.size) is not int or blob.size != len(payload)
                    or blob.md5_hash != digest):
                raise ValueError()
        except Exception as error:
            if _is_conflict(error):
                raise InventoryWriteConflict("INVENTORY_WRITE_CONFLICT") from None
            raise InventoryWriteUncertain("INVENTORY_WRITE_UNCERTAIN") from None
