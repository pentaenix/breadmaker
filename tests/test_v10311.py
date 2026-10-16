from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]


def test_v10311_checkpoint_hover_uses_event_owned_liveness_not_delayed_css_hover_query():
    source=(ROOT/'static/js/features/checkpoints/workspace.js').read_text(encoding='utf-8')
    match=re.search(r'function queueCheckpointPanelHover\(cp,row\)\{.*?\n?\}',source,re.S)
    assert match, 'queueCheckpointPanelHover must remain discoverable.'
    body=match.group(0)
    assert 'CHECKPOINT_PANEL_HOVER_DELAY_MS=300' in source
    assert 'checkpointPanelHoverSourceId!==id||!row.isConnected' in body
    assert "matches(':hover')" not in body


def test_v10311_durable_checkpoint_hover_waits_for_observable_visibility():
    e2e=(ROOT/'tests/test_e2e.py').read_text(encoding='utf-8')
    start=e2e.index('def test_browser_v4175_checkpoint_cards_hover_details_and_apply_to_live_chapter')
    end=e2e.find('\ndef ',start+4)
    body=e2e[start:end if end!=-1 else None]
    assert "flyer.wait_for(state='visible',timeout=1500)" in body
    assert 'wait_for_timeout(340)' not in body


def test_v10311_release_documents_reliability_fix_without_reopening_architecture():
    readme=(ROOT/'README.md').read_text(encoding='utf-8').lower()
    architecture=(ROOT/'ARCHITECTURE.md').read_text(encoding='utf-8').lower()
    assert '10.3.11' in readme and 'checkpoint hover' in readme
    assert '10.3.11' in architecture and 'does not reopen' in architecture
