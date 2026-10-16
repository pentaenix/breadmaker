from source_contracts import frontend_source_from_app_path
from pathlib import Path


def test_v4164_manuscript_mutations_preserve_viewport_and_marker_delete_does_not_flash():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    html=(root/'static/index.html').read_text(encoding='utf-8')

    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert 'function captureManuscriptViewport()' in js
    assert 'function restoreManuscriptViewport(snapshot)' in js
    assert 'async function preserveManuscriptViewport(action)' in js
    reload_start=js.index('async function reloadManuscriptView()')
    reload_body=js[reload_start:reload_start+520]
    assert 'captureManuscriptViewport()' in reload_body
    assert 'restoreManuscriptViewport(viewport)' in reload_body
    # True navigation may still start at the top; only in-place reloads restore the old anchor.
    assert "$('#documentScroller').scrollTop=0" in js
    # The Configure Marker frame is dismissed before the async delete starts, so it cannot flash back.
    delete_fragment=js[js.index("confirmDialog('Delete Marker'"):js.index("toast('Marker deleted')")]
    assert delete_fragment.index('dismissModalTree()') < delete_fragment.index('await withStructuralUndo')
    # Checkpoint creation explicitly protects the manuscript viewport even though it only rerenders its panel.
    checkpoint_fragment=js[js.index("$('#checkpointCreateConfirm').onclick"):js.index("// -------------------- history / cuts / principles")]
    assert 'preserveManuscriptViewport(async()=>{' in checkpoint_fragment
