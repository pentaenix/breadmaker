"""Synthetic regressions for observed production Manuscript flows."""
from playwright.sync_api import expect
from tests.test_v10569_narration_browser import browser


def selection_items(page):
    return page.evaluate('''()=>{const prose=document.querySelector('.manuscript-block'),text=prose.innerText;return [{block_id:prose.closest('[data-block-id]').dataset.blockId,start_offset:0,end_offset:9,selected_text:text.slice(0,9),prefix_context:'',suffix_context:text.slice(9,54)}]}''')


def selected_highlight(tmp_path,local):
    with browser(tmp_path,local=local) as (_,page,_):
        page.set_default_timeout(2000)
        marker=page.evaluate('''async()=>{const marker=await api('/api/labels',{method:'POST',body:JSON.stringify({name:'QA selected prose',color:'#bd8d45',description:''})});await refresh(false);return marker}''')
        page.evaluate('async arg=>await applyAnnotation("highlight",arg.items,arg.marker,"")',{'items':selection_items(page),'marker':marker['id']})
        page.wait_for_function('()=>[...CSS.highlights.keys()].some(name=>name.startsWith("marker-"))')
        page.locator('#undoBtn').click()
        page.wait_for_function('()=>![...CSS.highlights.keys()].some(name=>name.startsWith("marker-"))')
        page.locator('#redoBtn').click()
        page.wait_for_function('()=>[...CSS.highlights.keys()].some(name=>name.startsWith("marker-"))')
        page.reload(wait_until='domcontentloaded')
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        page.wait_for_function('()=>[...CSS.highlights.keys()].some(name=>name.startsWith("marker-"))')


def selected_bookmark(tmp_path,local):
    with browser(tmp_path,local=local) as (_,page,_):
        page.set_default_timeout(2000)
        page.evaluate('async items=>await toggleTextBookmark(items)',selection_items(page))
        expect(page.locator('.text-bookmark-marker')).to_be_visible()
        assert page.evaluate('()=>state.bookmark.target_type')=='range'
        page.reload(wait_until='domcontentloaded')
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        expect(page.locator('.text-bookmark-marker')).to_be_visible()


def test_local_selected_highlight_paints_and_reopens(tmp_path):selected_highlight(tmp_path,True)
def test_hosted_selected_highlight_paints_and_reopens(tmp_path):selected_highlight(tmp_path,False)
def test_local_selection_bookmark_paints_and_reopens(tmp_path):selected_bookmark(tmp_path,True)
def test_hosted_selection_bookmark_paints_and_reopens(tmp_path):selected_bookmark(tmp_path,False)


def test_hosted_notes_panel_opens_without_shape_error(tmp_path):
    with browser(tmp_path) as (base,page,pid):
        response=page.request.get(base+'/api/comments?status=open',headers={'X-Breadmaker-Project':pid})
        assert response.ok
        assert response.json().get('threads')==[]
        page.set_default_timeout(2000)
        page.locator('#commentsBtn').click()
        expect(page.locator('#commentsPanel .comments-empty')).to_be_visible()


def test_hosted_checkpoint_preserves_word_count_and_live_source(tmp_path):
    with browser(tmp_path) as (_,page,_):
        page.set_default_timeout(2000)
        page.locator('.chapter-row').first.dblclick()
        page.wait_for_function('()=>state.viewMode==="chapter"&&!!state.chapter')
        prose=page.locator('.manuscript-block').first;prose.fill('The quiet harbor')
        page.wait_for_function('()=>!state.dirty&&!state.saveInFlight')
        page.locator('#checkpointBtn').click();page.locator('#checkpointCreateBtn').click()
        page.locator('#checkpointCreateScope').select_option('chapter')
        page.locator('#checkpointCreateName').fill('QA source metadata')
        with page.expect_response(lambda response:response.url.endswith('/api/checkpoints') and response.request.method=='POST') as saved:
            page.locator('#checkpointCreateConfirm').click()
        assert saved.value.ok
        checkpoint=saved.value.json()
        expect(page.locator('[data-checkpoint-row]')).to_have_count(1)
        assert checkpoint.get('word_count')==3
        assert checkpoint.get('source_exists') is True
        assert checkpoint.get('current_chapter_id')

        cid=checkpoint['id'];source=checkpoint['current_chapter_id']
        page.locator('.manuscript-block').first.fill('The stormy harbor')
        page.wait_for_function('()=>!state.dirty&&!state.saveInFlight')
        result=page.evaluate("""async arg=>{
          const before=await api('/api/project/metadata');
          const filtered=await api('/api/checkpoints?chapter_id='+encodeURIComponent(arg.source));
          const comparison=await api('/api/checkpoints/compare/versions?left='+encodeURIComponent(arg.cid)+'&right=current');
          const saved=await api('/api/checkpoints/'+arg.cid);
          const after=await api('/api/project/metadata');
          return {before:before.generation,after:after.generation,filtered,comparison,saved};
        }""",{'cid':cid,'source':source})
        assert result['before']==result['after'],'Read-only comparison cannot publish project mutations'
        assert [row['id'] for row in result['filtered']['checkpoints']]==[cid]
        assert result['comparison']['change_count']>0
        assert 'quiet' in str(result['comparison']['rows']) and 'stormy' in str(result['comparison']['rows'])
        assert result['saved']['snapshot']['blocks'][0]['text_content']=='The quiet harbor'
        expect(page.locator('#checkpointPanel')).to_be_visible()
        page.locator('#checkpointScopeFilter').select_option('current')
        expect(page.locator('[data-checkpoint-row]')).to_have_count(1)
        expect(page.locator('[data-checkpoint-row]')).to_contain_text('3 words')
        page.locator('[data-checkpoint-compare]').click()
        expect(page.locator('#checkpointSplitShowDiff')).to_be_enabled()
        page.locator('#checkpointSplitShowDiff').check()
        expect(page.locator('#splitBody .checkpoint-diff-removed')).to_contain_text('quiet')
