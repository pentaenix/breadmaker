from source_contracts import frontend_styles_source_from_styles_path
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]




def test_v81813_durable_project_fixture_uses_browser_lease_identity():
    durable=(ROOT/'tests/test_e2e.py').read_text(encoding='utf-8')
    fixture=durable[durable.index('def durable_fresh_project('):durable.index('@pytest.fixture(scope="module")',durable.index('def durable_fresh_project('))]
    assert 'headers={"X-Breadmaker-Client":"e2e-tab"}' in fixture


def test_v81813_durable_shutdown_stops_heartbeat_before_releasing_lease():
    durable=(ROOT/'tests/test_e2e.py').read_text(encoding='utf-8')
    start=durable.index('def _shutdown_page(')
    end=durable.index('def test_browser_',start)
    shutdown=durable[start:end]
    stop=shutdown.index('__breadmakerE2EShutdown')
    release=shutdown.index('/api/lease/release')
    close=shutdown.index('page.close()')
    assert stop < release < close
    assert '__breadmakerBridge' in shutdown
