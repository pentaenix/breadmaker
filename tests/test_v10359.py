from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from auth.identity_platform import IdentityPlatformAdmissionStore, IdentityPlatformError, IdentityPlatformSessionService
from auth.identity_platform_security import IdentityPlatformSecurityService
from hosted.firestore.memory import MemoryDocumentStore
from hosted.runtime_services import HostedCloudAccountIdentityService

ROOT=Path(__file__).resolve().parents[1]


class _Admin:
    def __init__(self, claims, user):
        self.claims=dict(claims);self.user=user;self.revoked=[]
    def verify_id_token(self, _token, check_revoked=False):
        assert check_revoked is True;return dict(self.claims)
    def create_session_cookie(self, _token, expires_in):
        assert int(expires_in.total_seconds())>0;return 'm7b-provider-cookie'
    def verify_session_cookie(self, _cookie, check_revoked=False):
        assert check_revoked is True;return {**self.claims,'iat':990.0,'exp':1800.0}
    def revoke_refresh_tokens(self, uid):self.revoked.append(uid)
    def get_user(self, uid):
        assert uid==self.claims['uid'];return self.user


def _factor(uid='totp-1'):
    return SimpleNamespace(factor_id='totp',uid=uid,display_name='Breadmaker authenticator',enrollment_time='now')


def _provider(provider_id, email='writer@example.invalid'):
    return SimpleNamespace(provider_id=provider_id,uid='provider-user',email=email,display_name='Writer')


def _user(*, providers=('google.com',), totp=False, verified=True):
    return SimpleNamespace(
        uid='external-m7b',disabled=False,email='writer@example.invalid',email_verified=verified,
        display_name='Writer',provider_data=[_provider(item) for item in providers],
        multi_factor=SimpleNamespace(enrolled_factors=[_factor()] if totp else []),
    )


def _service(*, provider='google.com', user=None):
    docs=MemoryDocumentStore();accounts=HostedCloudAccountIdentityService(docs)
    claims={
        'uid':'external-m7b','sub':'external-m7b','auth_time':995.0,
        'email':'writer@example.invalid','email_verified':True,'name':'Writer',
        'firebase':{'sign_in_provider':provider},
    }
    admin=_Admin(claims,user or _user(providers=(provider,),totp=provider=='password'))
    admission=IdentityPlatformAdmissionStore(docs,now=lambda:1000.0)
    service=IdentityPlatformSessionService(admin,accounts,admission,project_id='breadmaker-staging',environment='test',now=lambda:1000.0)
    path=IdentityPlatformAdmissionStore.invitation_path('m7b-invite')
    docs.run_transaction(lambda tx:tx.create(path,{'status':'active','expires_at_epoch':2000.0}))
    return docs,accounts,admin,service


def test_v10359_password_exchange_requires_verified_email_and_enrolled_totp(monkeypatch):
    monkeypatch.setenv('BREADMAKER_SESSION_COOKIE_SECURE','0')
    user=_user(providers=('password',),totp=False,verified=True)
    _docs,_accounts,admin,service=_service(provider='password',user=user)
    with pytest.raises(IdentityPlatformError,match='TOTP_ENROLLMENT_REQUIRED'):
        service.exchange_id_token('fresh-password-token',invitation_token='m7b-invite')
    user.multi_factor.enrolled_factors=[_factor()]
    exchange=service.exchange_id_token('fresh-password-token',invitation_token='m7b-invite')
    assert exchange.provider=='email_password' and exchange.principal.user_id.startswith('usr_')
    assert admin.claims['email_verified'] is True


def test_python_admin_record_totp_metadata_is_authoritative(monkeypatch):
    from firebase_admin.auth import UserRecord
    monkeypatch.setenv('BREADMAKER_SESSION_COOKIE_SECURE','0')
    record={'localId':'external-m7b','emailVerified':True,'providerUserInfo':[{'providerId':'password'}]}
    _docs,accounts,admin,sessions=_service(provider='password',user=UserRecord(record))
    security=IdentityPlatformSecurityService(sessions,accounts,now=lambda:1000.0)
    for factors in [[],[{'mfaEnrollmentId':'phone','phoneInfo':'+15555550100'}],
                    [{'mfaEnrollmentId':'bad','totpInfo':None}],[{'totpInfo':{}}]]:
        admin.user=UserRecord({**record,'mfaInfo':factors})
        with pytest.raises(IdentityPlatformError,match='TOTP_ENROLLMENT_REQUIRED'):
            sessions.exchange_id_token('fresh-password-token',invitation_token='m7b-invite')
        assert security._factors(admin.user)==[]
    admin.user=UserRecord({**record,'mfaInfo':[{'mfaEnrollmentId':'totp-live','totpInfo':{},'displayName':'Authenticator'}]})
    exchange=sessions.exchange_id_token('fresh-password-token',invitation_token='m7b-invite')
    assert exchange.provider=='email_password'
    assert security._factors(admin.user)==[{'id':'totp-live','type':'totp','display_name':'Authenticator','enrollment_time':''}]


