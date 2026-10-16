from __future__ import annotations
from source_contracts import frontend_source_from_app_path

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCALES = ROOT / 'locales'
APP = frontend_source_from_app_path(ROOT / 'static' / 'app.js')


def _locale_strings() -> dict[str, dict[str, str]]:
    return {
        path.stem: json.loads(path.read_text(encoding='utf-8'))['strings']
        for path in sorted(LOCALES.glob('*.json'))
    }


def test_v7751_all_literal_runtime_i18n_keys_exist_in_every_locale():
    # Literal runtime lookups are easy to miss because locale key-set parity alone
    # cannot detect a key omitted from *every* locale at once.
    keys = set(re.findall(r"i18nText\(\s*['\"]([^'\"]+)['\"]", APP))
    # Retention select labels are routed through option(..., label) before i18nText.
    keys.update(re.findall(r"option\([^,]+,[^,]+,['\"]([^'\"]+)['\"]", APP))
    locales = _locale_strings()
    assert locales
    missing = {code: sorted(keys - set(strings)) for code, strings in locales.items()}
    assert not any(missing.values()), missing


def test_v7751_missing_runtime_translation_cannot_self_trigger_mutation_observer():
    # Once a locale is installed, a missing key degrades to a visible key name rather
    # than another {{i18n:...}} marker. DOM bindings also avoid no-op writes, so a
    # missing translation can never create an observer feedback loop.
    assert "if(!i18nReady)return `{{i18n:${key}}}`" in APP
    assert 'return String(key);' in APP
    assert 'if(node.nodeValue!==resolved)node.nodeValue=resolved' in APP
    assert 'if(node.getAttribute(attr)!==resolved)node.setAttribute(attr,resolved)' in APP


def test_v7751_recovery_scheduling_runtime_copy_is_translated():
    required = {
        'backup.destinations_update_all', 'backup.destinations_update_queued',
        'backup.retention_configure', 'backup.retention_title',
        'backup.retention_history', 'backup.retention_30_days',
        'backup.retention_90_days', 'backup.retention_1_year',
        'backup.retention_forever', 'backup.retention_history_help',
        'backup.retention_recovery', 'backup.retention_compact',
        'backup.retention_standard', 'backup.retention_extended',
        'backup.retention_recovery_help', 'backup.retention_saved',
        'recovery.boot_title',
    }
    for code, strings in _locale_strings().items():
        assert not (required - set(strings)), code
        assert all(str(strings[key]).strip() for key in required), code
