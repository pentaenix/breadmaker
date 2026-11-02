"""Request-local authority for an import's reserved, unpublished project prefix.

This is server-only staging, never project membership. Every provider write
rechecks the durable actor-bound reservation and current account erasure fence.
"""
from contextlib import contextmanager
from contextvars import ContextVar

from hosted.firestore.contracts import DocumentConflict
from hosted.firestore.ids import require_user_id
from hosted.idempotency import stable_project_id

_ACTIVE = ContextVar('breadmaker_package_import_staging', default=None)


@contextmanager
def package_import_staging(operation, user_id):
    scope = (stable_project_id(operation), require_user_id(user_id), operation)
    token = _ACTIVE.set(scope)
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def permits_unpublished_import(tx, project_id, actor):
    scope = _ACTIVE.get()
    if scope is None or scope[0] != project_id:
        return False
    _, uid, operation = scope
    if actor != uid:
        raise DocumentConflict('HOSTED_IMPORT_STAGING_ACTOR_MISMATCH')
    reservation = tx.get(('hosted_import_operations', operation))
    if reservation is None or reservation.data.get('user_id') != uid:
        raise DocumentConflict('HOSTED_IMPORT_STAGING_RESERVATION_REQUIRED')
    account = tx.get(('users', uid))
    if (account is None or account.data.get('status', 'active') != 'active'
            or account.data.get('_bm_erasure_operation')
            or tx.get(('account_erasure_locks', uid)) is not None):
        raise DocumentConflict('HOSTED_ACCOUNT_ERASURE_PENDING')
    return True
