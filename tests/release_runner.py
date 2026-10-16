"""Compatibility entry point for Breadmaker's release verification.

The authoritative verifier lives in scripts/verify_release.py. Keeping this tiny
wrapper prevents older local workflows from silently running a weaker gate.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.verify_release import main

if __name__ == "__main__":
    raise SystemExit(main())
