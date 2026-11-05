"""Background personal state must not join a canonical structural Undo action."""
from tests.test_v10569_narration_browser import browser


def test_personal_state_during_structural_action_retains_auth_nonce_and_canonical_undo(tmp_path):
    with browser(tmp_path,local=False) as (_,page,pid):
        requests=[];bad=[]
        page.on('request',lambda r:requests.append(r) if r.method=='PUT' and '/api/ui-state/' in r.url or r.method=='POST' and r.url.endswith('/api/maps') else None)
        page.on('response',lambda r:bad.append((r.status,r.url)) if r.status>=400 else None)
        created=page.evaluate('''async()=>{let created;await withStructuralUndo('Synthetic Map with background personal save',async()=>{
          await api('/api/ui-state/manuscript-last-location',{method:'PUT',body:JSON.stringify({value:{view:'book',tab_id:'tab_manuscript',viewport:{scrollTop:17}}})});
          created=await api('/api/maps',{method:'POST',body:JSON.stringify({name:'Synthetic member state isolation'})});
        },{view:'maps'});return created.id}''')
        preference=next(r for r in requests if r.method=='PUT')
        canonical=next(r for r in requests if r.method=='POST')
        for request in (preference,canonical):
            assert request.headers.get('x-breadmaker-project')==pid
            assert request.headers.get('idempotency-key')
        assert 'x-breadmaker-action' not in preference.headers
        assert canonical.headers.get('x-breadmaker-action')
        page.evaluate('async()=>await appUndo()')
        assert page.evaluate('(id)=>state.maps.some(m=>m.id===id)',created) is False
        assert page.evaluate("async()=>(await api('/api/ui-state/manuscript-last-location')).value.viewport.scrollTop")==17
        page.evaluate('async()=>await appRedo()')
        assert page.evaluate('(id)=>state.maps.some(m=>m.id===id)',created) is True
        assert not bad,bad
