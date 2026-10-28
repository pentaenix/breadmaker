from __future__ import annotations

"""Cloud-native hosted runtime composition.

Milestone 5 removes SQLite/project-directory persistence from hosted modes. Local
Breadmaker remains composed by ``runtime.local``; hosted modes compose exclusively from
Firestore/GCS adapters (or their deterministic in-memory fakes in hosted_dev).
"""

import os
from typing import Any

from auth.accounts import NullAccountIdentityService
from auth.sessions import AuthenticationError, NullSessionService, build_hosted_session_service
from auth.identity_platform import build_identity_platform_session_service
from hosted.firestore import HostedProjectRepository as FirestoreProjectRepository, HostedStructuredRepository, HostedUserRepository, MemoryDocumentStore
from hosted.firestore.google_driver import GoogleFirestoreDocumentStore
from hosted.runtime_services import (
    HostedAuthorizationService,
    HostedBackupStore,
    HostedCapabilityProvider,
    HostedCloudAccountIdentityService,
    HostedCloudProjectRepository,
    HostedCommittedAssetStore,
    HostedHistoryStore,
    HostedIdentityProvider,
    HostedProjectContextProvider,
    HostedProjectUsageService,
    _DEV_USER_ID,
    ensure_hosted_user,
)
from hosted.storage import MemoryObjectStore
from hosted.storage.google_driver import GoogleCloudObjectStore
from hosted.action_journal import ActionDocumentStore
from hosted.account_lifecycle import AccountLifecycleDocumentStore, AccountLifecycleObjectStore
from runtime.contracts import RuntimeServices

def _validate_worker_configuration() -> None:
    """Validate deployment configuration, never infer security from worker count.

    Firestore/GCS and provider-signed cookies are authoritative across replicas.
    One worker per container is still a reasonable cost default, not a lock.
    """
    for key in ('WEB_CONCURRENCY', 'UVICORN_WORKERS', 'BREADMAKER_WORKERS'):
        raw = str(os.environ.get(key) or '').strip()
        if not raw:
            continue
        try:
            count = int(raw)
        except ValueError as exc:
            raise RuntimeError(f'HOSTED_WORKER_COUNT_INVALID:{key}') from exc
        if not 1 <= count <= 64:
            raise RuntimeError(f'HOSTED_WORKER_COUNT_INVALID:{key}')


# request_user_id authority is implemented by HostedIdentityProvider and is server-bound.

_HOSTED_CAPABILITIES={
    'offline':False,
    'local_profiles':False,
    'native_filesystem':False,
    'portable_project_export':True,
    'removable_backups':False,
    'direct_remote_backups':False,
    'request_scoped_projects':True,
    'request_scoped_actors':True,
    'request_scoped_recovery':True,
    'project_scoped_editor_leases':True,
    'hosted_projects':True,
    'hosted_persistence':True,
    'developer_identity':True,
    'account_sessions':False,
    'test_auth_bootstrap':False,
    'account_authentication':False,
    'hosted_collaboration':False,
    'realtime_collaboration':False,
}


def _validate_mode(runtime_mode: str, auth_mode: str, environment: str) -> None:
    if runtime_mode == 'hosted':
        if environment not in {'staging','production'}:raise RuntimeError('HOSTED_RUNTIME_ENVIRONMENT_REQUIRED')
        if auth_mode!='identity_platform':raise RuntimeError('HOSTED_RUNTIME_IDENTITY_PLATFORM_REQUIRED')
        origin=str(os.environ.get('BREADMAKER_PUBLIC_ORIGIN') or '').strip()
        if not origin.startswith('https://'):raise RuntimeError('HOSTED_RUNTIME_HTTPS_ORIGIN_REQUIRED')
        if str(os.environ.get('BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED') or ''):raise RuntimeError('HOSTED_RUNTIME_DEV_BYPASS_FORBIDDEN')
        if str(os.environ.get('BREADMAKER_ENFORCE_PUBLIC_HOST') or '').strip().lower() not in {'1','true','yes'}:raise RuntimeError('HOSTED_RUNTIME_PUBLIC_HOST_PIN_REQUIRED')
    else:
        if auth_mode=='developer' and environment=='production':raise RuntimeError('HOSTED_DEV_UNAUTHENTICATED_FORBIDDEN_IN_PRODUCTION')
        if auth_mode=='developer' and str(os.environ.get('BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED') or '')!='1':raise RuntimeError('HOSTED_DEV_UNAUTHENTICATED_DISABLED')
        if auth_mode=='identity_platform' and environment not in {'development','test'}:raise RuntimeError('HOSTED_DEV_IDENTITY_PLATFORM_DEVELOPMENT_ONLY')


