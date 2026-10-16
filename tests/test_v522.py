from __future__ import annotations
from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_v522_release_contract_keeps_import_modal_visible_with_approximate_progress():
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 4, 4)
    for token in (
        'runManuscriptImportWithProgress',
        'beginManuscriptImportProgress',
        'completeManuscriptImportProgress',
        'failManuscriptImportProgress',
        'manuscriptImportProgressBar',
        'cancelImportManuscript',
    ):
        assert token in js
    assert "runManuscriptImportWithProgress(file,'Preparing replacement…'" in js
    assert "runManuscriptImportWithProgress(file,'Preparing new project…'" in js
    assert "runManuscriptImportWithProgress(file,'Preparing Tabs…'" in js
    assert 'manuscript-import-progress-track' in css
    assert 'styles.css?v=5.4.4' in html and 'app.js?v=5.4.4' in html


def test_v522_new_project_import_no_longer_forces_reload_inside_import_primitive():
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    start=js.index('async function performManuscriptImportAsNewProject')
    end=js.index('// -------------------- checkpoints',start)
    primitive=js[start:end]
    assert 'return await api(`/api/import/new-project' in primitive
    assert 'location.reload()' not in primitive
    assert "{reload:true}" in js
