from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_v10336_release_stays_on_schema_42_and_closes_checkpoint11_tooltip_race():
    assert tuple(map(int, (ROOT / "VERSION").read_text(encoding="utf-8").strip().split("."))) >= (10, 3, 36)
    assert "APP_SCHEMA_VERSION = 42" in (ROOT / "src/workspace/constants.py").read_text(encoding="utf-8")


def test_v10336_selection_toolbar_is_positioned_before_it_becomes_interactive():
    source = (ROOT / "static/js/features/manuscript/editing.js").read_text(encoding="utf-8")
    start = source.index("function positionSelectionToolbar")
    end = source.index("function showSelectionToolbarAfterPointer", start)
    section = source[start:end]
    assert "toolbar.style.visibility='hidden';toolbar.classList.remove('hidden')" in section
    assert section.index("toolbar.style.left=") < section.index("toolbar.style.visibility=''", section.index("toolbar.style.left="))
    assert "requestAnimationFrame(" not in section


def test_v10336_delayed_tooltips_are_owned_by_enter_leave_liveness():
    source = (ROOT / "static/js/features/manuscript/workspace.js").read_text(encoding="utf-8")
    start = source.index("function attachManuscriptToolTooltip")
    end = source.index("attachManuscriptToolTooltip('undoBtn'", start)
    section = source[start:end]
    assert "hovered=false,focused=false" in section
    assert "const active=()=>hovered||(!hoverOnly&&focused)" in section
    assert "if(!active())return" in section
    assert "button.addEventListener('mouseleave',()=>{hovered=false;if(!active())hide()})" in section


def test_v10336_auth_start_contract_includes_language_restart_without_dropping_login_or_signup():
    source = (ROOT / "src/backend/request_pipeline.py").read_text(encoding="utf-8")
    assert "path in {'/auth/login','/auth/signup','/auth/language'}" in source
