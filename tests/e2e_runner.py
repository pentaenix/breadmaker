"""Deterministic release gate for Breadmaker's real-browser workflows.

Each workflow runs in its own pytest process. Chromium, Playwright, FastAPI/uvicorn,
and the temporary workspace therefore have exactly one workflow lifetime and cannot
leak state or teardown callbacks into the next test. This is intentionally slower
than one shared browser and dramatically more reliable as a release gate.
"""
from __future__ import annotations

import ast
import os
import signal
import subprocess
import sys
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TEST_FILE = ROOT / "tests" / "test_e2e.py"
PROCESS_WATCHDOG_SECONDS = 15
SHUTDOWN_GRACE = 3


def _terminate_process_tree(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    if os.name == "posix":
        try: os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError: return
    else:
        proc.terminate()
    try:
        proc.wait(timeout=SHUTDOWN_GRACE); return
    except subprocess.TimeoutExpired:
        pass
    if os.name == "posix":
        try: os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError: return
    else:
        proc.kill()
    proc.wait(timeout=SHUTDOWN_GRACE)


def _workflow_names() -> list[str]:
    tree=ast.parse(TEST_FILE.read_text(encoding="utf-8"),filename=str(TEST_FILE))
    return [node.name for node in tree.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name.startswith("test_")]


def main() -> int:
    names=_workflow_names()
    if not names:
        print("No browser workflows found.",file=sys.stderr); return 2
    env=os.environ.copy(); env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"]="1"; env["PYTHONPATH"]=str(SRC)+(os.pathsep+env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    shared_home=Path(tempfile.mkdtemp(prefix="breadmaker-e2e-isolated-home-"))
    env["BREADMAKER_DURABLE_E2E_HOME"]=str(shared_home)
    try:
        for index,name in enumerate(names,1):
            target=f"{TEST_FILE}::{name}"
            proc=subprocess.Popen([sys.executable,"-m","pytest","-q",target],cwd=ROOT,env=env,start_new_session=(os.name=="posix"))
            try: code=proc.wait(timeout=PROCESS_WATCHDOG_SECONDS)
            except subprocess.TimeoutExpired:
                _terminate_process_tree(proc); print(f"\nBrowser pytest process failed to exit within {PROCESS_WATCHDOG_SECONDS}s after the per-test watchdog fired: {name}",file=sys.stderr,flush=True); return 124
            if code:
                print(f"\nBrowser workflow failed (exit {code}): {name}",file=sys.stderr,flush=True); return code
            print(f"[{index}/{len(names)}] {name} ✓",flush=True)
        print(f"{len(names)} isolated browser workflows passed.",flush=True); return 0
    finally:
        shutil.rmtree(shared_home,ignore_errors=True)


if __name__ == "__main__": raise SystemExit(main())
