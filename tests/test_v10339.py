from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v10339_release_and_schema_remain_explicit():
    assert tuple(map(int, read("VERSION").strip().split("."))) >= (10, 3, 39)
    assert "APP_SCHEMA_VERSION = 42" in read("src/workspace/constants.py")


def test_v10339_staging_rate_limit_module_import_is_complete():
    pipeline = read("src/backend/request_pipeline.py")
    assert "import ipaddress" in pipeline
    assert "ipaddress.ip_network(" in pipeline
    assert "ipaddress.ip_address(" in pipeline


def test_v10339_verifier_reports_collection_abort_as_failure():
    verifier = read("scripts/verify_release.py")
    assert "fatal_error: str | None = None" in verifier
    assert "========== VERIFIER FATAL ERROR ==========" in verifier
    assert "CORE COLLECTION FAILED:" in verifier
    assert "DISCOVERY FAILED:" in verifier
    assert "and not fatal_error" in verifier
