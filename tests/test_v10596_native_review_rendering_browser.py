"""Render imported identities/colors as data, without network or external resources.

These tests execute the actual renderer functions against Chromium's HTML/CSS
parsers. The quoted punctuation and extra CSS declarations are deliberately
benign; no scripts, external images, production APIs or user projects are used.
"""
from pathlib import Path

import pytest
from playwright.sync_api import expect

from scripts.browser_runtime import close_browser, start_browser

ROOT = Path(__file__).resolve().parents[1]
QUOTED_ID = 'frozen" data-extra="punctuation"><em>quoted</em><span title=" & \'single\''
EXTRA_CSS = '#123456; outline: 9px solid red; --unexpected: yes'


def _segment(path, start=None, end=None):
    source = (ROOT / path).read_text()
    return source[source.index(start) if start else 0:source.index(end) if end else None]


@pytest.fixture(scope='module')
def renderer_browser():
    driver, browser, _ = start_browser()
    try:
        yield browser
    finally:
        close_browser(driver, browser)


@pytest.fixture
def render_page(renderer_browser):
    page = renderer_browser.new_page()
    page.set_default_timeout(3000)
    errors, requests = [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.route('**/*', lambda route: (requests.append(route.request.url), route.abort()))
    page.set_content('''<div id="reviewerPaper"></div><div id="reviewerFeedbackForm"></div>
      <div id="reviewMarkersPanelBody"></div><div id="reviewMarkerPicker" class="hidden"><div id="reviewMarkerPickerList"></div></div>
      <div id="reviewSelectionToolbar"></div><div id="markupHoverCard" class="hidden"></div>
      <select id="reviewScopeType"><option value="chapters">Chapters</option></select><div id="reviewScopePicker"></div>
      <div id="checkpointList"></div><div id="checkpointPanelHoverCard" class="hidden"></div>
      <input id="checkpointSearch"><select id="checkpointScopeFilter"><option value="all">All</option></select>
      <div id="modalBody"></div><div id="modalActions"></div><div id="feedback"></div>''')
    page.add_script_tag(content=_segment('static/js/core/runtime.js', end='const CLIENT_ID'))
    page.add_script_tag(content='''
      const state={reviews:[],chapters:[],scenes:[],documents:[],reviewMarkerLibrary:[],markers:[]};
      const i18nText=key=>key,i18nSource=value=>value,i18nNumber=value=>String(value),i18nDateTime=value=>String(value),i18nCount=value=>String(value);
      const bindReadOnlyImageDescriptions=()=>{},visibleChapters=()=>state.chapters;
      const queueMarkerPanelHover=()=>{},hideMarkerPanelHover=()=>{};
      const showModal=(title,body,actions)=>{$('#modalBody').innerHTML=body;$('#modalActions').innerHTML=actions};
      const positionMarkupHoverCard=card=>card.classList.remove('hidden');
      let markerRows=[];
      const api=async url=>{if(url==='/api/review-markers')return markerRows;throw new Error('Unexpected renderer API '+url)};
    ''')
    page.add_script_tag(content=_segment('static/js/features/reviews/reader.js', 'function reviewById(', 'function reviewerApiPath('))
    page.add_script_tag(content=_segment('static/js/features/reviews/workspace.js', 'function reviewFeedbackCard(', 'function syncReviewFeedbackPreviews('))
    page.add_script_tag(content=_segment('static/js/features/reviews/workspace.js', 'function reviewScopeItems('))
    page.add_script_tag(content=_segment('static/js/features/reviews/panels.js', 'function reviewMarkerById(', 'function openReviewMarkersPanel('))
    page.add_script_tag(content=_segment('static/js/features/checkpoints/workspace.js', end="syncCheckpointTooltipStatus();$('#checkpointBtn')"))
    try:
        yield page
        assert not errors, errors
        assert not requests, requests
    finally:
        page.close()


def _assert_inert(page, selector):
    assert page.locator(selector + ' [data-extra]').count() == 0
    assert page.locator(selector + ' em').count() == 0


def _assert_color_only(page, selector, color='rgb(242, 201, 76)'):
    styles = page.locator(selector).evaluate_all('elements=>elements.map(el=>({background:el.style.backgroundColor,outline:el.style.outline,extra:el.style.getPropertyValue("--unexpected")}))')
    assert styles
    assert all(row == {'background': color, 'outline': '', 'extra': ''} for row in styles)


def test_imported_review_snapshot_keeps_quoted_chapter_and_block_ids_as_data(render_page):
    page = render_page
    page.evaluate('''id=>renderReviewerSnapshot({snapshot:{permission_read:true,
      chapters:[{source_chapter_id:id,title:'Chapter <em>title</em>'}],
      scenes:[{source_scene_id:id,title:'Scene <em>title</em>'}],
      blocks:[{source_chapter_id:id,source_scene_id:id,source_block_id:id,semantic_style:id,
        text_content:'Readable frozen text',content_html:'<strong>Readable frozen text</strong>'}]}})''', QUOTED_ID)
    expect(page.locator('#reviewerPaper section')).to_have_count(1)
    expect(page.locator('#reviewerPaper .reviewer-block')).to_have_count(1)
    assert page.locator('#reviewerPaper section').get_attribute('data-reviewer-chapter') == QUOTED_ID
    assert page.locator('#reviewerPaper section').evaluate('el=>el.getAttributeNames().sort()') == ['class', 'data-reviewer-chapter']
    assert page.locator('.reviewer-block').get_attribute('data-review-block-id') == QUOTED_ID
    assert page.locator('.reviewer-block').get_attribute('data-semantic-style') == QUOTED_ID
    assert page.locator('#reviewerPaper strong').inner_text() == 'Readable frozen text'
    _assert_inert(page, '#reviewerPaper')
    page.evaluate('id=>{state.chapters=[{id,title:"Frozen chapter"}];renderReviewScopePicker([id])}', QUOTED_ID)
    expect(page.locator('#reviewScopePicker input')).to_have_count(1)
    assert page.locator('#reviewScopePicker input').input_value() == QUOTED_ID
    assert page.locator('#reviewScopePicker input').is_checked()
    _assert_inert(page, '#reviewScopePicker')


def test_imported_review_marker_controls_escape_ids_and_restrict_color_declarations(render_page):
    page = render_page
    page.evaluate('''async ({id,color})=>{
      markerRows=[{id,name:'Frozen marker',color}];
      state.reviews=[{id:'review_safe',marker_ids:[id]}];state.currentReviewId='review_safe';
      await renderReviewMarkersPanel();state.markers=markerRows;reviewMarkerPopulateDialog();
    }''', {'id': QUOTED_ID, 'color': EXTRA_CSS})
    for attribute in ['data-review-marker-row', 'data-review-marker-use', 'data-review-marker-more']:
        control = page.locator('#reviewMarkerList [' + attribute + ']')
        expect(control).to_have_count(1)
        assert control.get_attribute(attribute) == QUOTED_ID
    assert page.locator('#reviewMarkerList [data-review-marker-use]').get_attribute('aria-pressed') == 'true'
    expect(page.locator('.review-marker-import-row input')).to_have_count(1)
    assert page.locator('.review-marker-import-row input').input_value() == QUOTED_ID
    _assert_inert(page, '#reviewMarkerList')
    _assert_inert(page, '.review-marker-import-list')
    _assert_color_only(page, '#reviewMarkerList .color-chip, .review-marker-import-list .color-chip')


def test_imported_checkpoint_controls_escape_ids_without_changing_selected_identity(render_page):
    page = render_page
    page.evaluate('''id=>{
      checkpointWorkspace={items:[{id,name:'Frozen checkpoint',scope:'chapter',source_exists:false}],selectedId:id};
      renderCheckpointList();
      state.documents=[{id:'tab_safe',is_manuscript:true}];state.chapters=[{id,title:'Frozen chapter'}];
      state.chapter=state.chapters[0];state.viewMode='chapter';showCheckpointCreate();
    }''', QUOTED_ID)
    for attribute in ['data-checkpoint-row', 'data-checkpoint-compare', 'data-checkpoint-more']:
        control = page.locator('#checkpointList [' + attribute + ']')
        expect(control).to_have_count(1)
        assert control.get_attribute(attribute) == QUOTED_ID
    expect(page.locator('#checkpointList .active')).to_have_count(1)
    expect(page.locator('#checkpointCreateChapter option')).to_have_count(1)
    assert page.locator('#checkpointCreateChapter').input_value() == QUOTED_ID
    _assert_inert(page, '#checkpointList')
    _assert_inert(page, '#modalBody')
    page.evaluate('id=>{$("#feedback").innerHTML=checkpointCompareUnitHtml("Read only text",id)}', QUOTED_ID)
    expect(page.locator('#feedback .checkpoint-unit')).to_have_count(1)
    _assert_inert(page, '#feedback')


def test_imported_review_picker_hover_feedback_and_annotations_accept_only_colors(render_page):
    page = render_page
    page.evaluate('''({id,color})=>{
      const snapshot={permission_read:true,permission_comment:true,
        chapters:[{source_chapter_id:'chapter_safe',title:'Frozen chapter'}],
        blocks:[{source_chapter_id:'chapter_safe',source_block_id:'block_safe',text_content:'Readable text'}],
        markers:[{source_marker_id:id,name:'Frozen marker',color}]};
      const item={id,participant_name:'Reviewer',marker_name:'Frozen marker',marker_color:color,
        start_block_id:'block_safe',end_block_id:'block_safe',start_offset:0,end_offset:8};
      state.reviewerSession={payload:{snapshot,feedback:{highlights:[item]}}};
      renderReviewerSnapshot(state.reviewerSession.payload);
      applyReviewerAnnotations(state.reviewerSession.payload);
      const range=document.createRange();range.selectNodeContents($('.reviewer-block'));window.getSelection().addRange(range);
      state.pendingReviewSelection={};openReviewMarkerPicker();
      $('#feedback').innerHTML=reviewFeedbackCard('highlight',item);
    }''', {'id': QUOTED_ID, 'color': EXTRA_CSS})
    _assert_color_only(page, '#reviewMarkerPickerList .color-chip, #feedback .review-feedback-marker-chip, #reviewerPaper .reviewer-highlight-swatch')
    _assert_inert(page, '#feedback')
    # Exercise the delayed hover renderer with a deterministic hover gate. Native
    # macOS headless tabs do not reliably retain the OS pointer's :hover state.
    # The resulting markup still goes through Chromium's real HTML/CSS parser.
    page.evaluate("()=>queueReviewerHighlightHover(state.reviewerSession.payload.feedback.highlights[0],{isConnected:true,matches:selector=>selector===':hover'})")
    expect(page.locator('#markupHoverCard .reviewer-highlight-hover-dot')).to_have_count(1)
    _assert_color_only(page, '#markupHoverCard .reviewer-highlight-hover-dot')
    assert page.evaluate('document.querySelector("#reviewMarkerHighlightStyles")?.textContent.includes("--unexpected")') is False
    assert page.evaluate('()=>["#abc","#abcd","#123456","#12345678"," #AABBCC "].map(reviewerHighlightCssColor)') == ['#abc', '#abcd', '#123456', '#12345678', '#AABBCC']
    assert page.evaluate('()=>["#abcde","#1234567","red;outline:0","var(--imported)",""].map(reviewerHighlightCssColor)') == ['#f2c94c'] * 5
