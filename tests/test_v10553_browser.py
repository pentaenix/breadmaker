"""Actual hosted Populate, large-canvas redraw and new Map line visibility."""
from tests.test_v10548_browser import hosted_browser


def test_hosted_population_renders_without_per_node_connector_redraw(tmp_path):
    with hosted_browser(tmp_path) as (base,page):
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        project=page.request.post(base+'/api/projects',data={'name':'Synthetic performance regression'},headers={'Origin':base}).json()['project']
        pid=project['id'];headers={'Origin':base,'X-Breadmaker-Project':pid}
        response=page.request.post(base+'/api/canvases',data={'name':'Synthetic canvas'},headers=headers)
        assert response.ok,response.text()
        cid=response.json()['id']
        response=page.request.post(base+f'/api/canvases/{cid}/populate',data={'nodes':[{'title':'Too many'}]*96},headers=headers)
        assert response.status==400 and response.json()['detail']=='HOSTED_POPULATE_TOO_LARGE'
        assert page.request.get(base+f'/api/canvases/{cid}',headers=headers).json()['nodes']==[]
        nodes=[{'title':f'Synthetic card {index}','body':'','shape':'rect','node_type':'free',
                'x':(index%8)*260,'y':(index//8)*170,'w':220,'h':125,'position':index} for index in range(40)]
        response=page.request.post(base+f'/api/canvases/{cid}/populate',data={'nodes':nodes},headers=headers)
        assert response.ok,response.text()
        created=response.json()['nodes'];assert len(created)==40
        edges=[{'from_node_id':left['id'],'to_node_id':right['id'],'from_port':'right','to_port':'left'}
               for left,right in zip(created,created[1:])]
        response=page.request.post(base+f'/api/canvases/{cid}/populate',data={'edges':edges},headers=headers)
        assert response.ok,response.text()
        mapping=page.request.post(base+'/api/maps',data={'name':'Synthetic map'},headers=headers).json()
        response=page.request.post(base+f'/api/maps/{mapping["id"]}/populate',data={'lines':[{'name':'Visible line'}]},headers=headers)
        assert response.ok,response.text()
        page.goto(base+'/p/'+pid+'/maps',wait_until='domcontentloaded')
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        assert page.evaluate('()=>state.mapData.lines.some(line=>line.visible && Array.isArray(line.items))')
        page.evaluate('()=>{window._drawCount=0;const original=drawEdges;drawEdges=(...args)=>{window._drawCount++;return original(...args)}}')
        result=page.evaluate('''async id=>{
          setView('canvas');await selectCanvas(id);
          await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
          return {draws:window._drawCount};
        }''',cid)
        # The renderer's nodes are a lexical feature variable, not global state.
        assert len(page.locator('.canvas-node').all())==40
        assert 0<result['draws']<12,result
        assert not errors,errors
