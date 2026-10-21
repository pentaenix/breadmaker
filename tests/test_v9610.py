from source_contracts import backend_source,frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path

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
    assert "api('/api/bootstrap')" in boot
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
    assert 'if(fresh&&state.activeDocumentId)state.bookCache.delete(entityId(state.activeDocumentId))' in reload_fn
    assert "if(state.viewMode==='book')await openWholeBook(false,false)" in reload_fn
    # Ordinary Full Text navigation still uses the cache; structural reload is the invalidation boundary.
    assert 'async function loadBookData' in js and 'state.bookCache.get' in js
