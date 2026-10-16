from __future__ import annotations
from source_contracts import frontend_source_from_app_path

import json
from pathlib import Path


def test_v763_release_metadata_backup_locations_and_google_visibility_contract():
    root = Path(__file__).resolve().parents[1]
    index = (root / 'static/index.html').read_text(encoding='utf-8')

    appjs = frontend_source_from_app_path(root / 'static/app.js')
    # Google Drive is a stable product choice; local OAuth setup affects
    # Connect, not whether writers can see the provider.
    assert "data-backup-kind=\"google_drive\"" in appjs
    assert "googleConfigured?" not in appjs
    assert "google-drive/config')).configured" not in appjs
    # The WebDAV connection surface is intentionally just the fields/actions.
    assert 'backup.destinations_remote_connect_help' not in appjs
    # Backup locations must precede Recovery Backups in the normal readable-project
    # Project Settings surface. Recovery-safe Project Settings intentionally has its
    # own earlier Recovery row so settings remain reachable when the active project
    # cannot be opened. Scope this frozen contract to the normal branch.
    normal_settings = appjs[appjs.index('if(!active)return;'):]
    assert normal_settings.index("projectSettingsRow('settingsBackups'") < normal_settings.index("projectSettingsRow('settingsRecovery'")


def test_v763_all_locales_use_backup_locations_and_removed_webdav_helper():
    root = Path(__file__).resolve().parents[1]
    expected = {
        'en': 'Backup locations',
        'es': 'Ubicaciones de respaldo',
        'fr': 'Emplacements de sauvegarde',
        'it': 'Posizioni di backup',
        'ja': 'バックアップ先',
        'pt-BR': 'Locais de backup',
        'zh-CN': '备份位置',
    }
    catalogs = [json.loads(path.read_text(encoding='utf-8')) for path in sorted((root / 'locales').glob('*.json'))]
    en = next(c for c in catalogs if c['code'] == 'en')
    keys = set(en['strings'])
    assert all(set(c['strings']) == keys for c in catalogs)
    assert all(c['source_index'] == en['source_index'] for c in catalogs)
    assert 'backup.destinations_remote_connect_help' not in keys
    for catalog in catalogs:
        assert catalog['strings']['backup.destinations_title'] == expected[catalog['code']]
