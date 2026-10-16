from source_contracts import backend_source, storage_source
from pathlib import Path


def test_v4121_startup_is_nonblocking_preservation_contract():
    root=Path(__file__).resolve().parents[1]
    app=backend_source(root)
    storage=storage_source(root)
    html=(root/'static/index.html').read_text(encoding='utf-8')
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    activation=app[app.index('def _activate_project'):app.index('@app.get("/",response_class=HTMLResponse)')]
    assert 'preservation_check(full=False)' in activation
    assert 'integrity_check()' not in activation
    assert 'commit("Opened Breadmaker Editor"' not in activation
    assert "v12_content_migration" in storage
    assert 'seen_chunks=set(); seen_blocks=set(); seen_other=set()' in storage


def test_v4121_preservation_content_migration_is_marked_once(tmp_path,app_module_fixture):
    storage=app_module_fixture.storage
    project=tmp_path/'project'; project.mkdir(); (project/'assets').mkdir(); (project/'backups').mkdir()
    storage.configure_project(project)
    storage.init_db()
    with storage.tx() as con:
        assert con.execute("SELECT value FROM preservation_meta WHERE key='v12_content_migration'").fetchone()[0]=='1'
        # A completed migration marker must survive ordinary subsequent startup/schema init.
        before=con.execute("SELECT COUNT(*) FROM preservation_meta").fetchone()[0]
    storage.init_db()
    with storage.tx() as con:
        assert con.execute("SELECT value FROM preservation_meta WHERE key='v12_content_migration'").fetchone()[0]=='1'
        assert con.execute("SELECT COUNT(*) FROM preservation_meta").fetchone()[0]==before
