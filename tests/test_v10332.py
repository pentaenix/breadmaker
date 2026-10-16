from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
CODES=('en','es','fr','it','ja','pt-BR','zh-CN')

def test_v10332_auth_copy_lives_only_in_existing_breadmaker_locale_catalogs():
    required={'auth.login.title','auth.login.email_placeholder','auth.login.password_placeholder','auth.login.trouble','auth.login.submit','auth.login.or_social','auth.login.no_account','auth.signup.action','auth.signup.title','auth.signup.submit','auth.signup.or_social','auth.signup.have_account','auth.login.action','auth.recovery.title','auth.recovery.continue','auth.recovery.back','auth.privacy_policy','auth.login.error_generic'}
    for code in CODES:
        payload=json.loads((ROOT/'locales'/f'{code}.json').read_text(encoding='utf-8'))
        assert required <= set(payload['strings'])
        assert not any(key.startswith('auth.preview.') for key in payload['strings'])
    source=(ROOT.parent/'breadmaker-cloud/scripts/auth_locale_source.py').read_text(encoding='utf-8')
    assert 'auth.login.error_generic' in source and 'auth.preview.' not in source

def test_v10332_preintegration_preview_artifacts_are_gone():
    assert not (ROOT/'run-auth-ui-preview').exists()
    assert not (ROOT/'static/dev/auth-ui-preview.html').exists()
    assert not (ROOT/'static/dev/auth-ui-preview.js').exists()

def test_v10332_release_plan_and_schema_remain_synchronized():
    version=(ROOT/'VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (10,3,32)
    assert 'APP_SCHEMA_VERSION = 42' in (ROOT/'src/workspace/constants.py').read_text(encoding='utf-8')
