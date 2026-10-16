from __future__ import annotations
from source_contracts import frontend_source_from_app_path
import json,re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
APP=frontend_source_from_app_path(ROOT/'static/app.js')


def test_v704_locale_catalogs_stay_in_lockstep():
    en=json.loads((ROOT/'locales/en.json').read_text(encoding='utf-8'))
    es=json.loads((ROOT/'locales/es.json').read_text(encoding='utf-8'))
    assert set(en['strings'])==set(es['strings'])
    assert en['source_index']==es['source_index']
    assert en.get('css_variables')==es.get('css_variables')
    for key in ['status.words.one','status.words.many','status.scenes.one','status.scenes.many','bookmark.remove_tooltip','scene_gap.words.many','task.milestones.all_complete','checkpoint.read_only_title','history.read_only_title']:
        assert key in en['strings'] and key in es['strings']


def test_v704_named_runtime_bypasses_are_localized():
    required=[
        "$('#saveState').textContent = localized",
        "$('#globalSaveState').textContent = localized",
        "$('#wordCount').textContent=i18nCount",
        "projectMenuButton('projectMenuSettings','Project Settings…')",
        "projectMenuButton(id,label",
        "escapeHtml(i18nSource(label))",
        "syncFindScopeLabel(){",
        "label.textContent=i18nSource",
        "Editor’s Eye on — click to restore normal manuscript view",
        "button.setAttribute('aria-label',i18nSource",
        "function bookmarkDestinationTooltip(bookmark=state.bookmark)",
        "if(text)text.textContent=tooltip",
        "i18nSource(hasCuts?'No matching cuts':'No cuts yet')",
        "i18nSource(hasCuts?'Try a different search.':'Select manuscript text and use Cut selection",
    ]
    for needle in required:
        assert needle in APP, needle


def test_v704_new_semantic_placeholders_match_between_locales():
    en=json.loads((ROOT/'locales/en.json').read_text(encoding='utf-8'))['strings']
    es=json.loads((ROOT/'locales/es.json').read_text(encoding='utf-8'))['strings']
    token=re.compile(r'\{[A-Za-z_][A-Za-z0-9_]*\}')
    for key in set(en):
        assert sorted(token.findall(en[key]))==sorted(token.findall(es[key])), key
