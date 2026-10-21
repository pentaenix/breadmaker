from __future__ import annotations

import _thread
import faulthandler
import json
import os
import signal
import shutil
import sys
import tempfile
import time
import threading
from pathlib import Path

APP_ROOT=Path(__file__).resolve().parents[1]
SRC_ROOT=APP_ROOT / "src"
if str(SRC_ROOT) not in sys.path: sys.path.insert(0,str(SRC_ROOT))
TESTS_ROOT=APP_ROOT / "tests"
if str(TESTS_ROOT) not in sys.path: sys.path.insert(0,str(TESTS_ROOT))
_existing_pythonpath=os.environ.get("PYTHONPATH","")
os.environ["PYTHONPATH"]=str(SRC_ROOT)+(os.pathsep+_existing_pythonpath if _existing_pythonpath else "")

# The workspace singleton reads BREADMAKER_HOME at import time, so establish an
# isolated suite home before importing the application.
TEST_HOME = Path(tempfile.mkdtemp(prefix="breadmaker-v4-tests-"))
os.environ["BREADMAKER_HOME"] = str(TEST_HOME)

import pytest


_APP_MODULE = None

def _load_app_module():
    # Unit tests deliberately avoid importing the full FastAPI/workspace graph.
    # Application import remains lazy for API/browser fixtures so the first
    # release-verification stage stays a true fast unit layer.
    global _APP_MODULE
    if _APP_MODULE is None:
        import app as app_module
        _APP_MODULE = app_module
    return _APP_MODULE



# The durable module shares one browser across 92 workflows. Cold Playwright /
# Chromium launch is a suite prerequisite, not a single writing-workflow test.
# Start that shared browser BEFORE per-item watchdogs arm, with its OWN bounded
# 30s startup deadline and 40s hard process-stop fallback. Individual items
# retain their unmodified 15s setup/call/teardown budget. Only the full durable
# verification subprocess opts in; ordinary pytest/current-browser are unchanged.
_DURABLE_BROWSER_STARTUP_SECONDS = 30.0
_DURABLE_BROWSER_HARD_STOP_SECONDS = 40.0


def _close_durable_browser(config):
    prepared = getattr(config, "_breadmaker_durable_browser", None)
    config._breadmaker_durable_browser = None
    if prepared is None:
        return
    from scripts.browser_runtime import close_browser
    close_browser(*prepared)


def _prepare_durable_browser(config):
    from scripts.browser_runtime import start_browser

    started = time.monotonic()
    playwright, browser, _executable = start_browser(
        startup_seconds=_DURABLE_BROWSER_STARTUP_SECONDS,
        hard_seconds=_DURABLE_BROWSER_HARD_STOP_SECONDS,
    )
    config._breadmaker_durable_browser = (playwright, browser)
    ready_file = os.environ.get("BREADMAKER_DURABLE_BROWSER_READY_FILE")
    if ready_file:
        Path(ready_file).write_text("ready\n", encoding="utf-8")
    print(f"BREADMAKER_DURABLE_BROWSER_PRESTART_OK: {time.monotonic() - started:.2f}s", flush=True)


def pytest_sessionfinish(session, exitstatus):
    # Safety cleanup if collection/setup failed before the durable module fixture
    # took ownership of the prepared browser.
    _close_durable_browser(session.config)


def pytest_sessionstart(session):
    # Selected heavyweight Core/API contract modules may ask the verifier to pay
    # one-time application import/bootstrap cost before the first item's hard
    # setup/call/teardown budget starts. Unit tests never set this flag.
    if os.environ.get("BREADMAKER_VERIFY_PRELOAD_APP") == "1":
        _load_app_module()
    if os.environ.get("BREADMAKER_DURABLE_VERIFY") == "1":
        _prepare_durable_browser(session.config)


@pytest.fixture(scope="session")
def app_module_fixture():
    return _load_app_module()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    app_module = _load_app_module()
    with TestClient(app_module.app) as c:
        yield c


