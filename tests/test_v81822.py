from source_contracts import frontend_source_from_app_path
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def _slice(source,name):
    start=source.index(f"def {name}(")
    end=source.find("\ndef ",start+5)
    return source[start:] if end<0 else source[start:end]


def test_v81822_timeout_policy_keeps_core_at_ten_and_browser_at_fifteen():
    source=(ROOT/'tests'/'conftest.py').read_text(encoding='utf-8')
    assert 'CORE_API_TEST_TIMEOUT_SECONDS = 10.0' in source
    assert 'BROWSER_TEST_TIMEOUT_SECONDS = 15.0' in source
    policy=_slice(source,'_timeout_seconds_for_item')
    assert "name=='test_e2e.py'" in policy
    assert "name.endswith('_browser.py')" in policy
    assert 'return BROWSER_TEST_TIMEOUT_SECONDS' in policy
    assert 'return CORE_API_TEST_TIMEOUT_SECONDS' in policy
    verifier=(ROOT/'scripts'/'verify_release.py').read_text(encoding='utf-8')
    assert 'len(tests) * 17 + 120' in verifier


def test_v81822_v640_creation_contracts_are_split_and_have_no_fixed_sleeps():
    source=(ROOT/'tests'/'test_v640_browser.py').read_text(encoding='utf-8')
    station=_slice(source,'test_v640_station_creation_modal_names_description_links_and_enter')
    span=_slice(source,'test_v640_span_creation_modal_name_and_enter')
    assert 'test_v640_station_and_span_creation_modal_names_description_links_and_enter' not in source
    assert 'wait_for_timeout' not in station
    assert 'wait_for_timeout' not in span
    assert "get_by_test_id('map-add-station')" in station
    assert "get_by_test_id('map-add-span')" in span
    assert "#modal').wait_for(state='hidden')" in station
    assert "#modal').wait_for(state='hidden')" in span


def test_v81822_map_creation_actions_have_stable_automation_hooks():
    js=frontend_source_from_app_path(ROOT/'static'/'app.js')
    for hook in ('map-line-actions','map-add-station','map-add-span'):
        assert hook in js
