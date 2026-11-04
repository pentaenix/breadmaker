"""Exercise the browser-independent foundation with synthetic time and memory storage."""
from pathlib import Path
import shutil
import subprocess


def test_app_telemetry_foundation_behavior():
    root = Path(__file__).resolve().parents[2]
    node = shutil.which('node')
    assert node, 'Node is required for the synthetic telemetry foundation tests'
    result = subprocess.run([node, '--test', 'tests/app_telemetry_foundation.cjs'],
                            cwd=root, capture_output=True, text=True, timeout=2)
    assert result.returncode == 0, result.stdout + result.stderr