@pytest.fixture
def fresh_project(client):
    name = "Test Book " + next(tempfile._get_candidate_names())
    response = client.post("/api/projects", json={"name": name})
    assert response.status_code == 200, response.text
    boot = client.get("/api/bootstrap")
    assert boot.status_code == 200
    data = boot.json()
    return {"name": name, "project": response.json()["project"], "bootstrap": data}


def assert_ok(response):
    assert response.status_code < 400, f"{response.status_code}: {response.text}"
    return response.json() if response.headers.get("content-type", "").startswith("application/json") else response


# Release reliability invariant: every individual pytest item has one hard
# execution budget across setup + call + teardown. Unit items stay at 3s,
# Core/API items at 10s, and browser contracts get 15s because their isolated server/Chromium lifecycle and
# real UI interactions have higher startup/teardown variance. The watchdog is
# deliberately DISARMED while pytest formats reports so an already-known assertion
# cannot be replaced by a timeout raised inside traceback rendering.
UNIT_TEST_TIMEOUT_SECONDS = 3.0
CORE_API_TEST_TIMEOUT_SECONDS = 10.0
BROWSER_TEST_TIMEOUT_SECONDS = 15.0
_TIMEOUT_KILL_GRACE_SECONDS = 6.0


class BreadmakerTestTimeout(AssertionError):
    """Raised in the main test thread when one pytest item exceeds its deadline."""


def _timeout_seconds_for_item(item) -> float:
    path=Path(str(getattr(item,'path',getattr(item,'fspath',''))))
    name=path.name
    if 'unit' in path.parts and 'tests' in path.parts:
        return UNIT_TEST_TIMEOUT_SECONDS
    if name=='test_e2e.py' or name.endswith('_browser.py'):
        return BROWSER_TEST_TIMEOUT_SECONDS
    return CORE_API_TEST_TIMEOUT_SECONDS


def _item_timeout_seconds(item) -> float:
    return float(getattr(item,'_breadmaker_timeout_seconds',_timeout_seconds_for_item(item)))


def _timeout_message(item) -> str:
    seconds=_item_timeout_seconds(item)
    return (
        f"BREADMAKER_TEST_TIMEOUT: {item.nodeid} exceeded the hard "
        f"{seconds:g}-second per-test limit"
    )


def _item_budget_remaining(item) -> float:
    return float(getattr(item,"_breadmaker_execution_budget",_item_timeout_seconds(item)))


def _append_verifier_record(nodeid: str, phase: str, outcome: str, reason: str = "") -> None:
    """Append a phase result even when pytest cannot finish constructing a report.

    The hard watchdog uses this immediately before its last-resort process exit.
    That keeps the exact timeout/cleanup cause in the release digest instead of
    degrading to an opaque ``setup passed; call missing`` accounting failure.
    """
    path=os.environ.get("BREADMAKER_VERIFY_RESULT_JSONL")
    if not path:
        return
    record={"nodeid":nodeid,"phase":phase,"outcome":outcome,"reason":reason}
    try:
        with open(path,"a",encoding="utf-8") as handle:
            handle.write(json.dumps(record,ensure_ascii=False)+"\n")
            handle.flush()
    except Exception:
        pass


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_setup(item):
    item._breadmaker_timeout_seconds=_timeout_seconds_for_item(item)
    item._breadmaker_execution_budget=item._breadmaker_timeout_seconds
    yield from _budget_yield(item,"setup")


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_call(item):
    yield from _budget_yield(item,"call")


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_teardown(item,nextitem):
    if getattr(item,"_breadmaker_timed_out",False):
        # The item is already failed. Give fixture/TestClient/Chromium teardown a
        # bounded cleanup window without producing a second timeout failure.
        yield from _cleanup_yield(item,"teardown")
    else:
        yield from _budget_yield(item,"teardown")


