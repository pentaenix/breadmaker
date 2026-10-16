from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path
from conftest import assert_ok


def test_v4103_chapter_from_paragraph_is_real_structural_split(client,fresh_project):
    chapter=fresh_project['bootstrap']['chapters'][0]
    cid=chapter['id']
    saved=assert_ok(client.put(f'/api/chapters/{cid}/blocks',json={'blocks':[
        {'semantic_style':'body','text_content':'Before','content_html':'Before'},
        {'semantic_style':'body','text_content':'New Chapter Name','content_html':'New Chapter Name'},
        {'semantic_style':'heading2','text_content':'Section','content_html':'Section'},
        {'semantic_style':'body','text_content':'After','content_html':'After'},
    ]}))
    pivot=saved['ids'][1]
    created=assert_ok(client.post(f'/api/chapters/{cid}/split',json={'block_id':pivot,'title':'New Chapter Name'}))
    old=assert_ok(client.get(f'/api/chapters/{cid}'))
    new=assert_ok(client.get(f"/api/chapters/{created['id']}"))
    assert [b['text_content'] for b in old['blocks']]==['Before']
    assert created['title']=='New Chapter Name'
    assert [b['text_content'] for b in new['blocks']]==['Section','After']
    assert new['blocks'][0]['semantic_style']=='heading2'
    boot=assert_ok(client.get('/api/bootstrap'))
    assert any(row['chapter_id']==created['id'] and row['text_content']=='Section' for row in boot['chapter_outlines'])


def test_v4103_static_contract_keeps_collapse_in_sidebar_only():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    html=(root/'static/index.html').read_text(encoding='utf-8')
    assert "{label:'H1  Chapter',action:()=>applyWritingStyle('chapter')}" in js
    assert "if(style==='chapter')" in js
    assert "createChapterFromStyle" in js
    assert "chapterOutlineItems" in js and "outline-nav" in js
    assert "Manuscript prose itself is never collapsible" in js
    assert '.chapter-outline-toggle' in css and '.outline-nav' in css
    assert '.outline-toggle{' not in css and '.book-chapter-toggle{' not in css
