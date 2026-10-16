from __future__ import annotations

from pathlib import Path

from source_contracts import frontend_styles_source, frontend_stylesheet_hrefs

ROOT=Path(__file__).resolve().parents[1]


def test_v1037_release_identity_keeps_css_boundary_stable():
    version=(ROOT/'VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (10,3,7)
    hrefs=frontend_stylesheet_hrefs(ROOT)
    assert len(hrefs)==23


def test_v1037_removes_known_redundant_cascade_reassertions():
    css=frontend_styles_source(ROOT)
    # 5.4.6 historically repeated this rule immediately; the earlier copy is redundant.
    rule='#canvasStructureBtn.active{background:var(--accent-soft);border-color:var(--accent)}'
    assert css.count(rule)==1
    # The avatar's complete presentation now has one canonical owner in local-profile CSS.
    assert css.count('.user-avatar{width:24px;height:24px;flex:none;border-radius:50%;overflow:hidden;display:block;box-shadow:0 0 0 1px color-mix(in srgb,var(--text) 16%,transparent);user-select:none;background:var(--surface2)}')==1


def test_v1037_cleanup_policy_is_documented():
    docs=(ROOT/'static/css/README.md').read_text(encoding='utf-8')
    architecture=(ROOT/'ARCHITECTURE.md').read_text(encoding='utf-8')
    assert 'mechanically provable redundancy' in docs
    assert '205 redundant declarations' in docs
    assert 'conservative CSS cascade cleanup' in architecture
    assert 'do not infer dead selectors from source absence' in architecture
