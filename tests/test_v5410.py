from source_contracts import frontend_source_from_app_path
from pathlib import Path


def test_v5410_task_linker_hierarchy_static_contract():
    root = Path(__file__).resolve().parents[1]
    html = (root / 'static' / 'index.html').read_text(encoding='utf-8')
    js = frontend_source_from_app_path(root / 'static' / 'app.js')
    readme = (root / 'README.md').read_text(encoding='utf-8')
    architecture = (root / 'ARCHITECTURE.md').read_text(encoding='utf-8')

    assert tuple(map(int,(root / 'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 4, 16)
    assert 'styles.css?v=5.4.16' in html and 'app.js?v=5.4.16' in html

    # New Task is an action inside Task, never a first-level relationship family.
    assert "{value:'new_task',label:'New Task'}" not in js
    assert "allowTaskCreation:sourceType==='node'" in js
    assert "openTasks.length||allowTaskCreation" in js
    assert 'id="createLinkedTask"' in js
    assert 'No open Tasks yet.' in js
    assert "showNestedModal('New Task'" in js
    assert "target_type:'task'" in js

    assert '**Add link → Task**' in readme
    assert 'New Task is never a first-level relationship type' in architecture
