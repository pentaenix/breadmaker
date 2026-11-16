"""Explicit operator assembly from existing clients; creates no credentials.

Each invocation requires a whole-run budget and an exact project/bucket target.
Caller supplies an inspected account plan and uses the worker's resumable cursors.
No production flag, scheduler, policy initializer, content write or purge exists.
"""
from hosted.security.account_word_backfill_store import BoundedAccountWordStore,BackfillBudget,BackfillUnavailable
from hosted.security.account_word_content import BoundedCurrentBlockReader
from hosted.security.account_word_backfill import AccountWordBackfill
from hosted.storage.google_driver import GoogleCloudObjectStore


def operator_worker(client,objects,*,owner_user_id,expected_project,expected_bucket,budget):
    if (type(budget) is not BackfillBudget or type(objects) is not GoogleCloudObjectStore
            or type(expected_bucket) is not str or not expected_bucket
            or objects._bucket.name!=expected_bucket or objects._client.project!=expected_project):
        raise BackfillUnavailable('WORD_BACKFILL_OPERATOR_SCOPE')
    store=BoundedAccountWordStore(client,project_id=expected_project,owner_user_id=owner_user_id,budget=budget)
    return AccountWordBackfill(store,BoundedCurrentBlockReader(store,objects),enabled=True)
