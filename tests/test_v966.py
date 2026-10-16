from source_contracts import backend_source, storage_source,frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
VERSION=(ROOT/'VERSION').read_text().strip()
APP=backend_source(ROOT)
STORAGE=storage_source(ROOT)
JS=frontend_source_from_app_path(ROOT/'static/app.js')
HTML=(ROOT/'static/index.html').read_text()
CSS=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))




def test_v966_sqlite_wal_is_initialized_once_and_hot_reads_are_concurrent():
    connect=STORAGE[STORAGE.index('def connect()'):STORAGE.index('def read_connect()')]
    init=STORAGE[STORAGE.index('def init_db()'):STORAGE.index('def _rotate_backup_files',STORAGE.index('def init_db()'))]
    assert 'execute("PRAGMA journal_mode=WAL")' not in connect and "execute('PRAGMA journal_mode=WAL')" not in connect
    assert 'PRAGMA journal_mode=WAL' in init
    assert 'def read_tx()' in STORAGE and 'mode=ro' in STORAGE
    for signature in ['def bootstrap():','def get_book(tab_id:Optional[int]=None):','def get_canvas(cid:int):','def get_map(mid:int):']:
        start=APP.index(signature);body=APP[start:start+1800]
        assert 'storage_database.read_tx()' in body


def test_v966_full_text_is_single_pass_and_idle_prefetched():
    book=APP[APP.index('def get_book(tab_id:Optional[int]=None):'):APP.index('@app.post("/api/chapters")')]
    assert 'chapter_revision_from_rows' in book
    assert 'storage.chapter_revision(con' not in book
    assert 'SELECT * FROM manuscript_blocks WHERE project_id=? AND tab_id=?' in book
    assert 'a.block_id IN (SELECT id FROM manuscript_blocks' in book
    assert 'bookCache: new Map()' in JS
    assert 'requestIdleCallback(prefetch' in JS
    assert 'document.createDocumentFragment()' in JS


def test_v966_canvas_hot_path_has_no_sync_full_history_or_request_fanout():
    populate=APP[APP.index('def populate_canvas('):APP.index('def _developmental_scope_nodes',APP.index('def populate_canvas('))]
    create=APP[APP.index('def create_canvas('):APP.index('@app.get("/api/canvases/{cid}")')]
    assert 'commit(' not in populate
    assert 'commit(' not in create
    assert 'operation="canvas_populate"' in populate
    assert 'operation="canvas_create"' in create
    assert '@app.patch("/api/canvases/{cid}/nodes/positions")' in APP
    settle=JS[JS.index('async function settlePopulatedRows'):JS.index('async function populateCanvas')]
    assert '/nodes/positions' in settle
    assert "api(`/api/nodes/${node.id}`" not in settle


def test_v966_session_undo_is_object_scoped_for_canvas_and_maps():
    assert "'canvas-tree': frozenset" in STORAGE
    assert "'maps-meta': frozenset({'story_maps'})" in STORAGE
    assert 'scope_id:int|None=None' in STORAGE
    assert "scope_name=='canvas'" in STORAGE and 'WHERE canvas_id=?' in STORAGE
    assert "scope_name=='maps'" in STORAGE and 'WHERE map_id=?' in STORAGE
    assert 'scope_id=${encodeURIComponent(scopeId)}' in JS
    assert "undoScope:'canvas-tree'" in JS
    assert "undoScope:'maps-meta'" in JS


def test_v966_map_projection_is_cached_by_manuscript_not_map_generation():
    assert '_MAP_CHRONOLOGY_CACHE' in APP
    chrono=APP[APP.index('def _manuscript_chronology_signature'):APP.index('def _map_anchor_position')]
    assert 'MAX(updated_at)' in chrono
    assert 'project_generation' not in chrono
    detail=APP[APP.index('def _map_detail'):APP.index('@app.get("/api/scenes/{scene_id}/map-memberships")')]
    assert detail.count('_map_row(con,pid,mid)')==1
    assert "chronology.get('chapter_names'" in APP
    assert "chronology.get('scene_names'" in APP


def test_v966_immutable_history_is_writer_idle_not_interactive_path():
    assert '_LOCAL_HISTORY_DEBOUNCE_SECONDS = 90.0' in APP
    assert "if stop_event.is_set():" in APP
    assert "_publish_local_history_event(event)" in APP
