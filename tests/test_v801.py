"""Legacy smoke: shell still references its primary CSS and JS entrypoints.

Exact cache identities are checked once by the release-identity preflight.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_v801_shell_includes_primary_assets():
    html = (ROOT / "static/index.html").read_text(encoding="utf-8")
    assert '/static/styles.css?v=' in html
    assert '/static/app.js?v=' in html
