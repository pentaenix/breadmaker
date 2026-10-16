from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]




def test_v81810_durable_kitchen_sink_is_split_without_dropping_workspace_coverage():
    durable = (ROOT / 'tests/test_e2e.py').read_text(encoding='utf-8')
    assert 'def test_browser_writing_undo_find_and_whole_book(' in durable
    assert 'def test_browser_canvas_tasks_maps_and_project_center(' in durable
    assert 'test_browser_writing_undo_find_whole_book_canvas_tasks_maps_and_project_center' not in durable
    for contract in ('#viewFindReplaceBtn', '#bookViewBtn', '#addNodePanelBtn', "[data-view=\"tasks\"]", "[data-view=\"maps\"]", '#projectMenuSettings'):
        assert contract in durable


def test_v81810_hard_watchdog_preserves_per_item_budget_policy_and_last_resort_reason():
    hooks = (ROOT / 'tests/conftest.py').read_text(encoding='utf-8')
    assert 'CORE_API_TEST_TIMEOUT_SECONDS = 10.0' in hooks
    assert 'BROWSER_TEST_TIMEOUT_SECONDS = 15.0' in hooks
    assert '_timeout_seconds_for_item' in hooks
    assert '_TIMEOUT_KILL_GRACE_SECONDS = 6.0' in hooks
    assert '_append_verifier_record(item.nodeid,phase,"failed",message+' in hooks
    assert 'pytest did not unwind within' in hooks
    assert '_append_verifier_record(item.nodeid,phase,"failed",cleanup_message+' in hooks


def test_v81810_canvas_persistence_contract_uses_a_bounded_source_slice():
    contract = (ROOT / 'tests/test_v8189.py').read_text(encoding='utf-8')
    assert 'end = js.index("$(\'#editCanvasDetailsBtn\')", start)' in contract
    assert "select.index('cancelQueuedCanvasViewportPersist()') < select.index('persistCurrentCanvasViewport().catch(console.warn)')" in contract
    assert "'await persistCurrentCanvasViewport().catch(console.warn)' not in select" in contract
