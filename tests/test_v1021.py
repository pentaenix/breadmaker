from __future__ import annotations

import sys
import typing
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_v1021_release_identity_and_route_annotations_resolve(app_module_fixture):
    assert (ROOT/'VERSION').read_text(encoding='utf-8').strip()>='10.2.1'
    failures=[]
    for route in app_module_fixture.app.routes:
        endpoint=getattr(route,'endpoint',None)
        if not callable(endpoint):
            continue
        module=sys.modules.get(getattr(endpoint,'__module__',''))
        if module is None:
            continue
        try:
            typing.get_type_hints(endpoint,globalns=vars(module),localns=vars(module))
        except Exception as exc:
            failures.append((getattr(route,'path',''),endpoint.__module__,endpoint.__name__,type(exc).__name__,str(exc)))
    assert failures==[]


def test_v1021_google_oauth_request_model_has_canonical_feature_owner():
    from api.durability.backup_destinations import GoogleDriveOAuthStart
    fields=getattr(GoogleDriveOAuthStart,'model_fields',None) or getattr(GoogleDriveOAuthStart,'__fields__',{})
    assert {'client_id','client_secret'} <= set(fields)
