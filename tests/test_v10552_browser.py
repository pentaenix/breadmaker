"""Navigation boots without a full-book read; paged text stays complete."""
from playwright.sync_api import expect
from tests.test_v10548_browser import hosted_browser


def test_hosted_navigation_opens_complete_chapter_and_full_text_without_whole_book_request(tmp_path):
    with hosted_browser(tmp_path) as (base,page):
        origin={'Origin':base}
        made=page.request.post(base+'/api/projects',data={'name':'Synthetic legacy navigation'},headers=origin)
        pid=made.json()['project']['id'];headers={**origin,'X-Breadmaker-Project':pid}
        bootstrap=page.request.get(base+'/api/bootstrap',headers=headers).json()
        cid=bootstrap['chapters'][0]['id'];tab=bootstrap['tabs'][0]['id']
        rows=[{'text_content':f'Synthetic paragraph {i}','content_html':f'Synthetic paragraph {i}'} for i in range(140)]
        saved=page.request.put(base+'/api/chapters/'+cid+'/blocks',headers=headers,
            data={'expected_revision':bootstrap['project']['generation'],'blocks':rows})
        assert saved.ok
        second=page.request.post(base+'/api/chapters',headers=headers,
            data={'tab_id':tab,'title':'Second synthetic chapter','position':1}).json()
        generation=page.request.get(base+'/api/bootstrap',headers=headers).json()['project']['generation']
        saved=page.request.put(base+'/api/chapters/'+second['id']+'/blocks',headers=headers,
            data={'expected_revision':generation,'blocks':[{'text_content':'Second chapter survives','content_html':'Second chapter survives'}]})
        assert saved.ok
        # Exercise the large-legacy browser path with a small fixture. The
        # separate provider test uses the actual5574/61 metadata layout.
        def large_navigation(route):
            response=route.fetch();body=response.json()
            body['manuscript_navigation']['chapter_first']=True
            route.fulfill(response=response,json=body)
        page.route('**/api/bootstrap?navigation_only=true',large_navigation)
        reads=[]
        page.on('request',lambda request:reads.append(request.url) if '/api/book?' in request.url or '/api/chapters/' in request.url else None)
        page.goto(base+'/p/'+pid+'/canvas',wait_until='domcontentloaded')
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        assert reads==[] # Inactive Manuscript does not hydrate on Canvas startup.
        page.locator('.main-tab[data-view="manuscript"]').click()
        expect(page.locator('.manuscript-block')).to_have_count(140)
        expect(page.locator('.manuscript-block').last).to_have_text('Synthetic paragraph 139')
        assert page.evaluate('()=>state.viewMode')=='chapter'
        assert any('block_offset=128' in url for url in reads)
        assert not any('/api/book?' in url for url in reads)
        chapter_reads=sum('/api/chapters/'+cid in url for url in reads)
        page.locator('#bookViewBtn').click()
        expect(page.locator('.manuscript-block')).to_have_count(141)
        expect(page.locator('.manuscript-block').last).to_have_text('Second chapter survives')
        assert page.evaluate('()=>state.viewMode')=='book'
        assert all('manifest=true' in url for url in reads if '/api/book?' in url)
        assert page.evaluate('()=>new Set(state.bookData.blocks.map(row=>row.id)).size')==141
        assert sum('/api/chapters/'+cid in url for url in reads)==chapter_reads # exact-revision chapter reused
