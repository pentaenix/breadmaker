from __future__ import annotations
from source_contracts import frontend_source_from_app_path

import json
import re
from pathlib import Path
from types import SimpleNamespace

from workspace import filesystem as workspace_fs


def test_v732_release_and_backup_ui_contract():
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text().strip().split('.'))) >= (7,3,2)
    index=(root/'static/index.html').read_text()
    appjs=frontend_source_from_app_path(root/'static/app.js')
    assert 'styles.css?v=' in index and 'app.js?v=' in index
    assert "i18nText('backup.destinations_disk_unknown_warning')" in appjs
    assert "toast(i18nText('backup.destinations_created'))" in appjs
    assert "i18nText('backup.destinations_created',{file:" not in appjs
    assert 'recovery-boot-backup' in appjs
    assert "b.created_at?i18nDateTime(b.created_at):i18nText('backup.recovery_unknown')" in appjs
    assert "button.textContent=i18nSource('Creating…')" in appjs


def test_v732_same_physical_disk_is_conservative(monkeypatch):
    a=Path('/a'); b=Path('/b')
    values={str(a):1,str(b):2}
    monkeypatch.setattr(workspace_fs.os,'stat',lambda p:SimpleNamespace(st_dev=values[str(Path(p))]))
    monkeypatch.setattr(workspace_fs,'_physical_disk_identity',lambda p:'disk-1')
    assert workspace_fs._same_physical_disk(a,b) is True
    monkeypatch.setattr(workspace_fs,'_physical_disk_identity',lambda p:'disk-1' if Path(p)==a else 'disk-2')
    assert workspace_fs._same_physical_disk(a,b) is False
    monkeypatch.setattr(workspace_fs,'_physical_disk_identity',lambda p:None)
    assert workspace_fs._same_physical_disk(a,b) is None
    values[str(b)]=1
    assert workspace_fs._same_physical_disk(a,b) is True


def test_v732_all_locales_drop_obsolete_backup_ui_and_match_placeholders():
    root=Path(__file__).resolve().parents[1]
    catalogs=[json.loads(p.read_text(encoding='utf-8')) for p in sorted((root/'locales').glob('*.json'))]
    assert {c['code'] for c in catalogs}=={'en','es','fr','it','ja','pt-BR','zh-CN'}
    base=set(catalogs[0]['strings'])
    assert all(set(c['strings'])==base for c in catalogs)
    assert 'backup.folder_disk_unknown_warning' in base
    assert not any(k.startswith('backup.external_') for k in base)
    assert 'runtime.source.9b42388bc401' not in base
    assert 'runtime.source.9b3a06fb7ae7' not in base
    assert 'dynamic.html.text.button.3b55abf9' not in base
    english=next(c for c in catalogs if c['code']=='en')['strings']
    token=re.compile(r'\{[^{}]+\}')
    for c in catalogs:
        assert sorted(token.findall(c['strings']['backup.folder_disk_unknown_warning']))==sorted(token.findall(english['backup.folder_disk_unknown_warning']))
        assert token.findall(c['strings']['backup.folder_created'])==[]
        assert c['strings']['backup.folder_disk_unknown_warning'].strip()
