"""Notes user workflows through actual local/hosted UI, HTTP and persistence."""
from playwright.sync_api import expect
from tests.test_v10569_narration_browser import browser
from tests.test_v10576_production_regressions_browser import selection_items


def lifecycle(tmp_path,local):
    with browser(tmp_path,local=local) as (_,page,_):
        page.set_default_timeout(2500)
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.evaluate('''async items=>{const prose=document.querySelector('.manuscript-block');const range=document.createRange();range.selectNodeContents(prose);state.lastManuscriptRange=range;await beginCommentComposer(items)}''',selection_items(page))
        page.locator('#commentCreateText').fill('Original harbor note')
        page.locator('#commentCreateSubmit').click()
        page.wait_for_function('()=>!state.pendingCommentItems')
        page.locator('#commentsBtn').click()
        expect(page.locator('[data-comment-row]')).to_have_count(1)
        page.locator('[data-comment-open]').click()
        expect(page.locator('[data-comment-message]')).to_have_count(1)
        expect(page.locator('[data-comment-message]').first).to_have_value('Original harbor note')
        expect(page.locator('.note-gutter-marker')).to_be_visible()
        tid=page.evaluate('()=>commentsWorkspace.items[0].id')
        saved=page.evaluate('async id=>await api("/api/comments/"+id)',tid)
        assert saved['first_message']=='Original harbor note' and len(saved['anchors'])==1
        assert saved['anchors'][0]['selected_text']=='Synthetic' and saved['status']=='open'
        page.locator('#commentReplyText').fill('Follow-up harbor note')
        page.locator('#commentReplySend').click()
        expect(page.locator('[data-comment-message]')).to_have_count(2)
        # A note-only mutation must not leave the next manuscript save stale.
        page.locator('.manuscript-block').first.fill('Synthetic narration passage. Still writing.')
        page.wait_for_function('()=>!state.dirty&&!state.saveInFlight')
        page.locator('[data-comment-message]').first.fill('Revised harbor note')
        page.locator('[data-comment-message]').first.press('Tab')
        page.wait_for_function('()=>commentsWorkspace.items.some(t=>t.first_message==="Revised harbor note")')
        page.locator('#commentResolve').click()
        expect(page.locator('[data-comment-row]')).to_have_count(0)
        page.locator('#commentsStatus').select_option('resolved')
        expect(page.locator('[data-comment-row]')).to_have_count(1)
        page.locator('[data-comment-open]').click()
        filtered=page.evaluate('''async()=>({open:await apiList('/api/comments?status=open','threads'),resolved:await apiList('/api/comments?status=resolved&q=Revised','threads'),missing:await apiList('/api/comments?status=all&q=absent-needle','threads')})''')
        assert filtered['open']==[] and filtered['missing']==[]
        assert [str(row['id']) for row in filtered['resolved']]==[str(tid)]
        page.locator('#commentResolve').click()
        expect(page.locator('[data-comment-row]')).to_have_count(0)
        page.locator('#commentsStatus').select_option('open')
        expect(page.locator('[data-comment-row]')).to_have_count(1)
        page.reload(wait_until='domcontentloaded')
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        page.locator('#commentsBtn').click();page.locator('[data-comment-open]').click()
        expect(page.locator('[data-comment-message]')).to_have_count(2)
        expect(page.locator('[data-comment-message]').first).to_have_value('Revised harbor note')
        expect(page.locator('[data-comment-message]').last).to_have_value('Follow-up harbor note')
        page.locator('#commentDelete').click()
        expect(page.locator('[data-comment-row]')).to_have_count(0)
        expect(page.locator('.note-gutter-marker')).to_have_count(0)
        page.locator('#undoBtn').click()
        expect(page.locator('[data-comment-row]')).to_have_count(1)
        restored=page.evaluate('async id=>await api("/api/comments/"+id)',tid)
        assert [m['body'] for m in restored['messages']]==['Revised harbor note','Follow-up harbor note']
        assert len(restored['anchors'])==1
        page.locator('#redoBtn').click()
        expect(page.locator('[data-comment-row]')).to_have_count(0)
        assert page.evaluate("async()=>await apiList('/api/comments?status=all','threads')")==[]
        assert not errors,errors


def test_local_notes_full_lifecycle_preserves_messages_anchors_and_writing(tmp_path):lifecycle(tmp_path,True)
def test_hosted_notes_full_lifecycle_preserves_messages_anchors_and_writing(tmp_path):lifecycle(tmp_path,False)
