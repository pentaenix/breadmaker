from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_v41811_full_text_embeds_navigator_overflow_and_view_toggle_contract():
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    control=html[html.index('id="fullDocumentControl"'):html.index('id="collapsedFullTextBtn"')]
    assert control.index('id="bookViewBtn"') < control.index('id="navigatorMenuBtn"') < control.index('id="manuscriptNavToolbar"')
    assert 'class="manuscript-item-more full-document-menu"' in control
    assert 'id="viewChaptersBtn"' in html and '>Show Chapters<' in html
    assert "function chapterMarkersVisible(docId=state.activeDocumentId){const prefs=manuscriptTabPrefs(docId);return prefs.chapterMarkers!==false}" in js
    assert "navToolbar?.classList.toggle('hidden',visibleCount<2)" in js
    assert '.full-document-menu{' in css and 'position:absolute' in css[css.index('.full-document-menu{'):css.index('.manuscript-nav-toolbar{padding-top:0}')]


def test_v41811_show_chapters_is_presentation_only():
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    assert "function setChapterMarkersVisible(visible){updateManuscriptTabPrefs({chapterMarkers:!!visible});syncViewChaptersControl();if(['book','scene','scene-gap'].includes(state.viewMode))rerenderCurrentManuscriptSurface()}" in js
    render=js[js.index('function renderBookEditor()'):js.index('function renderSceneEditor()')]
    assert "if(chapterMarkersVisible()&&!ch.is_implicit){const heading=document.createElement('div')" in render
    assert "heading.className='book-chapter-heading'" in render
    assert "makeBlockWrap(block,ch.id)" in render
    scroll=js[js.index('function scrollBookChapter'):js.index('// -------------------- manuscript rendering')]
    assert "firstBlock=$(`.manuscript-block-wrap[data-chapter-id=\"${chapterId}\"]`)" in scroll
    assert '(heading||firstBlock)?.scrollIntoView' in scroll


def test_v41811_documentation_records_display_boundary_contract():
    readme=(ROOT/'README.md').read_text(encoding='utf-8')
    architecture=(ROOT/'ARCHITECTURE.md').read_text(encoding='utf-8')
    assert '### 4.18.11 Full Text navigator and Chapter display' in readme
    assert '### 4.18.11 Full Text navigation and display-boundary contract' in architecture
    assert 'Scene markers remain governed independently by `sceneMarkers`' in architecture
