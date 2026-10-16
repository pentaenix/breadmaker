from source_contracts import frontend_source_from_app_path
from storage import mutations as storage_mutations
import storage as storage_package

from pathlib import Path
from conftest import assert_ok


def test_v4165_static_cut_label_and_history_viewport_contract():
    root=Path(__file__).resolve().parents[1]
    html=(root/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static/app.js')
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert 'data-selection-action="cuts">Cut</button>' in html
    history=js[js.index('function requestHistory'):js.index('function appUndo')]
    assert "if(currentView()==='manuscript')await withManuscriptMutationBarrier(()=>preserveManuscriptViewport(()=>kind==='undo'?performUndo():performRedo()))" in history


def test_v4165_cut_delete_is_interactive_and_defers_full_gc(client,fresh_project,monkeypatch):
    chapter=fresh_project['bootstrap']['chapters'][0]
    cut=assert_ok(client.post('/api/cuts',json={
        'title':'Fast delete','content_html':'<p>Discarded prose.</p>',
        'source_chapter_id':chapter['id']
    }))
    with storage_mutations.tx() as con:
        root_hash=con.execute('SELECT root_hash FROM cuts WHERE id=?',(cut['id'],)).fetchone()[0]
        assert con.execute('SELECT unreachable_since FROM content_objects WHERE hash=?',(root_hash,)).fetchone()[0] is None

    # A permanent Cut delete must not perform the whole preservation graph walk on
    # the request path. If that old call comes back, this test fails immediately.
    def forbidden_gc(*args,**kwargs):
        raise AssertionError('Cut deletion must not run whole-project preservation GC')
    monkeypatch.setattr(storage_package,'run_preservation_gc',forbidden_gc)

    assert_ok(client.delete(f"/api/cuts/{cut['id']}"))
    assert not any(x['id']==cut['id'] for x in assert_ok(client.get('/api/cuts')))
    with storage_mutations.tx() as con:
        row=con.execute('SELECT unreachable_since FROM content_objects WHERE hash=?',(root_hash,)).fetchone()
        assert row is not None and row[0] is not None
