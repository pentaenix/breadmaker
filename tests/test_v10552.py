"""Legacy populated projects must not hydrate a whole book during startup."""
import json
from copy import deepcopy
from pathlib import Path
import subprocess

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from hosted.firestore.contracts import DocumentConflict, ProjectUnavailable
from hosted.runtime_services import _DEV_USER_ID as OWNER
from tests.test_v10364 import _replicas


def legacy_project(monkeypatch, count=5574):
    one,make,docs,pid,cid=_replicas(monkeypatch)
    block=dict(docs.query_collection(('projects',pid,'blocks'))[0].data)
    chapter=dict(docs.get(('projects',pid,'chapters',cid)).data)
    def seed(tx):
        tx.delete(('projects',pid,'blocks',block['id']))
        for index in range(61):
            key=cid if index==0 else 'chapter_'+format(index,'032x')
            tx.set(('projects',pid,'chapters',key),{**chapter,'id':key,'chapter_id':key,'position':index})
        for index in range(count):
            key='b_'+format(index,'032x')
            owner=cid if index%61==0 else 'chapter_'+format(index%61,'032x')
            tx.set(('projects',pid,'blocks',key),{**deepcopy(block),'id':key,'block_id':key,
                'chapter_id':owner,'position':index//61,'semantic_style':'body'})
    docs.run_transaction(seed)
    return one,make,docs,pid,cid


def request(pid,path,query='',tag=''):
    headers=[(b'x-breadmaker-project',pid.encode())]
    if tag:headers.append((b'if-none-match',tag.encode()))
    return Request({'type':'http','method':'GET','path':path,'query_string':query.encode(),'headers':headers})


def test_5574_legacy_paragraphs_bootstrap_and_manifest_read_zero_body_objects(monkeypatch):
    import hosted.http_api as http
    one,_,_,pid,cid=legacy_project(monkeypatch)
    monkeypatch.setattr(http,'runtime',one)
    reads=[];native=one.projects.objects.read_chunks
    def counted(*args,**kwargs):reads.append(args[0]);return native(*args,**kwargs)
    monkeypatch.setattr(one.projects.objects,'read_chunks',counted)
    bootstrap=http.hosted_bootstrap(request(pid,'/api/bootstrap','navigation_only=true'))
    assert bootstrap['blocks']==[] and len(bootstrap['chapters'])==61 and reads==[]
    assert bootstrap['manuscript_navigation']=={'chapter_first':True,'block_count':5574,'chapter_outlines':[]}
    tab=bootstrap['tabs'][0]['id']
    manifest=json.loads(http.hosted_book(request(pid,'/api/book',f'tab_id={tab}&manifest=true')).body)
    assert len(manifest['chapters'])==61 and manifest['blocks']==[] and reads==[]
    page=http.hosted_chapter_get(cid,request(pid,'/api/chapters/'+cid,'block_offset=0'))
    assert len(page['blocks'])==92 and len(reads)==92 # only the requested chapter
    assert page['next_block_offset'] is None
    assert all(row['chapter_id']==cid for row in page['blocks'])


def test_navigation_hydrates_only_headings_with_integrity_and_current_authority(monkeypatch):
    one,_,docs,pid,cid=legacy_project(monkeypatch,600)
    row=dict(docs.query_collection(('projects',pid,'blocks'))[0].data)
    docs.run_transaction(lambda tx:tx.set(('projects',pid,'blocks',row['id']),{**row,'semantic_style':'heading2'}))
    reads=[];native=one.projects.objects.read_chunks
    def counted(*args,**kwargs):reads.append(args[0]);return native(*args,**kwargs)
    monkeypatch.setattr(one.projects.objects,'read_chunks',counted)
    nav=one.projects.domain.manuscript_navigation(pid,user_id=OWNER)
    assert len(nav['chapter_outlines'])==1 and len(reads)==1
    assert nav['chapter_outlines'][0]['id']==row['id']
    reads.clear()
    with pytest.raises(ProjectUnavailable):one.projects.domain.manuscript_navigation(pid,user_id='usr_'+'f'*32)
    assert not reads
    bad=deepcopy(row);bad['semantic_style']='heading2';bad['payload_object']['object_sha256']='f'*64
    docs.run_transaction(lambda tx:tx.set(('projects',pid,'blocks',row['id']),bad))
    with pytest.raises(RuntimeError,match='HASH_MISMATCH'):one.projects.domain.manuscript_navigation(pid,user_id=OWNER)


def test_navigation_and_manifest_reject_concurrent_generation_change(monkeypatch):
    import hosted.http_api as http
    one,_,_,pid,_=legacy_project(monkeypatch,600)
    monkeypatch.setattr(http,'runtime',one)
    native=one.projects.domain.manuscript_navigation
    def intervening(*args,**kwargs):
        result=native(*args,**kwargs);one.projects.rename(pid,'Concurrent synthetic rename');return result
    monkeypatch.setattr(one.projects.domain,'manuscript_navigation',intervening)
    with pytest.raises(DocumentConflict,match='SNAPSHOT_GENERATION_CHANGED'):
        http._project_snapshot(pid,navigation_only=True)
    monkeypatch.setattr(http,'_uid',lambda:'usr_'+'f'*32)
    with pytest.raises(HTTPException) as denied:
        http.hosted_book(request(pid,'/api/book','manifest=true'))
    assert denied.value.status_code==404


def test_stale_bundle_accelerators_do_not_misclassify_legacy_reads_as_fast(monkeypatch):
    one,_,docs,pid,_=legacy_project(monkeypatch,600)
    def stale(tx):
        for stored in tx.query_collection(('projects',pid,'blocks')):
            tx.set(stored.path,{**stored.data,'_bm_read_bundle':{
                'source_sha256':'stale','bundle_entry':stored.data['id']}})
    docs.run_transaction(stale)
    assert one.projects.domain.manuscript_navigation(pid,user_id=OWNER)['chapter_first'] is True


def test_paragraph_pages_are_complete_ordered_bounded_and_generation_fenced(monkeypatch):
    import hosted.http_api as http
    one,_,docs,pid,cid=legacy_project(monkeypatch,300)
    # Model a large single chapter, not just a book spread across small chapters.
    def move(tx):
        for stored in tx.query_collection(('projects',pid,'blocks')):
            tx.set(stored.path,{**stored.data,'chapter_id':cid,'position':int(stored.data['id'][2:],16)})
    docs.run_transaction(move)
    monkeypatch.setattr(http,'runtime',one)
    reads=[];native=one.projects.objects.read_chunks
    def counted(*args,**kwargs):reads.append(args[0]);return native(*args,**kwargs)
    monkeypatch.setattr(one.projects.objects,'read_chunks',counted)
    pages=[http.hosted_chapter_get(cid,request(pid,'/api/chapters/'+cid,f'block_offset={offset}')) for offset in (0,128,256)]
    assert [len(page['blocks']) for page in pages]==[128,128,44] and len(reads)==300
    assert [page['next_block_offset'] for page in pages]==[128,256,None]
    ids=[row['id'] for page in pages for row in page['blocks']]
    assert ids==['b_'+format(i,'032x') for i in range(300)] and len(set(ids))==300
    native_list=one.projects.domain.list
    def intervening(entity,*args,**kwargs):
        result=native_list(entity,*args,**kwargs)
        if entity=='manuscript_block':one.projects.rename(pid,'Changed synthetic chapter')
        return result
    monkeypatch.setattr(one.projects.domain,'list',intervening)
    with pytest.raises(HTTPException) as changed:http.hosted_chapter_get(cid,request(pid,'/api/chapters/'+cid,'block_offset=0'))
    assert changed.value.status_code==409
    with pytest.raises(HTTPException) as invalid:http.hosted_chapter_get(cid,request(pid,'/api/chapters/'+cid,'block_offset=-1'))
    assert invalid.value.status_code==400


def test_changed_manifest_revalidation_never_falls_back_to_entire_book(monkeypatch):
    import hosted.http_api as http
    one,_,_,pid,_=legacy_project(monkeypatch,600)
    monkeypatch.setattr(http,'runtime',one)
    def no_body(*args,**kwargs):raise AssertionError('Manifest downloaded paragraph body')
    monkeypatch.setattr(one.projects.objects,'read_chunks',no_body)
    first=json.loads(http.hosted_book(request(pid,'/api/book','manifest=true')).body)
    assert http.hosted_book(request(pid,'/api/book','manifest=true',first['read_tag'])).status_code==304
    one.projects.rename(pid,'New synthetic revision')
    changed=http.hosted_book(request(pid,'/api/book','manifest=true',first['read_tag']))
    assert changed.status_code==200 and json.loads(changed.body)['blocks']==[]
    assert json.loads(changed.body)['read_tag']!=first['read_tag']


def test_browser_bounded_loader_rejects_partial_changed_denied_or_locally_edited_snapshots():
    source=(Path(__file__).resolve().parents[1]/'static/js/features/manuscript/rendering.js').read_text()
    functions=source[source.index('async function loadBookData'):source.index('async function selectSceneGap')]
    functions+='\n'+source[source.index('async function loadChapterData'):source.index('async function openWholeBook')]
    harness=r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const source=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
async function scenario(mode){
  const state={hostedChapterFirst:true,bookCache:new Map(),editGeneration:0,dirty:false,saveInFlight:null};
  if(['cached','stale-cache','partial-cache'].includes(mode))state.chapterData={chapter:{id:'one'},revision:mode==='stale-cache'?'6':'7',
    blocks:Array.from({length:129},(_,i)=>({id:'one'+i})),next_block_offset:mode==='partial-cache'?128:null};
  let active='project',inflight=0,peak=0;const calls=[],statuses=[];
  const manifest={project:{generation:7},chapters:[{id:'one'},{id:'two'},{id:'three'}],read_tag:'tag7',blocks:[]};
  const context=vm.createContext({state,entityId:String,runtimeSupports:()=>true,currentProjectIdentity:()=>active,setSaveState:value=>statuses.push(value),
    api:async(url,options)=>{
      calls.push(url);
      if(options?.acceptNotModified){
        assert.ok(url.includes('manifest=true'),'Changed revalidation could download the entire book');
        if(mode==='denied')throw new Error('denied');
        if(mode==='final-change')return {...manifest,project:{generation:8}};
        if(mode==='local-edit'){state.editGeneration++;state.dirty=true}
        if(mode==='project-change')active='different';
        return null;
      }
      if(url.includes('/api/book?'))return structuredClone(manifest);
      assert.ok(url.includes('block_offset='));inflight++;peak=Math.max(peak,inflight);
      await new Promise(resolve=>setTimeout(resolve,1));inflight--;
      const id=url.split('/').pop().split('?')[0],last=url.includes('block_offset=128');
      if(mode==='page-failure'&&last)throw new Error('page failed');
      const blocks=last?[{id:id+'128'}]:Array.from({length:128},(_,i)=>({id:id+i}));
      return {chapter:{id},revision:mode==='revision-change'&&last?'8':'7',blocks,next_block_offset:last?null:128};
    }});
  vm.runInContext(source,context);
  if(['ok','cached','stale-cache','partial-cache'].includes(mode)){
    const data=await context.loadBookData('tab');
    assert.equal(data.blocks.length,387);assert.equal(new Set(data.blocks.map(row=>row.id)).size,387);
    assert.equal(data.blocks[0].id,'one0');assert.equal(data.blocks[129].id,'two0');
    assert.equal(state.bookCache.get('tab'),data);assert.ok(peak<=2);assert.ok(peak>1);
    assert.equal(calls.filter(url=>url.includes('/api/book?')).length,2);
    assert.equal(calls.filter(url=>url.includes('/api/chapters/one?')).length,mode==='cached'?0:2);
    const before=calls.length;assert.equal(await context.loadBookData('tab'),data);assert.equal(calls.length,before+1);
    assert.ok(calls.at(-1).includes('manifest=true'),'Cached Full Text must recheck current authority');
  }else{
    await assert.rejects(context.loadBookData('tab'));
    assert.equal(state.bookCache.size,0,'Partial or unauthorized book escaped into the cache');
    if(mode==='local-edit')assert.equal(state.dirty,true,'Unsaved edits were cleared');
    assert.equal(inflight,0,'Failure returned while sibling chapter reads were still active');
  }
}
(async()=>{for(const mode of ['ok','cached','stale-cache','partial-cache','page-failure','revision-change','final-change','denied','local-edit','project-change'])await scenario(mode)})()
 .catch(error=>{console.error(error);process.exitCode=1});
'''
    result=subprocess.run(['node','-e',harness],input=json.dumps(functions),text=True,capture_output=True,timeout=3)
    assert result.returncode==0,result.stderr
