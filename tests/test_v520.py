from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_v520_project_surface_has_no_lost_project_center_actions():
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    # Former Project Center capabilities redistributed between quick Project menu and Settings.
    required=(
        'projectMenuNew','projectMenuRename','projectMenuSwitch','projectMenuBrowseAll',
        'projectMenuImportManuscript','projectMenuImportProject','projectMenuImportCuts',
        'projectMenuExportDocx','projectMenuExportPdf','projectMenuExportProject','projectMenuExportCuts',
        'settingsMetadata','settingsHistory','settingsBackup','settingsRecovery','settingsVerify',
        'settingsDeleteProject','settingsDeleteAll',
    )
    for token in required:
        assert token in js,token
    assert 'id="projectModal"' not in html
    assert 'id="projectMenu"' in html and 'id="projectSubmenu"' in html
    assert ':root[data-theme=dark] .brand-mark{background:transparent;color:#fff;border-color:#fff}' in css
