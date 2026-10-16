from __future__ import annotations

"""Process environment and stable runtime services.

No HTTP routes live here. Feature modules may import these stable objects directly
without importing the FastAPI composition module.
"""

import os
from pathlib import Path

from runtime import build_runtime
from security import hosted_guardrails

SOURCE_ROOT = Path(__file__).resolve().parents[1]
ROOT = SOURCE_ROOT.parent
STATIC = ROOT / "static"
LOCALES = ROOT / "locales"
CONFIG = ROOT / "config"
STORY_TEMPLATES_CONFIG = CONFIG / "story_templates.json"
FEATURE_FLAGS_CONFIG = CONFIG / "features.json"
METRO_VIEW_CONFIG = CONFIG / "metro_view.json"
CANVAS_CONFIG = CONFIG / "canvas.json"
APP_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()

_runtime_mode=str(os.environ.get('BREADMAKER_RUNTIME') or 'local').strip().lower()
if _runtime_mode=='local':
    # Keep the green local composition exactly on the existing Workspace/SQLite path.
    from workspace import workspace
    runtime = build_runtime(workspace)
else:
    # Hosted composition owns Firestore/GCS directly and must not instantiate a
    # Workspace merely as a compatibility object.
    workspace=None
    runtime=build_runtime(None)

_HOSTED_LIMITS=hosted_guardrails.HostedLimits.from_environment() if runtime.mode!='local' else None