def test_v10359_fresh_security_proof_is_bound_to_same_external_uid(monkeypatch):
    monkeypatch.setenv('BREADMAKER_SESSION_COOKIE_SECURE','0')
    _docs,accounts,admin,sessions=_service(user=_user(providers=('google.com','password'),totp=True))
    exchange=sessions.exchange_id_token('google-token',invitation_token='m7b-invite')
    security=IdentityPlatformSecurityService(sessions,accounts,now=lambda:1000.0)
    with sessions.bind(exchange.cookie) as principal:
        snapshot=security.snapshot(principal)
        assert snapshot['auth_mode']=='identity_platform'
        assert snapshot['email_is_authority'] is False
        assert snapshot['mfa']['totp_enrolled'] is True
        assert snapshot['mfa']['totp_mandatory'] is True
        assert next(row for row in snapshot['identities'] if row['provider']=='google')['primary'] is True
        proof=security.verify_fresh_proof(principal,'fresh-proof',purpose='link_identity')
        assert proof.external_uid=='external-m7b'
        admin.claims['uid']=admin.claims['sub']='different-user'
        with pytest.raises(IdentityPlatformError,match='IDENTITY_PLATFORM_REAUTH_USER_MISMATCH'):
            security.verify_fresh_proof(principal,'wrong-user-proof',purpose='link_identity')


def test_v10359_post_mutation_policy_never_accepts_password_without_totp(monkeypatch):
    monkeypatch.setenv('BREADMAKER_SESSION_COOKIE_SECURE','0')
    user=_user(providers=('google.com',),totp=False)
    _docs,accounts,admin,sessions=_service(user=user)
    exchange=sessions.exchange_id_token('google-token',invitation_token='m7b-invite')
    security=IdentityPlatformSecurityService(sessions,accounts,now=lambda:1000.0)
    with sessions.bind(exchange.cookie) as principal:
        user.provider_data=[_provider('google.com'),_provider('password')]
        with pytest.raises(IdentityPlatformError,match='TOTP_ENROLLMENT_REQUIRED'):
            security.sync_after_provider_mutation(principal,'fresh-proof',purpose='link_identity')
        user.multi_factor.enrolled_factors=[_factor()]
        snapshot=security.sync_after_provider_mutation(principal,'fresh-proof',purpose='link_identity')
        assert snapshot['mfa']['totp_mandatory'] is True


def test_v10359_m7b_web_and_provider_neutral_security_boundaries_are_explicit():
    assert tuple(map(int, (ROOT/'VERSION').read_text(encoding='utf-8').strip().split("."))) >= (10, 3, 59)
    client=(ROOT/'static/js/features/account/identity-platform-client.js').read_text(encoding='utf-8')
    login=(ROOT/'static/js/features/account/identity-platform.js').read_text(encoding='utf-8')
    security=(ROOT/'static/js/features/account/security.js').read_text(encoding='utf-8')
    auth=(ROOT/'src/hosted/http_auth.py').read_text(encoding='utf-8')
    pipeline=(ROOT/'src/backend/request_pipeline.py').read_text(encoding='utf-8')
    boundary=(ROOT/'src/backend/http_boundary.py').read_text(encoding='utf-8')
    local=(ROOT/'src/runtime/local.py').read_text(encoding='utf-8')
    assert 'inMemoryPersistence' in client and 'indexedDBLocalPersistence' not in client and 'browserLocalPersistence' not in client
    assert 'signOutClient' in client and 'accounts:signUp' in client and 'linkWithPopup' in client
    assert 'TotpMultiFactorGenerator.generateSecret' in client and 'getMultiFactorResolver' in client
    assert 'requirePasswordTotp' in login and 'requireVerifiedEmail' in login
    assert 'accountSecurityWithIdentityProof' in security and 'sign_out_all_sessions' in security
    assert '/api/auth/client-config' in auth and '/api/auth/security/proof' in auth and '/api/auth/security/sync' in auth
    assert '/api/auth/client-config' in pipeline and '/api/auth/session/exchange' in pipeline
    assert 'https://www.gstatic.com' in boundary and 'https://securetoken.googleapis.com' in boundary
    assert 'identity_platform' not in local


def test_v10359_account_security_never_persists_provider_proof_material():
    source=(ROOT/'src/auth/identity_platform_security.py').read_text(encoding='utf-8').lower()
    browser=(ROOT/'static/js/features/account/identity-platform-client.js').read_text(encoding='utf-8').lower()
    forbidden=['refresh_token =','access_token =','totp_secret =','recovery_code =','password =']
    for marker in forbidden:
        assert marker not in source
    assert 'provider_tokens_persisted' in source and 'false' in source
    assert 'localstorage' not in browser and 'sessionstorage' not in browser and 'inmemorypersistence' in browser