def _cleanup_yield(item,phase):
    """Give a failed/timed-out item bounded cleanup without eagerly killing pytest.

    Cleanup is outside the item's execution budget. On POSIX, first interrupt a
    Python-level teardown that exceeds the grace window so pytest can record the
    teardown and continue with later items. A second grace window remains the hard
    process-stop fallback for teardown code stuck below Python's signal boundary.
    """
    done=threading.Event()
    grace=_TIMEOUT_KILL_GRACE_SECONDS
    can_signal=hasattr(signal,"SIGALRM") and threading.current_thread() is threading.main_thread()
    previous_handler=signal.getsignal(signal.SIGALRM) if can_signal else None
    cleanup_message=f"BREADMAKER_CLEANUP_TIMEOUT: {item.nodeid} exceeded {grace:g}s cleanup grace [{phase}]"
    if can_signal:
        def _cleanup_alarm(signum,frame):
            raise BreadmakerTestTimeout(cleanup_message)
        signal.signal(signal.SIGALRM,_cleanup_alarm)

    def _watchdog():
        if done.wait(grace): return
        try: os.write(2,("\n"+cleanup_message+"\n").encode("utf-8","replace"))
        except OSError: pass
        if can_signal:
            try: os.kill(os.getpid(),signal.SIGALRM)
            except OSError: pass
            if done.wait(grace): return
        try:
            faulthandler.dump_traceback(file=sys.stderr,all_threads=True);sys.stderr.flush()
        except Exception: pass
        _append_verifier_record(item.nodeid,phase,"failed",cleanup_message+"; pytest did not unwind before the hard-stop fallback")
        os._exit(124)

    threading.Thread(target=_watchdog,name=f"breadmaker-cleanup-timeout:{item.nodeid}:{phase}",daemon=True).start()
    try:
        yield
    finally:
        done.set()
        if can_signal: signal.signal(signal.SIGALRM,previous_handler)

def _budget_yield(item,phase):
    """Spend only real setup/call/teardown time from the item's assigned budget."""
    remaining=_item_budget_remaining(item)
    message=_timeout_message(item)
    if remaining<=0:
        raise BreadmakerTestTimeout(message)
    phase_started=time.monotonic()
    done=threading.Event()
    can_signal=hasattr(signal,"SIGALRM") and threading.current_thread() is threading.main_thread()
    previous_handler=signal.getsignal(signal.SIGALRM) if can_signal else None
    if can_signal:
        def _alarm_handler(signum,frame):
            item._breadmaker_timed_out=True
            raise BreadmakerTestTimeout(message)
        signal.signal(signal.SIGALRM,_alarm_handler)

    def _watchdog():
        if done.wait(remaining): return
        try: os.write(2,("\n"+message+f" [{phase}]\n").encode("utf-8","replace"))
        except OSError: pass
        if can_signal:
            try: os.kill(os.getpid(),signal.SIGALRM)
            except OSError: pass
        else:
            item._breadmaker_timed_out=True
            try: _thread.interrupt_main()
            except Exception: pass
        if done.wait(_TIMEOUT_KILL_GRACE_SECONDS): return
        try:
            faulthandler.dump_traceback(file=sys.stderr,all_threads=True);sys.stderr.flush()
        except Exception: pass
        _append_verifier_record(item.nodeid,phase,"failed",message+f" [{phase}]; pytest did not unwind within {_TIMEOUT_KILL_GRACE_SECONDS:g}s after timeout interrupt")
        os._exit(124)

    threading.Thread(target=_watchdog,name=f"breadmaker-test-timeout:{item.nodeid}:{phase}",daemon=True).start()
    try:
        yield
    finally:
        done.set()
        elapsed=max(0.0,time.monotonic()-phase_started)
        item._breadmaker_execution_budget=max(0.0,remaining-elapsed)
        if can_signal: signal.signal(signal.SIGALRM,previous_handler)


