"""Explicit app-side maintenance activation, independent of ordinary user auth."""
from datetime import datetime, timezone
import time

from hosted.firestore.contracts import DocumentConflict
from hosted.narration.gc import NarrationGC
from hosted.narration.gc_job import GoogleGCQueue, retention_allows, run_batch
from hosted.narration.io_limits import deadline
from hosted.narration.maintenance import CONTROL, NarrationMaintenanceStore
from hosted.narration.maintenance_http import MaintenanceOperation
from hosted.narration.maintenance_identity import MachineIdentity, PATH, origin

DAILY_BATCHES = 8


def reserve_batch(documents, *, now=time.time):
    period = datetime.fromtimestamp(now(), timezone.utc).strftime('%Y%m%d')
    path = (CONTROL, 'invocations')
    def reserve(tx):
        row = tx.get(path)
        if row is not None and (set(row.data) != {'v', 'period', 'attempts'} or type(row.data['v']) is not int or row.data['v'] != 1
                or not isinstance(row.data['period'], str) or len(row.data['period']) != 8
                or not row.data['period'].isascii() or not row.data['period'].isdigit()
                or type(row.data['attempts']) is not int or not 1 <= row.data['attempts'] <= DAILY_BATCHES):
            raise ValueError('NARRATION_GC_BUDGET_INVALID')
        if row is not None and row.data['period'] > period:
            raise DocumentConflict('NARRATION_GC_CLOCK_REVERSED')
        attempts = row.data['attempts'] if row is not None and row.data['period'] == period else 0
        if attempts >= DAILY_BATCHES:
            raise DocumentConflict('NARRATION_GC_DAILY_LIMIT')
        tx.set(path, dict(v=1, period=period, attempts=attempts + 1))
    documents.run_transaction(reserve)


def run_scoped_batch(provider, objects, queue, *, now=time.time):
    """No configurable targets/budgets; bounded failed attempts also count."""
    token = deadline.set(time.monotonic() + 40)
    try:
        documents = NarrationMaintenanceStore(provider)
        reserve_batch(documents, now=now)
        objects.verify_gc_bucket()  # prove no versioning/soft-delete/lifecycle first
        collector = NarrationGC(documents, objects,
            retention_allows=lambda tx, pid, owner: retention_allows(tx, pid, owner, now=now()), now=now)
        return run_batch(collector, queue, seconds=35, safety_margin=8)
    finally:
        deadline.reset(token)


def validate_configuration(env, *, project):
    flag = env.get('BREADMAKER_NARRATION_GC_ENABLED', '')
    if flag in ('', '0', 'false'):
        return None
    if flag != 'true':
        raise ValueError('NARRATION_GC_CONFIGURATION_REQUIRED')
    public = origin(env.get('BREADMAKER_PUBLIC_ORIGIN', ''))
    identity = MachineIdentity(audience=public + PATH,
        email=env.get('BREADMAKER_NARRATION_GC_SERVICE_ACCOUNT', ''),
        subject=env.get('BREADMAKER_NARRATION_GC_SERVICE_ACCOUNT_ID', ''))
    if identity.email != 'breadmaker-narration-gc@' + project + '.iam.gserviceaccount.com':
        raise ValueError('NARRATION_GC_PRINCIPAL_SCOPE_INVALID')
    return identity


def configured(env, *, project, objects, provider_factory=None):
    identity = validate_configuration(env, project=project)
    if identity is None:
        return None
    provider = None
    def run():
        nonlocal provider
        if provider is None:
            from hosted.narration.maintenance_google import MaintenanceGoogleStore
            provider = (provider_factory or MaintenanceGoogleStore)(project)
        return run_scoped_batch(provider, objects, GoogleGCQueue(provider._client))
    return MaintenanceOperation(identity, run)
