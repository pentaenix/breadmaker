from source_contracts import frontend_styles_source_from_styles_path
from pathlib import Path


def test_v4171_manuscript_tool_cards_share_marker_border_language():
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    assert '.marker-row{display:flex;align-items:stretch;gap:0;padding:0;border:1px solid var(--line);' in css
    assert '.comments-row{position:relative;flex:none;width:100%;max-width:100%;overflow:hidden;border:1px solid var(--line);background:var(--surface);' in css
    assert '.comments-row:hover{background:var(--surface2);border-color:var(--line2)}' in css
    assert '.comments-row.active{background:var(--accent-soft);border-color:var(--accent)}' in css
    assert '.checkpoint-list-row{\n  flex:none;\n  border:1px solid var(--line);\n  background:var(--surface);' in css
    assert '.checkpoint-list-row:hover{border-color:var(--line2)}' in css
    assert '.checkpoint-list-row.active{border-color:var(--accent)}' in css
    assert '.cuts-list-row{flex:none;border:1px solid var(--line);background:var(--surface)}' in css
    assert '.cuts-list-row:hover{border-color:var(--line2)}' in css
    assert '.cuts-list-row.active{border-color:var(--accent)}' in css
