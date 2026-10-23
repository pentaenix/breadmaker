from source_contracts import backend_source,frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path
import json
import subprocess

ROOT=Path(__file__).resolve().parents[1]




def test_v9610_bootstrap_is_navigation_projection_not_full_revision_hydration():
    app=backend_source(ROOT)
    projection=app[app.index('def _manuscript_navigation_projection'):app.index('def _scene_gap_rows')]
    bootstrap=app[app.index('def bootstrap():'):app.index('@app.patch("/api/project/title")')]
    assert "CASE WHEN b.scene_id IS NULL AND b.block_type='paragraph' THEN b.text_content ELSE '' END text_content" in projection
    assert 'revision_blocks' not in bootstrap
    assert 'chapter_revision_from_rows' not in bootstrap
    assert 'storage.chapter_revision(' not in bootstrap
    assert 'scenes,scene_gaps=manuscript_navigation._manuscript_navigation_projection(con,pid)' in bootstrap


def test_v9610_boot_overlaps_independent_io_but_keeps_lease_gate():
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    boot=js[js.index('async function boot()'):js.index('async function openRecoveryBoot')]
    assert 'Promise.all([ensureI18n(),loadRuntimeBootstrap()])' in boot
    assert 'Promise.all([profilePromise,portablePromise,workspacePromise])' in boot
    assert "const leasePromise=api('/api/lease',{method:'POST'})" in boot
    assert "'/api/bootstrap?navigation_only=true'" in boot and "'/api/bootstrap'" in boot
    assert 'loadBootUIState()' in boot
    assert 'crashJournalForProject(currentProjectIdentity())' in boot
    assert 'if(!leaseGranted){showLeaseLost(true);return}' in boot


def test_v9610_crash_drafts_lazy_load_missing_chapter_revision_only_when_needed():
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    assert 'async function ensureChapterRevision(chapterId)' in js
    assert 'const data=await api(`/api/chapters/${chapterId}`)' in js
    check=js[js.index('async function checkCrashJournal'):js.index('// -------------------- lease / boot')]
    assert 'map(ensureChapterRevision)' in check
    recover=js[js.index('async function recoverCrashDraft'):js.index('async function checkCrashJournal')]
    assert 'await ensureChapterRevision(entityId(draft.chapter_id))' in recover


def test_v9610_structural_manuscript_reload_invalidates_only_active_book_cache():
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    reload_fn=js[js.index('async function reloadManuscriptView'):js.index('function scheduleCommentsWorkspaceSync')]
    load_fn=js[js.index('async function loadBookData'):js.index('async function selectSceneGap')]
    # Execute the actual functions. The invariant is safe invalidation/reuse,
    # not the spelling of an if-condition before authorized revalidation existed.
    harness=r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const source=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
function scenario(hosted=true){
  const old={read_tag:'old',revision:4},fresh={read_tag:'new',revision:5},other={revision:99};
  const state={activeDocumentId:'tab_main',viewMode:'book',bookData:old,
    bookCache:new Map([['tab_main',old],['tab_other',other]])};
  const calls=[],opens=[],restores=[],viewport={position:123};
  let reply=fresh,context;
  context=vm.createContext({state,entityId:String,runtimeSupports:()=>hosted,
    captureManuscriptViewport:()=>viewport,restoreManuscriptViewport:value=>restores.push(value),
    api:async(url,options)=>{calls.push({url,options});if(reply instanceof Error)throw reply;return reply},
    openWholeBook:async(...args)=>{opens.push(args);state.bookData=await context.loadBookData(state.activeDocumentId)}});
  vm.runInContext(source.load+'\n'+source.reload,context);
  return {context,state,old,fresh,other,calls,opens,restores,viewport,setReply:value=>{reply=value}};
}
function unaffected(s){
  assert.equal(s.state.bookCache.get('tab_other'),s.other,'unrelated Tab cache was changed');
  assert.deepEqual(s.restores,[s.viewport],'viewport was not restored');
}
(async()=>{
  // Local structural reload must invalidate ONLY the active Tab and fetch it.
  let s=scenario(false);
  await s.context.reloadManuscriptView();
  assert.equal(s.state.bookData,s.fresh);assert.equal(s.calls.length,1);
  assert.deepEqual(s.opens,[[false,false]]);unaffected(s);

  // Ordinary navigation explicitly opting out of fresh reload still uses cache.
  s=scenario(false);await s.context.reloadManuscriptView({fresh:false});
  assert.equal(s.state.bookData,s.old);assert.equal(s.calls.length,0);unaffected(s);

  // Hosted 304 retains the SAME body and revision; it must not rebase stale text.
  s=scenario();s.setReply(null);await s.context.reloadManuscriptView();
  assert.equal(s.calls.length,1);assert.equal(s.calls[0].options.headers['If-None-Match'],'old');
  assert.equal(s.calls[0].options.acceptNotModified,true);
  assert.equal(s.state.bookData,s.old);assert.equal(s.state.bookData.revision,4);
  assert.equal(s.opens.length,0);unaffected(s);

  // A changed revision replaces active content; rendering must not fetch twice.
  s=scenario();await s.context.reloadManuscriptView();
  assert.equal(s.state.bookData,s.fresh);assert.equal(s.calls.length,1);
  assert.deepEqual(s.opens,[[false,false]]);unaffected(s);

  // Known structural invalidation bypasses conditional reuse altogether.
  s=scenario();s.state.bookCache.delete('tab_main');await s.context.reloadManuscriptView();
  assert.equal(s.state.bookData,s.fresh);assert.equal(s.calls.length,1);
  assert.equal(s.calls[0].options,undefined);unaffected(s);

  // A denied revalidation must propagate; it cannot authorize cached rendering.
  s=scenario();s.setReply(new Error('denied'));
  await assert.rejects(s.context.reloadManuscriptView(),/denied/);
  assert.equal(s.state.bookData,s.old);assert.equal(s.opens.length,0);
  assert.equal(s.state.bookCache.get('tab_other'),s.other);
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    result=subprocess.run(['node','-e',harness],input=json.dumps({'reload':reload_fn,'load':load_fn}),
        text=True,capture_output=True,timeout=3)
    assert result.returncode==0,result.stderr
