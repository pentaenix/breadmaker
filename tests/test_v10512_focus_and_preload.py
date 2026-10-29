"""M12D corrective contracts: preserve rapid milestone entry and 10s item budgets.

The owner, not the assistant, runs verification and the existing 92-workflow E2E.
"""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_v10512_milestone_description_autofocus_cannot_steal_next_title():
    source = (ROOT / "static/js/features/tasks.js").read_text(encoding="utf-8")
    assert "focusMilestoneDescriptionIfIdle(created.id,input)" in source
    assert "focusMilestoneDescriptionIfIdle(milestone.id,input)" in source
    helper = 'const focusMilestoneDescriptionIfIdle=' + source.split(
        'const focusMilestoneDescriptionIfIdle=', 1)[1].split('const addMilestone=', 1)[0]
    result = subprocess.run(['node', '-e', '''
      const assert=require('node:assert/strict'),frames=[];
      const input={isConnected:true,value:''},other={};
      const document={activeElement:input},CSS={escape:String};
      const description={focus:()=>{document.activeElement=description}};
      const $=()=>description;
      const requestAnimationFrame=callback=>frames.push(callback);
    ''' + helper + '''
      focusMilestoneDescriptionIfIdle('draft_1',input);
      // A later explicit focus of the next empty title must remain the user's.
      document.activeElement=input;
      for(const callback of frames)callback();
      assert.equal(document.activeElement,input);
      focusMilestoneDescriptionIfIdle('draft_2',input);
      assert.equal(document.activeElement,description);
      // Pending persisted creates must not take focus from newer input or UI.
      document.activeElement=input;input.value='Next title';
      focusMilestoneDescriptionIfIdle('saved',input);
      assert.equal(document.activeElement,input);
      input.value='';document.activeElement=other;
      focusMilestoneDescriptionIfIdle('saved',input);
      assert.equal(document.activeElement,other);
      document.activeElement=input;input.isConnected=false;
      focusMilestoneDescriptionIfIdle('saved',input);
      assert.equal(document.activeElement,input);
    '''], capture_output=True, text=True, timeout=2)
    assert result.returncode == 0, result.stderr


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
