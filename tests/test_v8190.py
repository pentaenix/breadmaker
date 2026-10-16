from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]


def test_v8190_manuscript_toolbar_contract_reserves_room_without_losing_commands():
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int, version.split('.'))) >= (8, 19, 0)
    html = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
    js = frontend_source_from_app_path(ROOT / 'static' / 'app.js')
    css = frontend_styles_source_from_styles_path((ROOT / 'static' / 'styles.css'))
    soup = BeautifulSoup(html, 'html.parser')

    toolbar = soup.select_one('.document-toolbar')
    assert toolbar is not None
    direct_ids = [node.get('id') for node in toolbar.find_all(recursive=False) if node.get('id')]
    assert 'findBtn' not in direct_ids and 'bookmarkBtn' not in direct_ids
    assert direct_ids == [
        'commentsBtn', 'markupBtn', 'manuscriptImageBtn', 'checkpointBtn',
        'manuscriptReviewsBtn', 'cutsBtn', 'editorsEyeBtn',
    ]
    assert toolbar.select_one('.toolbar-title-group #activeDocumentTitle') is not None

    find = soup.select_one('#viewFindReplaceBtn')
    scenes = soup.select_one('#viewScenesBtn')
    divider = scenes.find_next_sibling(class_='view-popover-divider')
    assert find is not None and scenes.find_next_sibling('button') == find
    assert find.find_next_sibling(class_='view-popover-divider') == divider
    assert "$('#viewFindReplaceBtn')?.addEventListener('click',()=>{closeViewPopover();openFind()})" in js

    viewport_bookmark = soup.select_one('#documentScroller > #bookmarkBtn')
    assert viewport_bookmark is not None and 'hidden' in viewport_bookmark.get('class', [])
    assert 'viewport-bookmark-button{position:sticky;top:1px;left:1px' in css
    assert "button.classList.toggle('hidden',!has)" in js
    assert "bookmarkDestinationTooltip" in js
    assert "manuscript.bookmark_go_passage" in js
    assert "function wireSidebarBookmarkNavigation(button)" in js
    assert "$('#bookmarkBtn').oncontextmenu=event=>" in js
    assert "label:'Remove Bookmark'" in js

    selection_bookmark = soup.select_one('#selectionBookmarkBtn')
    assert selection_bookmark is not None
    assert selection_bookmark.get('data-selection-action') == 'bookmark'
    assert selection_bookmark.get('title') is None
    assert "attachManuscriptToolTooltip('selectionBookmarkBtn','selectionBookmarkTooltip',{hoverOnly:true,delay:300})" in js
    assert "{label:i18nSource(bookmarked?'Remove bookmark':'Bookmark selection'),action:()=>toggleTextBookmark(items)}" in js


def test_v8190_image_and_reviews_keep_their_toolbar_panel_contracts():
    html = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
    js = frontend_source_from_app_path(ROOT / 'static' / 'app.js')
    soup = BeautifulSoup(html, 'html.parser')

    canvas_image = soup.select_one('#addImage svg')
    manuscript_image = soup.select_one('#manuscriptImageBtn svg')
    assert canvas_image is not None and manuscript_image is not None
    assert str(canvas_image) == str(manuscript_image), 'Manuscript Image must reuse the Canvas Image icon exactly.'

    image_button = soup.select_one('#manuscriptImageBtn')
    image_panel = soup.select_one('#manuscriptImagePanel')
    assert image_button is not None and image_panel is not None
    assert image_button.get('aria-describedby') == 'manuscriptImageTooltip'
    assert 'right-panel' in image_panel.get('class', []) and 'hidden' in image_panel.get('class', [])
    assert image_panel.select_one('.panel-head') is not None
    assert image_panel.select_one('#manuscriptAddImage') is not None
    assert image_panel.select_one('#manuscriptImageSize') is not None
    assert image_panel.select_one('#manuscriptImageFooter') is not None
    assert image_panel.select_one('#manuscriptImageDescription') is not None

    reviews_button = soup.select_one('#manuscriptReviewsBtn')
    reviews_panel = soup.select_one('#manuscriptReviewsPanel')
    assert reviews_button is not None and reviews_panel is not None
    assert reviews_button.get('aria-describedby') == 'manuscriptReviewsTooltip'
    assert 'right-panel' in reviews_panel.get('class', []) and 'hidden' in reviews_panel.get('class', [])
    assert reviews_panel.select_one('.panel-head') is not None
    reviews_body = reviews_panel.select_one('#manuscriptReviewsPanelBody')
    assert reviews_body is not None, '8.20+ Manuscript Reviews owns a real panel body.'

    assert "$('#manuscriptImageBtn')?.addEventListener('click',openManuscriptImagePanel)" in js
    assert "$('#manuscriptReviewsBtn')?.addEventListener('click',openManuscriptReviewsPanel)" in js
    assert 'manuscriptImagePanel' in js and 'manuscriptReviewsPanel' in js
