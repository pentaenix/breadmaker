from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]


def test_v10318_marker_hover_uses_event_owned_liveness_not_delayed_css_hover_query():
    source=(ROOT/'static/js/features/manuscript/annotations.js').read_text(encoding='utf-8')
    match=re.search(r'function queueMarkerPanelHover\(marker,row\)\{.*?\n?\}',source,re.S)
    assert match, 'queueMarkerPanelHover must remain discoverable.'
    body=match.group(0)
    assert 'MARKER_PANEL_HOVER_DELAY_MS=300' in source
    assert 'markerPanelHoverSourceId!==id||!row.isConnected' in body
    assert "matches(':hover')" not in body


def test_v10318_durable_marker_hover_waits_for_observable_visibility():
    e2e=(ROOT/'tests/test_e2e.py').read_text(encoding='utf-8')
    start=e2e.index('def test_browser_v4167_marker_description_hover_only_when_present')
    end=e2e.find('\ndef ',start+4)
    body=e2e[start:end if end!=-1 else None]
    assert "flyer.wait_for(state='visible',timeout=1500)" in body
    assert "flyer.wait_for(state='hidden',timeout=1500)" in body
    assert 'wait_for_timeout(340)' not in body


def test_v10318_release_documents_hover_reliability_fix():
    readme=(ROOT/'README.md').read_text(encoding='utf-8').lower()
    architecture=(ROOT/'ARCHITECTURE.md').read_text(encoding='utf-8').lower()
    assert '10.3.18' in readme and 'marker hover' in readme
    assert '10.3.18' in architecture and 'does not reopen' in architecture
