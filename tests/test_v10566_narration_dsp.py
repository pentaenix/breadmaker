"""Numerical audio checks belong in Core/API, not the three-second unit lane."""
import shutil
import subprocess
from pathlib import Path


def test_narration_export_and_pitch_signal():
    root=Path(__file__).resolve().parents[1]
    node=shutil.which('node')
    assert node, 'Node is required for narration DSP regressions'
    result=subprocess.run([node,str(root/'tools/narration/test-export-pitch.mjs')],
                          cwd=root,capture_output=True,text=True,timeout=8)
    assert result.returncode==0,result.stdout+result.stderr
