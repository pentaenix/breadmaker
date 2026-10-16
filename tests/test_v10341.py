from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v10341_release_and_schema_remain_explicit():
    assert tuple(map(int, read("VERSION").strip().split("."))) >= (10, 3, 41)
    assert "APP_SCHEMA_VERSION = 42" in read("src/workspace/constants.py")


def test_v10341_local_launcher_is_safe_on_macos_bash_with_nounset():
    launcher = read("run")
    # macOS still ships Bash 3.2. Under `set -u`, expanding an empty array can
    # raise "unbound variable". Build the optional Uvicorn flags with ordinary
    # positional parameters instead of an optional Bash array.
    assert "set -euo pipefail" in launcher
    assert "UVICORN_ACCESS_ARGS=()" not in launcher
    assert "${UVICORN_ACCESS_ARGS[@]}" not in launcher
    assert 'set -- -m uvicorn src.server:app --host 127.0.0.1 --port "$PORT"' in launcher
    assert 'set -- "$@" --no-access-log' in launcher
    assert '"$PYTHON_RUNTIME" "$@"' in launcher
