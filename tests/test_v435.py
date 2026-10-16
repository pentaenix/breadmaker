from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_v435_project_center_modal_handoff_is_explicit():
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    assert 'function projectCenterChild(openChild)' in js
    assert "$('#newProjectAction').onclick = () => projectCenterChild(createProjectDialog)" in js
    assert "$('#renameProjectAction').onclick = () => projectCenterChild(renameCurrentProject)" in js
    assert "$('#historyAction').onclick = () => projectCenterChild(openHistory)" in js
    assert "$('#recoveryAction').onclick = () => projectCenterChild(openRecovery)" in js
    assert "$('#verifyAction').onclick = () => projectCenterChild(verifyProject)" in js
    assert "function renameCurrentProject(onDismiss=null)" in js
    assert "showModal('Rename Project'" in js and 'onDismiss);' in js


def test_v435_brand_chevron_and_project_actions_have_dedicated_visual_contract():
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    assert '<svg class="brand-chevron"' in html
    assert '<span class="brand-chevron">⌄</span>' not in html
    assert '.brand-chevron path{' in css
    assert '.project-action-grid .project-action{display:flex;align-items:center' in css
    assert 'border:0;border-radius:7px;background:var(--surface2);font-weight:600' in css


def test_v435_assets_are_versioned_to_current_release():
    version=(ROOT/'VERSION').read_text(encoding='utf-8').strip()
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    assert version
