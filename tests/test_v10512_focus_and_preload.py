"""M12D corrective contracts: preserve rapid milestone entry and 10s item budgets.

The owner, not the assistant, runs verification and the existing 92-workflow E2E.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_v10512_milestone_description_autofocus_cannot_steal_next_title():
    source = (ROOT / "static/js/features/tasks.js").read_text(encoding="utf-8")
    assert "const focusMilestoneDescriptionIfIdle=(id,input)=>requestAnimationFrame" in source
    assert "!input.isConnected||document.activeElement!==input||input.value.trim()" in source
    assert "focusMilestoneDescriptionIfIdle(created.id,input)" in source
    assert "focusMilestoneDescriptionIfIdle(milestone.id,input)" in source
    assert "requestAnimationFrame(()=>$(`[data-milestone-id=" not in source


def test_v10512_cold_app_prewarm_is_module_scoped_and_watchdogs_stay_strict():
    verifier = (ROOT / "scripts/verify_release.py").read_text(encoding="utf-8")
    conftest = (ROOT / "tests/conftest.py").read_text(encoding="utf-8")
    prewarm = verifier.split("PREWARM_APP_CORE_MODULES = {", 1)[1].split("}", 1)[0]
    for module in ("test_v8173.py", "test_v10326.py", "test_v700.py"):
        assert f'"{module}"' in prewarm
    assert 'env["BREADMAKER_VERIFY_PRELOAD_APP"]="1"' in verifier
    assert 'os.environ.get("BREADMAKER_VERIFY_PRELOAD_APP") == "1"' in conftest
    assert "CORE_API_TEST_TIMEOUT_SECONDS = 10.0" in conftest
    assert "BROWSER_TEST_TIMEOUT_SECONDS = 15.0" in conftest
    assert "NO_CONTENTION_CORE_MODULES = {" in verifier
