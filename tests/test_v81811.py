from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]




def test_v81811_durable_verifier_resumes_unrecorded_tail_in_same_workspace():
    source=(ROOT/'scripts/verify_release.py').read_text(encoding='utf-8')
    durable=(ROOT/'tests/test_e2e.py').read_text(encoding='utf-8')
    assert 'BREADMAKER_DURABLE_E2E_HOME' in source and 'BREADMAKER_DURABLE_E2E_HOME' in durable
    assert '_durable_recorded_names' in source
    assert 'targets = [str(durable_path)] if attempt == 1 else' in source
    assert 'missing = [name for name in names if name not in recorded_names]' in source
    assert '_cleanup_abandoned_process_group(proc)' in source
    assert 'call_done = any' in source and 'setup_terminal = any' in source
    assert 'BREADMAKER_DURABLE_RESUME_STALLED' in source


def test_v81811_durable_contracts_follow_current_collection_theme_and_map_ui():
    durable=(ROOT/'tests/test_e2e.py').read_text(encoding='utf-8')
    assert ".filter(has_text='Add Map').click()" in durable
    assert "dusk['theme']=='dusk'" in durable and "dark['theme']=='dark'" in durable
    assert "line_row.locator('.map-tree-line-short').is_hidden()" in durable
    assert "#mapViewPopover #mapZoomFit" in durable
    assert 'test_browser_v412_history_compare_is_integrated_and_read_only' in durable
    assert 'test_browser_v412_history_can_pin_checkpoint_and_is_a_split_source' in durable