def _stores(runtime_mode: str, documents=None, objects=None):
    if documents is not None or objects is not None:
        if runtime_mode != 'hosted_dev' or documents is None or objects is None:
            raise RuntimeError('HOSTED_STORE_INJECTION_DEVELOPMENT_ONLY')
        return documents, objects
    if runtime_mode == 'hosted_dev':
        return MemoryDocumentStore(), MemoryObjectStore()
    project=str(os.environ.get('BREADMAKER_GCP_PROJECT_ID') or os.environ.get('BREADMAKER_GCP_PROJECT') or os.environ.get('GOOGLE_CLOUD_PROJECT') or '').strip() or None
    database=str(os.environ.get('BREADMAKER_FIRESTORE_DATABASE') or '').strip() or None
    bucket=str(os.environ.get('BREADMAKER_STORAGE_BUCKET') or '').strip()
    if not bucket:raise RuntimeError('HOSTED_STORAGE_BUCKET_REQUIRED')
    return GoogleFirestoreDocumentStore(project=project,database=database), GoogleCloudObjectStore(bucket=bucket,project=project)


def _build_hosted_runtime(_workspace=None, *, runtime_mode: str, documents=None, objects=None) -> RuntimeServices:
    auth_mode=str(os.environ.get('BREADMAKER_AUTH_MODE') or ('developer' if runtime_mode=='hosted_dev' else 'identity_platform')).strip().lower()
    environment=str(os.environ.get('BREADMAKER_ENV') or ('development' if runtime_mode=='hosted_dev' else '')).strip().lower()
    _validate_mode(runtime_mode,auth_mode,environment)
    _validate_worker_configuration()

    documents,objects=_stores(runtime_mode, documents, objects)
    lifecycle = None
    if isinstance(documents, GoogleFirestoreDocumentStore):
        lifecycle = AccountLifecycleDocumentStore(documents)
        documents = lifecycle
    elif isinstance(documents, AccountLifecycleDocumentStore):
        # Explicit deterministic integration injection; ordinary local runtime
        # and the historical memory-only purge rehearsals stay unchanged.
        lifecycle = documents
    if lifecycle is not None:
        objects = AccountLifecycleObjectStore(objects, lifecycle)
    documents=ActionDocumentStore(documents)
    project_authority=FirestoreProjectRepository(documents)
    users=HostedUserRepository(HostedStructuredRepository(documents))

    if auth_mode=='developer':
        display_name=str(os.environ.get('BREADMAKER_HOSTED_DEV_NAME') or 'Developer').strip()[:160] or 'Developer'
        configured=str(os.environ.get('BREADMAKER_HOSTED_DEV_USER_ID') or _DEV_USER_ID).strip()
        profile=ensure_hosted_user(documents,user_id=configured,display_name=display_name)
        dev_uid=str(profile['user_id'])
        sessions=NullSessionService()
        accounts=NullAccountIdentityService()
        def user_id_provider():return dev_uid
        identity_kind='hosted_dev'
    elif auth_mode in {'test','session','identity_platform'}:
        if runtime_mode=='hosted' and auth_mode!='identity_platform':raise RuntimeError('HOSTED_RUNTIME_IDENTITY_PLATFORM_REQUIRED')
        accounts=HostedCloudAccountIdentityService(documents)
        if auth_mode=='identity_platform':
            project_id=str(os.environ.get('BREADMAKER_GCP_PROJECT_ID') or os.environ.get('BREADMAKER_GCP_PROJECT') or os.environ.get('GOOGLE_CLOUD_PROJECT') or '').strip()
            if not project_id:raise RuntimeError('IDENTITY_PLATFORM_PROJECT_REQUIRED')
            sessions=build_identity_platform_session_service(documents,accounts,environment=environment,project_id=project_id)
        else:
            sessions=build_hosted_session_service(auth_mode)
        if runtime_mode=='hosted' and not bool(sessions.metadata().get('cookie_secure')):raise RuntimeError('HOSTED_RUNTIME_SECURE_COOKIE_REQUIRED')
        def user_id_provider():
            principal=sessions.current()
            if principal is None:raise AuthenticationError('AUTHENTICATION_REQUIRED')
            return str(principal.user_id)
        identity_kind='account'
    else:
        raise RuntimeError(f'AUTH_MODE_UNSUPPORTED:{auth_mode}')

    if lifecycle is not None:
        lifecycle.actor = (lambda: dev_uid) if auth_mode == 'developer' else (
            lambda: sessions.current().user_id if sessions.current() is not None else None)

    context=HostedProjectContextProvider(project_authority,users,user_id_provider,kind=identity_kind)
    identity=HostedIdentityProvider(documents,objects,project_authority,context,user_id_provider,kind=identity_kind)
    projects=HostedCloudProjectRepository(documents,objects,project_authority,users,user_id_provider)
    context.project_target_provider=projects.profile_project_target

    def current_project_id():
        current=context.current()
        if current is not None and current.project_id:return current.project_id
        target=projects.profile_project_target(user_id_provider())
        if not target:raise RuntimeError('PROJECT_CONTEXT_REQUIRED')
        return target

    assets=HostedCommittedAssetStore(documents,objects,user_id_provider,current_project_id)
    history=HostedHistoryStore(documents,objects,user_id_provider,current_project_id)
    backups=HostedBackupStore()
    usage=HostedProjectUsageService(documents,objects)
    capabilities=dict(_HOSTED_CAPABILITIES)
    capabilities['account_erasure_fence'] = lifecycle is not None
    capabilities['developer_identity']=auth_mode=='developer'
    capabilities['account_sessions']=auth_mode in {'test','session','identity_platform'}
    capabilities['account_authentication']=auth_mode=='identity_platform'
    capabilities['test_auth_bootstrap']=auth_mode=='test'
    capability_provider=HostedCapabilityProvider(
        runtime_mode,auth_mode,capabilities,
        {
            'persistence':'firestore_gcs',
            'identity':'developer_fixed' if auth_mode=='developer' else 'server_session',
            'auth_mode':auth_mode,
            'runtime_mode':runtime_mode,
            'environment':environment,
            'production_ready':environment=='production',
            'single_worker_required':False,
            'cross_instance_authority':'firestore_gcs_identity_platform',
            'persistent_server_filesystem':False,
            'sqlite':False,
        },
    )
    # The absence of native bridges is deliberate and is the M5 no-filesystem gate.
    return RuntimeServices(
        mode=runtime_mode,
        context=context,
        identity=identity,
        authorization=HostedAuthorizationService(identity),
        projects=projects,
        assets=assets,
        history=history,
        backups=backups,
        usage=usage,
        capabilities=capability_provider,
        sessions=sessions,
        accounts=accounts,
        native_projects=None,
        native_assets=None,
        native_history=None,
        native_backups=None,
    )


def build_hosted_dev_runtime(workspace=None, *, documents=None, objects=None) -> RuntimeServices:
    return _build_hosted_runtime(workspace,runtime_mode='hosted_dev',documents=documents,objects=objects)


def build_hosted_runtime(workspace=None) -> RuntimeServices:
    return _build_hosted_runtime(workspace,runtime_mode='hosted')
