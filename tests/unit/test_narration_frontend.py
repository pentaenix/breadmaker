"""Run source-only narration regressions in the standard fast test lane."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('name', ['preferences', 'playback', 'progress', 'punctuation', 'lifecycle', 'download'])
def test_narration_frontend_behavior(name):
    node = shutil.which('node')
    assert node, 'Node is required for narration frontend regressions'
    result = subprocess.run([node, str(ROOT / 'tools/narration' / f'test-{name}.mjs')],
                            cwd=ROOT, capture_output=True, text=True, timeout=2)
    assert result.returncode == 0, result.stdout + result.stderr
