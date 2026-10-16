from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from auth.identity_platform import (
    IdentityPlatformAdmissionStore,
    IdentityPlatformError,
    IdentityPlatformSessionService,
    csrf_matches,
)
from hosted.firestore.memory import MemoryDocumentStore
from hosted.runtime_services import HostedCloudAccountIdentityService

ROOT=Path(__file__).resolve().parents[1]


class _FakeAdmin:
    def __init__(self,claims):
        self.claims=dict(claims);self.calls=[];self.revoked=[]
    def verify_id_token(self,token,check_revoked=False):
        self.calls.append(('id',token,check_revoked));return dict(self.claims)
    def create_session_cookie(self,token,expires_in):
        self.calls.append(('create',token,int(expires_in.total_seconds())))
        return 'provider-session-cookie'
    def verify_session_cookie(self,cookie,check_revoked=False):
        self.calls.append(('session',cookie,check_revoked))
        return {**self.claims,'iat':990.0,'exp':2000.0}
    def revoke_refresh_tokens(self,uid):
        self.revoked.append(uid)


def _seed_invite(store,token,*,expires=2000.0):
    path=IdentityPlatformAdmissionStore.invitation_path(token)
    store.run_transaction(lambda tx:tx.create(path,{'status':'active','expires_at_epoch':expires}))


def _service(*,provider='google.com',verified=True):
    docs=MemoryDocumentStore();accounts=HostedCloudAccountIdentityService(docs)
    claims={
        'uid':'external-uid-1','sub':'external-uid-1','auth_time':995.0,
        'email':'writer@example.invalid','email_verified':verified,'name':'Writer',
        'firebase':{'sign_in_provider':provider},
    }
    admin=_FakeAdmin(claims);admission=IdentityPlatformAdmissionStore(docs,now=lambda:1000.0)
    service=IdentityPlatformSessionService(admin,accounts,admission,project_id='breadmaker-staging',environment='test',now=lambda:1000.0)
    return docs,accounts,admin,service


def test_v10358_identity_platform_exchange_requires_invite_then_binds_internal_user(monkeypatch):
    monkeypatch.setenv('BREADMAKER_SESSION_COOKIE_SECURE','0')
    docs,accounts,admin,service=_service()
    with pytest.raises(IdentityPlatformError,match='PRIVATE_ALPHA_ADMISSION_REQUIRED'):
        service.exchange_id_token('fresh-id-token')
    _seed_invite(docs,'invite-1')
    exchanged=service.exchange_id_token('fresh-id-token',invitation_token='invite-1')
    assert exchanged.cookie=='provider-session-cookie'
    assert exchanged.principal.user_id.startswith('usr_')
    assert accounts.resolve_external_identity('identity-platform://breadmaker-staging','external-uid-1').user_id==exchanged.principal.user_id
    assert service.admission.is_admitted('external-uid-1') is True
    invite=docs.get(IdentityPlatformAdmissionStore.invitation_path('invite-1'))
    assert invite.data['status']=='consumed' and invite.data['used_by_uid']=='external-uid-1'
    with service.bind(exchanged.cookie) as principal:
        assert principal.user_id==exchanged.principal.user_id
    assert ('id','fresh-id-token',True) in admin.calls
    assert ('session','provider-session-cookie',True) in admin.calls


def test_v10358_identity_platform_rejects_stale_auth_and_unverified_password(monkeypatch):
    monkeypatch.setenv('BREADMAKER_SESSION_COOKIE_SECURE','0')
    docs,_accounts,admin,service=_service(provider='password',verified=False)
    _seed_invite(docs,'invite-password')
    with pytest.raises(IdentityPlatformError,match='VERIFIED_EMAIL_REQUIRED'):
        service.exchange_id_token('id-token',invitation_token='invite-password')
    admin.claims['email_verified']=True;admin.claims['auth_time']=600.0
    with pytest.raises(IdentityPlatformError,match='RECENT_AUTH_REQUIRED'):
        service.exchange_id_token('id-token',invitation_token='invite-password')


def test_v10358_identity_platform_global_revocation_and_csrf(monkeypatch):
    monkeypatch.setenv('BREADMAKER_SESSION_COOKIE_SECURE','0')
    docs,_accounts,admin,service=_service()
    _seed_invite(docs,'invite-2')
    exchanged=service.exchange_id_token('id-token',invitation_token='invite-2')
    assert service.revoke_user(exchanged.principal.user_id)==1
    assert admin.revoked==['external-uid-1']
    assert csrf_matches('abc','abc') is True
    assert csrf_matches('abc','abd') is False
    assert csrf_matches('', '') is False
    metadata=service.metadata()
    assert metadata['check_revoked'] is True and metadata['provider_tokens_persisted'] is False


def test_v10358_m7a_runtime_and_privacy_reset_boundaries_are_explicit():
    assert tuple(map(int, (ROOT/'VERSION').read_text(encoding='utf-8').strip().split("."))) >= (10, 3, 58)
    hosted=(ROOT/'src'/'runtime'/'hosted.py').read_text(encoding='utf-8')
    http_auth=(ROOT/'src'/'hosted'/'http_auth.py').read_text(encoding='utf-8')
    destructive=(ROOT/'src'/'api'/'project_management'/'destructive_actions.py').read_text(encoding='utf-8')
    context=(ROOT/'src'/'storage'/'context.py').read_text(encoding='utf-8')
    local=(ROOT/'src'/'runtime'/'local.py').read_text(encoding='utf-8')
    local_requirements=(ROOT/'requirements.txt').read_text(encoding='utf-8')
    hosted_requirements=(ROOT/'requirements-hosted.txt').read_text(encoding='utf-8')
    assert "auth_mode!='identity_platform'" in hosted
    assert 'BREADMAKER_IDENTITY_PLATFORM_ENABLED' not in hosted
    assert 'auth0' not in hosted.lower()
    assert '/api/auth/session/exchange' in http_auth and '/api/auth/csrf' in http_auth
    assert 'storage_context.clear_project()' in destructive
    assert 'def clear_project()' in context
    assert 'identity_platform' not in local and 'firebase-admin' not in local_requirements
    assert 'firebase-admin==7.7.0' in hosted_requirements
