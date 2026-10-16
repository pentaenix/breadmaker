from __future__ import annotations
from source_contracts import frontend_source_from_app_path

from pathlib import Path


def test_v549_structure_hover_and_node_relationship_static_contract():
    root = Path(__file__).resolve().parents[1]
    html = (root / 'static' / 'index.html').read_text(encoding='utf-8')
    js = frontend_source_from_app_path(root / 'static' / 'app.js')
    architecture = (root / 'ARCHITECTURE.md').read_text(encoding='utf-8')

    assert tuple(map(int,(root / 'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 4, 16)
    assert 'styles.css?v=5.4.16' in html and 'app.js?v=5.4.16' in html

    # Structure descriptions belong to the interactive control, not the section row/label.
    assert 'id="structureOrderToggle"' in js and 'aria-label="Show Structure" data-structure-description=' in js
    assert 'id="newCanvasVersion"' in js and 'aria-label="New Structure Version" data-structure-description=' in js
    assert 'id="reviewStructureChanges"' in js and 'data-structure-description=' in js
    assert 'structure-toggle-row structure-description-target' not in js
    assert 'structure-section-head structure-description-target' not in js
    assert 'structure-review-hover structure-description-target' not in js

    # Structural inspector removes permanent prose for direct-manipulation/automatic behavior.
    assert 'This tile is linked to a real ${scope} in the manuscript.' not in js
    assert 'Text switches automatically between light and dark for contrast.' not in js

    # Canvas Card relationships have one path: Linked items -> Add link.
    assert 'id="nodeTaskSelect"' not in js
    assert 'id="nodeLinkTask"' not in js
    assert 'id="nodeCreateTask"' not in js
    assert "{value:'new_task',label:'New Task'}" not in js
    assert 'id="createLinkedTask"' in js
    assert "target_type:'task'" in js
    assert 'interactive control itself as the only trigger' in architecture
    assert 'do not render a parallel Tasks section' in architecture
