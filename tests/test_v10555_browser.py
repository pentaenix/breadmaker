"""Fully bundled large imports must never turn on unsolicited full-book reads."""
from tests.test_v10548_browser import hosted_browser


def _assert_hosted_boot_does_not_prefetch(tmp_path,count):
    with hosted_browser(tmp_path) as (base,page):
        headers={'Origin':base}
        raw='\n'.join(f'Synthetic55 paragraph {i}' for i in range(count)).encode()
        progress=page.request.post(base+'/api/import/new-project?resumable=true&project_name=Synthetic55',
            multipart={'file':{'name':'Synthetic55.txt','mimeType':'text/plain','buffer':raw}},headers=headers).json()
        job=progress['job_id']
        while not progress['ready']:
            progress=page.request.post(base+f'/api/import/jobs/{job}/step?batch={progress["next_batch"]}',headers=headers).json()
        pid=page.request.post(base+f'/api/import/jobs/{job}/finish',headers=headers).json()['project']['id']
        book=[];errors=[]
        page.on('request',lambda r:book.append(r.url) if '/api/book?' in r.url else None)
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(base+'/p/'+pid,wait_until='domcontentloaded')
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        assert page.evaluate('()=>state.hostedChapterFirst')==(count>512)
        assert page.locator('.manuscript-block').count()==count
        initial=len(book)
        # Small books intentionally open Full Text; large ones open a chapter.
        assert initial==(1 if count<=512 else 0)
        # Let startup idle callbacks run; no hidden book hydration is permitted.
        page.wait_for_timeout(2000)
        assert len(book)==initial and not errors,(book,errors)


def test_small_hosted_book_opens_full_text_without_extra_prefetch(tmp_path):
    _assert_hosted_boot_does_not_prefetch(tmp_path,2)


def test_large_bundled_hosted_book_stays_chapter_first_without_prefetch(tmp_path):
    _assert_hosted_boot_does_not_prefetch(tmp_path,513)
