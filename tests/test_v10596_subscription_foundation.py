"""Provider-neutral preparation must never activate billing or extend access."""
from datetime import datetime, timedelta, timezone

import pytest
from hosted.billing import BillingUnavailable, DisabledBillingService, PaidPeriod

START = datetime(2026, 1, 31, tzinfo=timezone.utc)
END = datetime(2026, 2, 28, tzinfo=timezone.utc)


@pytest.mark.parametrize('canceled', [False, True])
@pytest.mark.parametrize('instant,allowed', [(START-timedelta(microseconds=1), False),
    (START, True), (END-timedelta(microseconds=1), True), (END, False),
    (END+timedelta(days=1), False)])
def test_paid_boundary_preserves_canceled_remainder(canceled, instant, allowed):
    period = PaidPeriod('owner', START, END, canceled)
    assert period.allows_paid_access(authenticated_account_id='owner', now=instant) is allowed
    assert not period.allows_paid_access(authenticated_account_id='other', now=instant)


def test_revocation_and_timezone():
    revoked = START + timedelta(days=10)
    period = PaidPeriod('owner', START, END, True, revoked)
    assert period.allows_paid_access(authenticated_account_id='owner', now=revoked-timedelta(microseconds=1))
    assert not period.allows_paid_access(authenticated_account_id='owner', now=revoked)
    assert not period.allows_paid_access(authenticated_account_id='owner', now=END.astimezone(timezone(timedelta(hours=-6))))


@pytest.mark.parametrize('kwargs', [dict(account_id=''), dict(paid_until=START),
    dict(paid_until=START-timedelta(days=1)), dict(paid_from=START.replace(tzinfo=None)),
    dict(paid_until=END.replace(tzinfo=None)), dict(revoked_at=START.replace(tzinfo=None)),
    dict(cancel_at_period_end='false')])
def test_invalid_records_rejected(kwargs):
    args = dict(account_id='owner', paid_from=START, paid_until=END)
    args.update(kwargs)
    with pytest.raises(ValueError):
        PaidPeriod(**args)


def test_naive_clock_rejected():
    with pytest.raises(ValueError):
        PaidPeriod('owner', START, END).allows_paid_access(authenticated_account_id='owner', now=START.replace(tzinfo=None))


@pytest.mark.parametrize('method', ['checkout', 'portal', 'cancel_at_period_end'])
def test_disabled_mutations_cannot_activate(method, monkeypatch):
    monkeypatch.setenv('BREADMAKER_BILLING_ENABLED', 'true')
    with pytest.raises(BillingUnavailable, match='BILLING_NOT_CONNECTED'):
        getattr(DisabledBillingService(), method)(authenticated_account_id='owner')


def test_snapshot_is_unknown_not_free_or_zero_and_contains_no_identity():
    service = DisabledBillingService()
    one = service.account_snapshot(authenticated_account_id='owner')
    assert one == service.account_snapshot(authenticated_account_id='another')
    assert one['plan'] is None and one['paid_until'] is None
    assert not any(one[k] for k in ('connected', 'can_checkout', 'can_manage', 'can_cancel'))
    one['connected'] = True
    assert service.account_snapshot(authenticated_account_id='owner')['connected'] is False
    assert service.dashboard_snapshot() == {'schema_version': 1, 'source': 'unconnected',
        'connected': False, 'observed_at': None, 'coverage': None, 'currencies': []}
    with pytest.raises(ValueError):
        service.account_snapshot(authenticated_account_id='')


def test_authenticated_account_snapshot_uses_server_principal():
    # Execute the actual route body with isolated collaborators rather than
    # initializing real Identity Platform or cloud infrastructure.
    import ast
    from pathlib import Path
    from types import SimpleNamespace
    from fastapi import HTTPException
    from fastapi.responses import JSONResponse
    import json
    tree = ast.parse((Path(__file__).parents[1]/'src/hosted/http_auth.py').read_text())
    route = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                 and node.name == 'identity_platform_security_snapshot')
    route.decorator_list = []
    session = SimpleNamespace(current=lambda: None)
    calls = []
    class IdentityError(Exception):
        pass
    scope = dict(runtime=SimpleNamespace(sessions=session), HTTPException=HTTPException,
        JSONResponse=JSONResponse, IdentityPlatformError=IdentityError,
        DisabledBillingService=DisabledBillingService,
        _IP_SECURITY=SimpleNamespace(snapshot=lambda principal: calls.append(principal.user_id) or {}),
        _ip_client_config=lambda: {}, _ip_no_store_headers=lambda: {'Cache-Control': 'no-store'})
    exec(compile(ast.Module(body=[route], type_ignores=[]), '<actual-account-route>', 'exec'), scope)
    invoke = scope['identity_platform_security_snapshot']
    with pytest.raises(HTTPException) as denied:
        invoke()
    assert denied.value.status_code == 401 and calls == []
    session.current = lambda: SimpleNamespace(user_id='server-owner')
    response = invoke()
    assert calls == ['server-owner']
    assert response.headers['cache-control'] == 'no-store'
    assert json.loads(response.body)['billing']['connected'] is False
