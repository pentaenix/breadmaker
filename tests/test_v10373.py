"""M9C1 followup: current release metadata is synchronized without changing historical tests.

Source-only check; the user operates the actual Breadmaker verifier.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]




def test_v10373_local_schema_and_cloud_boundaries_stay_fixed():
    assert 'APP_SCHEMA_VERSION = 42' in (ROOT / 'src/workspace/constants.py').read_text(encoding='utf-8')
    assert 'SCHEMA_VERSION = 42' in (ROOT / 'src/storage/schema.py').read_text(encoding='utf-8')
    local_requirements = (ROOT / 'requirements.txt').read_text(encoding='utf-8')
    assert 'google-cloud-firestore' not in local_requirements
    assert 'google-cloud-storage' not in local_requirements
