"""Hosted structured-state persistence for the serverless rewrite.

The deterministic memory driver powers release contracts. ``GoogleFirestoreDocumentStore``
is imported explicitly by hosted composition only; local ``./run`` does not import the
Google SDK or require cloud credentials.
"""

from .contracts import (
    DocumentAlreadyExists,
    DocumentConflict,
    DocumentNotFound,
    HostedAuthorizationError,
    HostedValidationError,
    ProjectUnavailable,
)
from .memory import MemoryDocumentStore
from .repositories import (
    HostedIdentityBindingRepository,
    HostedProjectEntityRepository,
    HostedProjectRepository,
    HostedStructuredRepository,
    HostedUserRepository,
)

__all__ = [
    "DocumentAlreadyExists",
    "DocumentConflict",
    "DocumentNotFound",
    "HostedAuthorizationError",
    "HostedValidationError",
    "ProjectUnavailable",
    "MemoryDocumentStore",
    "HostedIdentityBindingRepository",
    "HostedProjectEntityRepository",
    "HostedProjectRepository",
    "HostedStructuredRepository",
    "HostedUserRepository",
]
