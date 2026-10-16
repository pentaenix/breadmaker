from __future__ import annotations

"""Explicit accessors for local/native persistence bridges.

These helpers exist to make migration debt visible. Serverless hosted composition is
expected to provide no native filesystem bridges; callers that still require one must
be migrated before they can run in Cloud Run.
"""

from .contracts import RuntimeServices


def require_native_projects(runtime: RuntimeServices):
    if runtime.native_projects is None:
        raise RuntimeError('NATIVE_PROJECT_STORE_UNAVAILABLE')
    return runtime.native_projects


def require_native_assets(runtime: RuntimeServices):
    if runtime.native_assets is None:
        raise RuntimeError('NATIVE_ASSET_STORE_UNAVAILABLE')
    return runtime.native_assets


def require_native_history(runtime: RuntimeServices):
    if runtime.native_history is None:
        raise RuntimeError('NATIVE_HISTORY_STORE_UNAVAILABLE')
    return runtime.native_history


def require_native_backups(runtime: RuntimeServices):
    if runtime.native_backups is None:
        raise RuntimeError('NATIVE_BACKUP_STORE_UNAVAILABLE')
    return runtime.native_backups