def _compact_failure_reason(report) -> str:
    """Preserve the failing expression/predicate as well as pytest's short reason."""
    try:
        text=getattr(report,"longreprtext","") or str(getattr(report,"longrepr","") or "")
    except Exception:
        text=""
    raw_lines=[line.rstrip() for line in str(text).splitlines() if line.strip()]

    def cleaned(line:str)->str:
        value=line.strip()
        if value.startswith("E   "): value=value[4:].strip()
        return value

    def useful(line:str)->bool:
        low=line.lower()
        if line.startswith("+ where ") or low in {"waiting 500ms","waiting 100ms","waiting 250ms"}: return False
        if line.startswith(("File ","self = ","page = ","client = ")): return False
        return True

    # Pytest marks the actual source expression with a leading '>'. Keep it: for
    # `assert False`, compound assertions, and Playwright timeout wrappers this is
    # often the only line that explains what was really being checked.
    source_candidates=[]
    for raw in raw_lines:
        stripped=raw.strip()
        source=stripped.lstrip("> ").strip()
        # Depending on traceback style pytest may omit the visual `>` marker.
        # Keep source-looking assertion/wait lines either way.
        if useful(source) and ("assert " in source or "wait_for_function(" in source or "wait_for_selector(" in source):
            source_candidates.append(source)

    # Prefer pytest's structured traceback entries when available. They retain the
    # exact failing source expression even when longreprtext compresses it away.
    try:
        trace=getattr(getattr(report,"longrepr",None),"reprtraceback",None)
        for entry in reversed(list(getattr(trace,"reprentries",[]) or [])):
            for raw in reversed(list(getattr(entry,"lines",[]) or [])):
                source=str(raw).strip().lstrip("> ").strip()
                if useful(source) and ("assert " in source or "wait_for_function(" in source or "wait_for_selector(" in source):
                    source_candidates.append(source)
    except Exception:
        pass

    preferred=[]
    tokens=("AssertionError","assert ","expected ","actual ","waiting for locator","element is not visible","locator resolved to","TimeoutError","TypeError","ReferenceError","BREADMAKER_","Playwright")
    for raw in raw_lines:
        line=cleaned(raw)
        if useful(line) and any(token.lower() in line.lower() for token in tokens):
            preferred.append(line)

    terminal=""
    if preferred:
        for line in reversed(preferred):
            if any(token in line.lower() for token in ("assert ","expected ","actual ","waiting for locator","element is not visible","referenceerror","typeerror")):
                terminal=line;break
        if not terminal: terminal=preferred[-1]
    if not terminal:
        for raw in reversed(raw_lines):
            line=cleaned(raw)
            if useful(line): terminal=line;break
    terminal=terminal or "pytest failure"

    source=""
    low_terminal=terminal.lower()
    if source_candidates:
        if "wait_for_function" in low_terminal or "timeout" in low_terminal:
            source=next((line for line in reversed(source_candidates) if "wait_for_function(" in line or "wait_for_selector(" in line),"")
        if not source and (terminal in {"assert False","AssertionError: assert False"} or terminal.startswith("assert (")):
            source=next((line for line in reversed(source_candidates) if "assert " in line),"")
        if not source and terminal.startswith("AssertionError"):
            source=next((line for line in reversed(source_candidates) if "assert " in line),"")
    if source and source not in terminal:
        return f"{source} -> {terminal}"[:1200]
    return terminal[:1200]


def _append_verifier_report(report) -> None:
    _append_verifier_record(
        report.nodeid,
        report.when,
        report.outcome,
        _compact_failure_reason(report) if report.failed else "",
    )


@pytest.hookimpl(trylast=True)
def pytest_runtest_logreport(report):
    """Emit compact machine-readable phase + durable JSONL result metadata."""
    _append_verifier_report(report)
    if not report.failed:
        return
    try:
        os.write(2,f"\nBREADMAKER_FAILURE_PHASE::{report.nodeid}::{report.when}\n".encode("utf-8","replace"))
        reason=_compact_failure_reason(report)
        os.write(2,f"BREADMAKER_FAILURE_REASON::{report.nodeid}::{report.when}::{reason}\n".encode("utf-8","replace"))
    except OSError:
        reason=_compact_failure_reason(report)
    # A timed-out Playwright call can leave the synchronous driver in an undefined
    # state even when pytest catches the exception. The durable verifier has a
    # resumable process boundary, so stop this worker after recording the call
    # failure instead of letting one poisoned item manufacture downstream timeouts.
    if (os.environ.get("BREADMAKER_DURABLE_VERIFY")=="1" and report.when=="call"
            and "BREADMAKER_TEST_TIMEOUT" in reason):
        try: sys.stderr.flush();sys.stdout.flush()
        finally: os._exit(124)

