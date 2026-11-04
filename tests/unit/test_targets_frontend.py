"""Pure Targets calendar/activity rules run in the fast standard unit lane."""
from pathlib import Path
import shutil
import subprocess


def test_targets_calendar_activity_progress_and_history():
    root=Path(__file__).resolve().parents[2]
    result=subprocess.run([shutil.which('node') or 'node',str(root/'tools/targets/test-model.cjs')],cwd=root,capture_output=True,text=True,timeout=2)
    assert result.returncode==0,result.stdout+result.stderr
