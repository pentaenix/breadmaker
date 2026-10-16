"""Hosted Cloud Storage persistence boundary for serverless Breadmaker.

The deterministic ``MemoryObjectStore`` is the release-test driver. The Google SDK
is imported only by ``google_driver.py`` and only future Milestone-5 hosted
composition should instantiate it.
"""

from .assets import HostedAssetStore
from .contracts import (
    HostedObjectError,
    ObjectAlreadyExists,
    ObjectGenerationConflict,
    ObjectIntegrityError,
    ObjectLimitExceeded,
    ObjectMetadata,
    ObjectNameInvalid,
    ObjectNotFound,
)
from .google_driver import GoogleCloudObjectStore
from .memory import MemoryObjectStore
from .publication import (
    HostedHybridObjectPublisher,
    ImmutableObjectPublisher,
    PublishedObject,
    delete_confirmed_orphan,
    discover_orphans,
)

__all__ = [
    "HostedAssetStore",
    "HostedObjectError",
    "ObjectAlreadyExists",
    "ObjectGenerationConflict",
    "ObjectIntegrityError",
    "ObjectLimitExceeded",
    "ObjectMetadata",
    "ObjectNameInvalid",
    "ObjectNotFound",
    "GoogleCloudObjectStore",
    "MemoryObjectStore",
    "HostedHybridObjectPublisher",
    "ImmutableObjectPublisher",
    "PublishedObject",
    "delete_confirmed_orphan",
    "discover_orphans",
]
