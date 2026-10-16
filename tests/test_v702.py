from __future__ import annotations
from source_contracts import frontend_source_from_app_path
import re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_v702_release_and_language_entry_placement():
    assert tuple(map(int,(ROOT/'VERSION').read_text().strip().split('.'))) >= (7,0,2)
    app=frontend_source_from_app_path(ROOT/'static/app.js')
    index=(ROOT/'static/index.html').read_text(encoding='utf-8')
    settings_pos=app.index("projectMenuButton('projectMenuSettings'")
    language_pos=app.index("projectMenuButton('projectMenuLanguage'")
    assert settings_pos < language_pos
    assert "$('#projectMenuLanguage').onclick=()=>{hideProjectMenu();openLanguageSettings()}" in app
    assert 'settingsLanguage' not in app
    version=(ROOT/'VERSION').read_text().strip()


def test_v702_project_settings_no_longer_contains_language_section():
    app=frontend_source_from_app_path(ROOT/'static/app.js')
    block=app[app.index('async function openProjectSettings()'):app.index('async function openExperimentalFeatures')]
    assert 'settings.language.application_section' not in block
    assert 'settings.language.row_label' not in block
    assert 'openLanguageSettings' not in block
