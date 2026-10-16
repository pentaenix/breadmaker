from source_contracts import frontend_styles_source_from_styles_path
from pathlib import Path


def test_v571_dusk_manuscript_parity_contract():
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text().strip().split('.'))) >= (5, 7, 1)
    html=(root/'static/index.html').read_text()
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    assert '?v=5.7.1' in html

    # Dusk keeps the 5.7 palette, but Manuscript is one continuous writing surface.
    assert ':root[data-theme=dusk]{--bg:#b6b1a8;--surface:#dfdbd2;--surface2:#cbc6bc;--text:#292a27;' in css
    assert ':root[data-theme=dusk] .document-scroller{background:#e2ded5}' in css
    assert ':root[data-theme=dusk] .paper{background:transparent;box-shadow:none}' in css

    # Eye owns the scroller background, and transparent paper allows it to recolor all prose space.
    assert '#documentScroller.editors-eye-active{background:var(--ee-bg);color:var(--ee-text)}' in css

    # Dusk selection is visible but not a bright/high-glare highlight.
    assert 'background:#b7c5c9;color:#202829' in css
