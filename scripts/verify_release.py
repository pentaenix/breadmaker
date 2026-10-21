"""Breadmaker Editor release verification orchestrator.

8.18.27 keeps the first-class unit gate and transparent browser retry while hardening browser startup against late boot/view restoration races:
- ordinary core/API contracts run as parallel module jobs while reliability items remain exact-item isolated and the scale contract runs in a dedicated no-contention lane;
- current browser contracts remain isolated, but the durable tests/test_e2e.py workflow
  keeps shared server/browser startup for speed while each pytest item activates a fresh project;
- every job has a process-level watchdog in addition to the per-test watchdog in
  tests/conftest.py;
- a live progress bar/heartbeat shows that the verifier is still advancing and names the most recently completed failing item separately from the oldest still-running job;
- the console ends with a compact grouped failure digest; full traces remain in the
  report and are opt-in on the console with --verbose-failures.

Historical version-locked tests remain design archaeology. The release gate includes
foundational contracts, the 6.4 durability boundary, all current 7.x/8.x/9.x core and
browser contracts, and the durable browser workflows in tests/test_e2e.py.

9.6.8 retains verifier throughput by batching ordinary core tests at module granularity
while preserving exact-item isolation for reliability stress tests and running the scale contract without competing core workers. Pytest's own
tiered per-item watchdog (3s Unit / 10s Core/API / 15s browser-E2E) remains authoritative inside every module. JUnit plus incremental JSONL output are parsed for every process so aggregate jobs still report each failing child test, its failure phase, and a useful expected/actual or exception signature.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
UNIT_TESTS = TESTS / "unit"
SRC = ROOT / "src"
REPORT = ROOT / "verification-report.txt"

FOUNDATIONAL = [
    "test_manuscript.py",
    "test_canvas_maps.py",
    "test_maps.py",
    "test_projects.py",
    "test_reliability.py",
    "test_docx.py",
    "test_scale.py",
    "test_product_contract.py",
]
BOUNDARY_CORE = ["test_v640.py", "test_v641.py", "test_v642.py"]
BOUNDARY_BROWSER = ["test_v640_browser.py", "test_v641_browser.py", "test_v642_browser.py"]
DURABLE_E2E = "test_e2e.py"

UNIT_JOB_TIMEOUT = 90
CORE_JOB_TIMEOUT = 35
BROWSER_JOB_TIMEOUT = 45
HEARTBEAT_SECONDS = 10
ISOLATED_CORE_MODULES = {"test_reliability.py", "test_scale.py"}
# A fast security-relevant smoke subset. The FULL gate still runs every current
# core/browser contract; quick mode must not regress to pre-M12-only coverage.
HISTORICAL_TEST_MODULES_PATH = ROOT / "scripts/historical_test_modules.txt"
SECURITY_QUICK_CORE_MODULES = {
    "test_v1056_security.py",
    "test_v1057_privacy.py",
    "test_v1059_encrypted_objects.py",
    "test_v10513_private_firestore.py",
    "test_v10517_encrypted_recovery.py",
    "test_v10518_sealed_recovery_source.py",
    "test_v10519_verification_quality.py",
}
# These modules are individually within the unchanged 10-second per-item budget but
# are intentionally CPU/IO intensive (tree-wide source scans, randomized soak, or
# durability-heavy project/review persistence). Running them beside the ordinary
# parallel core pool measures scheduler contention instead of Breadmaker behavior.
# Keep them in a one-worker lane; do not raise their per-item timeout.
PREWARM_APP_CORE_MODULES = {
    # Review-checkpoint durability is API-heavy enough that its 10-second item
    # budget should measure the contract itself, not one-time FastAPI/workspace
    # import cost. The app is loaded once at pytest session start for this module.
    "test_v8173.py",
    # These legacy module contracts also begin with source-only tests before the
    # first API fixture. Under cold-cache load, that first TestClient import can
    # consume the otherwise unchanged 10-second per-item setup budget. Use the
    # existing session-level app warmup, outside every individual test's budget.
    "test_v10326.py",
    "test_v700.py",
}

NO_CONTENTION_CORE_MODULES = {
    # Import/bootstrap-heavy historical API contracts. Their individual behavior is
    # fast enough for the unchanged 10-second item budget, but parallel cold imports
    # (cryptography/python-docx/etc.) turn the watchdog into a scheduler benchmark.
    "test_v700.py",
    "test_v703.py",
    "test_v705.py",
    "test_v720.py",
    # Tree-wide source scans are IO-heavy and should measure source contracts, not
    # parallel filesystem pressure from the ordinary Core/API pool.
    "test_v1024.py",
    "test_v1030.py",
    # Existing persistence/source-heavy no-contention contracts.
    "test_v773.py",
    "test_v778.py",
    "test_v8173.py",
    "test_v1010.py",
    "test_v10213.py",
    "test_v10214.py",
    "test_v10215.py",
    "test_v10326.py",
}


@dataclass(frozen=True)
class Job:
    name: str
    category: str
    target: str
    timeout: int
    expected_items: int = 1


@dataclass
class Result:
    job: Job
    returncode: int
    output: str
    duration: float
    timed_out: bool = False
    executed_items: int = 0
    passed_items: int = 0
    failed_items: int = 0
    skipped_items: int = 0
    child_failures: list[tuple[str, str, str]] = field(default_factory=list)
    retry_attempted: bool = False
    retry_recovered: bool = False
    retry_duration: float = 0.0
    initial_output: str = ""
    initial_timed_out: bool = False
    initial_child_failures: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and self.failed_items == 0


def _test_functions(path: Path, prefix: str = "test_") -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(prefix)
    ]


def _unit_paths() -> list[Path]:
    paths = sorted(UNIT_TESTS.glob("test_*.py"))
    if not paths:
        raise AssertionError("No unit-test modules found under tests/unit")
    return [path.resolve() for path in paths]


def discover_unit_jobs() -> list[Job]:
    jobs: list[Job] = []
    for path in _unit_paths():
        names = _test_functions(path)
        if not names:
            raise AssertionError(f"No pytest functions discovered in unit module: {path.name}")
        jobs.append(Job(
            name=f"{path.name} ({len(names)} source tests)",
            category="unit",
            target=str(path),
            timeout=UNIT_JOB_TIMEOUT,
            expected_items=len(names),
        ))
    return jobs


def collect_exact_unit_job() -> Job:
    """Collect exact unit items (including parametrized cases) and run them in one fast process."""
    command = [
        sys.executable, "-m", "pytest", "--collect-only", "-q",
        "-p", "no:cacheprovider", str(UNIT_TESTS),
    ]
    try:
        completed = subprocess.run(
            command, cwd=ROOT, env=_pytest_env(), text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise AssertionError("Unit pytest collection exceeded 60 seconds") from exc
    if completed.returncode not in (0, 5):
        raise AssertionError("Unit pytest collection failed:\n" + (completed.stdout or ""))
    nodeids = []
    for raw in (completed.stdout or "").splitlines():
        line = raw.strip()
        if "::" in line and line.split("::", 1)[0].endswith(".py"):
            nodeids.append(line)
    if not nodeids:
        raise AssertionError("Unit pytest collection returned no test items")
    return Job(
        name=f"unit suite ({len(nodeids)} tests)",
        category="unit",
        target=str(UNIT_TESTS),
        timeout=max(UNIT_JOB_TIMEOUT, len(nodeids) * 4 + 30),
        expected_items=len(nodeids),
    )


def _current_core_paths() -> list[Path]:
    paths = [TESTS / name for name in FOUNDATIONAL + BOUNDARY_CORE]
    current = sorted([*TESTS.glob("test_v7*.py"), *TESTS.glob("test_v8*.py"), *TESTS.glob("test_v9*.py"), *TESTS.glob("test_v10*.py")])
    paths.extend(path for path in current if not path.name.endswith("_browser.py"))
    # Preserve order while protecting against accidental duplicate classification.
    return list(dict.fromkeys(path.resolve() for path in paths))


def _current_browser_paths() -> list[Path]:
    paths = [TESTS / name for name in BOUNDARY_BROWSER]
    paths.extend(sorted([*TESTS.glob("test_v7*_browser.py"), *TESTS.glob("test_v8*_browser.py"), *TESTS.glob("test_v9*_browser.py"), *TESTS.glob("test_v10*_browser.py")]))
    return list(dict.fromkeys(path.resolve() for path in paths))


def discover_core_jobs(*, quick: bool = False) -> list[Job]:
    paths = _current_core_paths()
    if quick:
        keep = {"test_product_contract.py", "test_v8180.py", "test_v8181.py", "test_v8184.py", "test_v8185.py", "test_v8186.py", "test_v8187.py", "test_v8188.py", "test_v8189.py", "test_v81810.py", "test_v81811.py", "test_v963.py", "test_v965.py", "test_v966.py", "test_v967.py", "test_v968.py", "test_v969.py", "test_v9610.py", "test_v9611.py", "test_v9612.py", "test_v9613.py", "test_v9614.py", "test_v1000.py", "test_v1010.py", "test_v1011.py", "test_v1012.py","test_v1020.py","test_v1021.py","test_v1022.py","test_v1023.py","test_v1024.py","test_v1025.py","test_v1026.py","test_v1027.py","test_v1028.py","test_v1029.py","test_v10210.py","test_v10211.py","test_v10212.py","test_v10213.py","test_v10214.py","test_v10215.py","test_v10217.py","test_v10218.py","test_v10219.py","test_v10221.py","test_v10222.py","test_v10223.py","test_v10224.py","test_v10225.py","test_v10226.py","test_v10227.py","test_v1030.py","test_v1031.py","test_v1032.py","test_v1033.py","test_v1034.py",}
        paths = [path for path in paths if path.name in keep or path.name in SECURITY_QUICK_CORE_MODULES]
    jobs: list[Job] = []
    for path in paths:
        names = _test_functions(path)
        if not names:
            raise AssertionError(f"No pytest functions discovered in required module: {path.name}")
        for name in names:
            jobs.append(
                Job(
                    name=f"{path.name}::{name}",
                    category="core/API",
                    target=f"{path}::{name}",
                    timeout=CORE_JOB_TIMEOUT,
                )
            )
    return jobs


def _expected_core_nodeids() -> set[str]:
    expected: set[str] = set()
    for path in _current_core_paths():
        expected.update(f"{path}::{name}" for name in _test_functions(path))
    return expected


def collect_exact_core_jobs(*, quick: bool = False) -> list[Job]:
    """Ask pytest for exact item node IDs so parametrized cases are isolated too."""
    paths = _current_core_paths()
    if quick:
        keep = {"test_product_contract.py", "test_v8180.py", "test_v8181.py", "test_v8184.py", "test_v8185.py", "test_v8186.py", "test_v8187.py", "test_v8188.py", "test_v8189.py", "test_v81810.py", "test_v81811.py", "test_v963.py", "test_v965.py", "test_v966.py", "test_v967.py", "test_v968.py", "test_v969.py", "test_v9610.py", "test_v9611.py", "test_v9612.py", "test_v9613.py", "test_v9614.py", "test_v1000.py", "test_v1010.py", "test_v1011.py", "test_v1012.py","test_v1020.py","test_v1021.py","test_v1022.py","test_v1023.py","test_v1024.py","test_v1025.py","test_v1026.py","test_v1027.py","test_v1028.py","test_v1029.py","test_v10210.py","test_v10211.py","test_v10212.py","test_v10213.py","test_v10214.py","test_v10215.py","test_v10217.py","test_v10218.py","test_v10219.py","test_v10221.py","test_v10222.py","test_v10223.py","test_v10224.py","test_v10225.py","test_v10226.py","test_v10227.py","test_v1030.py","test_v1031.py","test_v1032.py","test_v1033.py","test_v1034.py",}
        paths = [path for path in paths if path.name in keep]
    command = [
        sys.executable, "-m", "pytest", "--collect-only", "-q",
        "-p", "no:cacheprovider", *(str(path) for path in paths),
    ]
    try:
        completed = subprocess.run(
            command, cwd=ROOT, env=_pytest_env(), text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120,
        )
    except subprocess.TimeoutExpired as exc:
        raise AssertionError("Exact core pytest collection exceeded 120 seconds") from exc
    if completed.returncode not in (0, 5):
        raise AssertionError("Exact core pytest collection failed:\n" + (completed.stdout or ""))
    nodeids: list[str] = []
    for raw in (completed.stdout or "").splitlines():
        line = raw.strip()
        if "::" not in line:
            continue
        first = line.split("::", 1)[0]
        if first.endswith(".py"):
            nodeids.append(line)
    if not nodeids:
        raise AssertionError("Exact core pytest collection returned no test items")
    return [
        Job(name=nodeid, category="core/API", target=nodeid, timeout=CORE_JOB_TIMEOUT)
        for nodeid in nodeids
    ]


def plan_core_execution_jobs(exact_jobs: list[Job]) -> list[Job]:
    """Batch ordinary core tests by module while keeping stress tests item-isolated.

    Pytest still executes every collected test item, and tests/conftest.py still applies
    the hard per-item watchdog across setup/call/teardown (10s core/API, 15s browser/E2E). Batching removes
    hundreds of repeated Python/FastAPI imports without weakening the highest-risk
    reliability/scale isolation boundary.
    """
    grouped: dict[str, list[Job]] = {}
    for job in exact_jobs:
        module = job.target.split("::", 1)[0]
        grouped.setdefault(module, []).append(job)
    planned: list[Job] = []
    for module, items in grouped.items():
        path = Path(module)
        if path.name in ISOLATED_CORE_MODULES:
            planned.extend(items)
            continue
        count = len(items)
        planned.append(Job(
            name=f"{path.name} ({count} test{'s' if count != 1 else ''})",
            category="core/API module",
            target=module,
            timeout=max(60, count * 12 + 30),
            expected_items=count,
        ))
    return planned


def _durable_e2e_job() -> Job:
    e2e = (TESTS / DURABLE_E2E).resolve()
    tests = _test_functions(e2e)
    if not tests:
        raise AssertionError("Durable E2E module contains no pytest workflows")
    # Each durable browser item has the hard 15-second watchdog from conftest.py. This
    # outer bound only catches a poisoned pytest/Chromium process or fixture teardown.
    timeout = max(600, len(tests) * 17 + 120)
    return Job(
        name=f"{DURABLE_E2E} sequential workflows ({len(tests)} tests)",
        category="durable E2E",
        target=str(e2e),
        timeout=timeout,
        expected_items=len(tests),
    )


def discover_browser_jobs(*, quick: bool = False) -> list[Job]:
    jobs: list[Job] = []
    # tests/test_e2e.py intentionally owns one module-scoped live server + Chromium
    # process and several historical workflows build on that sequential lifecycle.
    # 8.18.4 incorrectly exploded the module into independent subprocesses, which
    # both slowed verification dramatically and manufactured false regressions.
    if not quick:
        jobs.append(_durable_e2e_job())
    paths = _current_browser_paths()
    if quick:
        paths = [path for path in paths if path.name in {"test_v8180_browser.py", "test_v8184_browser.py", "test_v8185_browser.py", "test_v8187_browser.py"}]
    for path in paths:
        for name in _test_functions(path):
            jobs.append(
                Job(
                    name=f"{path.name}::{name}",
                    category="current browser",
                    target=f"{path}::{name}",
                    timeout=BROWSER_JOB_TIMEOUT,
                )
            )
    return jobs


def _expected_browser_nodeids() -> set[str]:
    expected: set[str] = set()
    for path in _current_browser_paths():
        expected.update(f"{path.resolve()}::{name}" for name in _test_functions(path))
    # Durable E2E is deliberately one sequential module target. Current browser
    # contracts provide per-control isolation; the durable module protects the
    # long-lived historical workflow exactly as authored.
    expected.add(str((TESTS / DURABLE_E2E).resolve()))
    return expected


def _historical_test_modules() -> frozenset[str]:
    """Read the reviewed, *exact* exclusions from the frozen v4-v6 test era.

    Avoid a prefix/version-number regex here: `test_v110.py` might be a new
    v11 test, and must be reported as untracked rather than silently ignored.
    """
    entries = [
        line.strip()
        for line in HISTORICAL_TEST_MODULES_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if len(entries) != len(set(entries)) or any(
        not re.fullmatch(r"test_v[4-6][0-9]*(?:_browser)?\.py|test_v500_migration\.py", name)
        for name in entries
    ):
        raise AssertionError("Invalid or duplicated historical test exclusion")
    return frozenset(entries)


def _untracked_test_modules() -> list[str]:
    """Reject new invisible tests; only explicitly inventoried legacy files are exempt."""
    discovered = {
        *(path.name for path in _unit_paths()),
        *(path.name for path in _current_core_paths()),
        *(path.name for path in _current_browser_paths()),
        DURABLE_E2E,
    }
    historical = _historical_test_modules()
    return sorted(
        path.name for path in TESTS.glob("test_*.py")
        if path.name not in discovered and path.name not in historical
    )


def _assert_manifest_complete() -> None:
    untracked = _untracked_test_modules()
    if untracked:
        raise AssertionError(
            "Untracked test modules; explicitly include them in the current "
            "verifier (or deliberately classify as historical): " + ", ".join(untracked[:15])
        )
    unit_paths = _unit_paths()
    for path in unit_paths:
        if not _test_functions(path):
            raise AssertionError(f"Unit test module has no test functions: {path.name}")

    missing = [path.name for path in _current_core_paths() if not path.exists()]
    if missing:
        raise AssertionError(f"Missing required core test modules: {', '.join(missing)}")

    core_targets = {job.target for job in discover_core_jobs()}
    expected_core = _expected_core_nodeids()
    if core_targets != expected_core:
        omitted = sorted(expected_core - core_targets)
        extra = sorted(core_targets - expected_core)
        raise AssertionError(f"Core verification manifest mismatch; omitted={omitted[:8]}, extra={extra[:8]}")

    browser_targets = {job.target for job in discover_browser_jobs()}
    expected_browser = _expected_browser_nodeids()
    if browser_targets != expected_browser:
        omitted = sorted(expected_browser - browser_targets)
        extra = sorted(browser_targets - expected_browser)
        raise AssertionError(
            f"Browser verification manifest mismatch; omitted={omitted[:8]}, extra={extra[:8]}"
        )


def _python_syntax_check() -> None:
    roots = [ROOT / "src", ROOT / "scripts", ROOT / "tests"]
    failures: list[str] = []
    count = 0
    for base in roots:
        for path in sorted(base.rglob("*.py")):
            if any(part in {".venv", "__pycache__"} for part in path.parts):
                continue
            count += 1
            try:
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except Exception as exc:  # SyntaxError plus unreadable source diagnostics.
                failures.append(f"{path.relative_to(ROOT)}: {exc}")
    if failures:
        raise AssertionError("Python source parse failures:\n" + "\n".join(failures))
    print(f"  ✓ Python source parse ({count} files)", flush=True)


def _command_check(label: str, command: list[str]) -> None:
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    if completed.returncode:
        details = (completed.stdout or "") + (completed.stderr or "")
        raise AssertionError(f"{label} failed:\n{details.strip()}")
    print(f"  ✓ {label}", flush=True)


def _javascript_syntax_check(node: str) -> None:
    """Validate every shipped JS module, then print one compact preflight line.

    A single static/app.js parse is insufficient: it imports most of the actual
    UI from feature modules.  Run each syntax check outside pytest and report
    the exact file rather than burying it in an E2E startup exception.
    """
    scripts = sorted((ROOT / "static").rglob("*.js"))
    if not scripts:
        raise AssertionError("No browser JavaScript modules found for syntax preflight")
    for script in scripts:
        relative = script.relative_to(ROOT).as_posix()
        completed = subprocess.run(
            [node, "--check", relative], cwd=ROOT, text=True,
            capture_output=True, timeout=15,
        )
        if completed.returncode:
            detail = ((completed.stderr or "") + (completed.stdout or "")).strip()
            raise AssertionError(f"JavaScript syntax failed: {relative}\n{detail}")
    print(f"  ✓ JavaScript syntax coverage ({len(scripts)} modules)", flush=True)


def _locale_check() -> None:
    locale_dir = ROOT / "locales"
    files = sorted(locale_dir.glob("*.json"))
    if not files:
        raise AssertionError("No locale catalogs found")
    baseline: set[str] | None = None
    baseline_name = ""
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        strings = data.get("strings")
        if not isinstance(strings, dict):
            raise AssertionError(f"{path.name}: missing strings object")
        keys = set(strings)
        if baseline is None:
            baseline, baseline_name = keys, path.name
        elif keys != baseline:
            missing = sorted(baseline - keys)[:12]
            extra = sorted(keys - baseline)[:12]
            raise AssertionError(
                f"Locale key mismatch {path.name} vs {baseline_name}; missing={missing}, extra={extra}"
            )
    print(f"  ✓ Locale catalog parity ({len(files)} catalogs / {len(baseline or ())} keys)", flush=True)


def _assert_release_identity() -> None:
    """Single fast release-identity gate; never launch pytest or rewrite files."""
    try:
        # CLI runs as ``python scripts/verify_release.py``; imported unit tests
        # use ``from scripts import verify_release``. Support both entrypoints.
        from scripts.sync_editor_release_metadata import validate
    except ModuleNotFoundError:
        from sync_editor_release_metadata import validate
    try:
        version = validate(ROOT)
    except (ValueError, OSError) as exc:
        raise AssertionError(str(exc)) from exc
    print(f"  ✓ Editor release identity and nested asset cache keys ({version})", flush=True)


def _browser_runtime_check() -> None:
    # Run a real launch/page/close in an isolated process. A wedged driver or its
    # cleanup must fail before test discovery/core work, not hang the verifier.
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts/browser_runtime.py")], cwd=ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        start_new_session=(os.name == "posix"),
    )
    try:
        try:
            output, _ = proc.communicate(timeout=45)
        except subprocess.TimeoutExpired as exc:
            _kill_process_tree(proc)
            raise AssertionError("BROWSER_PREFLIGHT_TIMEOUT: launch/page/cleanup exceeded 45 seconds") from exc
    finally:
        _cleanup_abandoned_process_group(proc)
    print(output.rstrip(), flush=True)
    if proc.returncode != 0:
        raise AssertionError(f"BROWSER_PREFLIGHT_FAILED: child exit {proc.returncode}; see browser phase above")
    prefix = "BREADMAKER_BROWSER_EXECUTABLE::"
    executable = next((line[len(prefix):] for line in output.splitlines() if line.startswith(prefix)), "")
    if not executable or not Path(executable).is_file():
        raise AssertionError("BROWSER_PREFLIGHT_FAILED: no verified Chromium executable")
    # Every subsequent browser job must use the exact binary that passed probe.
    os.environ["BREADMAKER_TEST_BROWSER"] = executable
    print(f"  ✓ Browser launch, page and cleanup ({Path(executable).name})", flush=True)


def _assert_protected_integrity() -> None:
    """Single exact-hash gate: one failure, before expensive test collection."""
    try:
        from scripts.protected_file_integrity import validate
    except ModuleNotFoundError:
        from protected_file_integrity import validate
    protected = validate(ROOT)
    print(f"  ✓ Protected source integrity ({len(protected)} reviewed files)", flush=True)


def run_preflight() -> None:
    print("Preflight", flush=True)
    _assert_manifest_complete()
    _assert_release_identity()
    _assert_protected_integrity()
    _browser_runtime_check()
    print("  ✓ Verification manifest covers all configured current tests", flush=True)
    _python_syntax_check()
    node = shutil.which("node")
    if not node:
        raise AssertionError("Node.js is required for JavaScript syntax verification")
    _javascript_syntax_check(node)
    bash = shutil.which("bash")
    if not bash:
        raise AssertionError("bash is required for launcher syntax verification")
    for name in ("run", "reset", "test", "verify-release", "Breadmaker Editor.command"):
        _command_check(f"Shell syntax: {name}", [bash, "-n", name])
    _locale_check()


def _pytest_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(SRC) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def _kill_process_tree(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    else:
        proc.terminate()
    try:
        proc.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass
    if os.name == "posix":
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
    else:
        proc.kill()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def _failure_phase_map(output: str) -> dict[str, str]:
    phases: dict[str, str] = {}
    prefix = "BREADMAKER_FAILURE_PHASE::"
    for raw in (output or "").splitlines():
        line = raw.strip()
        if not line.startswith(prefix):
            continue
        payload = line[len(prefix):]
        if "::" not in payload:
            continue
        nodeid, phase = payload.rsplit("::", 1)
        phases[nodeid] = phase
    return phases


def _junit_nodeid(case: ET.Element) -> str:
    name = str(case.attrib.get("name") or "unknown")
    file_attr = str(case.attrib.get("file") or "")
    classname = str(case.attrib.get("classname") or "")
    if file_attr:
        path = file_attr
    elif classname:
        dotted = classname.replace(".", "/")
        path = dotted if dotted.endswith(".py") else dotted + ".py"
    else:
        path = "pytest"
    return f"{path}::{name}"


def _failure_reason_from_element(element: ET.Element) -> str:
    message = " ".join(str(element.attrib.get("message") or "").split())
    body = str(element.text or "")
    # Pytest can reduce `assert response.ok, response.text` to the unhelpful
    # `assert False` in the JUnit message while the traceback still contains the
    # response body. Prefer that concrete AssertionError payload when available.
    if message and (message.endswith("-> assert False") or message in {"assert False", "AssertionError: assert False"}):
        for raw in body.splitlines():
            line=raw.strip()
            if line.startswith("E   AssertionError:"):
                detail=line[len("E   AssertionError:"):].strip()
                if detail and detail not in {"assert False", "False"}:
                    return f"{message} | {detail}"[:500]
    if message:
        return message[:500]
    return _concise_reason(body)[:500]


def _parse_junit(path: Path, output: str) -> tuple[int, int, int, int, list[tuple[str, str, str]]]:
    if not path.exists() or not path.stat().st_size:
        return 0, 0, 0, 0, []
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return 0, 0, 0, 0, []
    phases = _failure_phase_map(output)
    executed = passed = failed = skipped = 0
    failures: list[tuple[str, str, str]] = []
    for case in root.iter("testcase"):
        executed += 1
        nodeid = _junit_nodeid(case)
        failure = case.find("failure")
        error = case.find("error")
        skipped_node = case.find("skipped")
        if failure is not None or error is not None:
            failed += 1
            element = failure if failure is not None else error
            phase = phases.get(nodeid, "")
            # Pytest's JUnit classname may omit a leading tests/. Fall back to matching
            # by the function/parameter suffix emitted by our phase hook.
            if not phase:
                suffix = "::" + nodeid.split("::", 1)[-1]
                for candidate, candidate_phase in phases.items():
                    if candidate.endswith(suffix):
                        phase = candidate_phase
                        break
            failures.append((nodeid, _failure_reason_from_element(element), phase))
        elif skipped_node is not None:
            skipped += 1
        else:
            passed += 1
    return executed, passed, failed, skipped, failures




def _parse_result_jsonl(path: Path) -> tuple[int, int, int, int, list[tuple[str, str, str]]]:
    """Parse per-phase records written directly by tests/conftest.py.

    Unlike JUnit, this file is appended as each report is emitted, so a later hard
    timeout or aggregate-module failure cannot erase already-known child results.
    """
    if not path.exists() or not path.stat().st_size:
        return 0,0,0,0,[]
    records: dict[str,list[dict]]={}
    try:
        for raw in path.read_text(encoding="utf-8",errors="replace").splitlines():
            if not raw.strip(): continue
            try: row=json.loads(raw)
            except Exception: continue
            nodeid=str(row.get("nodeid") or "")
            if nodeid: records.setdefault(nodeid,[]).append(row)
    except Exception:
        return 0,0,0,0,[]
    passed=failed=skipped=0; failures=[]
    for nodeid,rows in records.items():
        bad=next((row for row in rows if row.get("outcome")=="failed"),None)
        if bad is not None:
            failed+=1
            failures.append((nodeid,str(bad.get("reason") or "pytest failure")[:1200],str(bad.get("phase") or "")))
            continue
        if any(row.get("outcome")=="skipped" for row in rows):
            skipped+=1
        elif (any(row.get("phase")=="call" and row.get("outcome")=="passed" for row in rows)
              and any(row.get("phase")=="teardown" and row.get("outcome")=="passed" for row in rows)):
            passed+=1
        else:
            # Preserve exactly how far pytest got before the process/fixture stopped.
            # This makes a partial aggregate run diagnostic without needing traceback walls.
            phase_order=("setup","call","teardown")
            seen={str(row.get("phase") or ""):str(row.get("outcome") or "") for row in rows}
            recorded=", ".join(f"{phase}={seen[phase]}" for phase in phase_order if phase in seen) or "no phase outcomes"
            missing=" + ".join(phase for phase in phase_order if phase not in seen) or "completion"
            failed+=1
            failures.append((nodeid,f"incomplete pytest item; recorded {recorded}; missing {missing} report(s)","accounting"))
    return len(records),passed,failed,skipped,failures


def _result_jsonl_nodeids(path: Path) -> set[str]:
    nodeids: set[str] = set()
    if not path.exists() or not path.stat().st_size:
        return nodeids
    try:
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except Exception:
                continue
            nodeid = str(row.get("nodeid") or "")
            if nodeid:
                nodeids.add(nodeid)
    except Exception:
        return set()
    return nodeids


def _durable_recorded_names(path: Path) -> set[str]:
    """Return durable item names whose body has a definitive outcome.

    A setup-only JSONL row is deliberately *not* enough. If pytest or Chromium dies
    between setup and call, the recovery pass must retry that item instead of silently
    treating it as executed. A call outcome, or a setup failure/skip that prevents the
    body from running, is definitive.
    """
    rows_by_nodeid: dict[str, list[dict]] = {}
    if not path.exists() or not path.stat().st_size:
        return set()
    try:
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except Exception:
                continue
            nodeid = str(row.get("nodeid") or "")
            if nodeid:
                rows_by_nodeid.setdefault(nodeid, []).append(row)
    except Exception:
        return set()
    names: set[str] = set()
    for nodeid, rows in rows_by_nodeid.items():
        call_done = any(str(row.get("phase") or "") == "call" for row in rows)
        setup_terminal = any(
            str(row.get("phase") or "") == "setup"
            and str(row.get("outcome") or "") in {"failed", "skipped"}
            for row in rows
        )
        if (call_done or setup_terminal) and "::" in nodeid:
            names.add(nodeid.split("::", 1)[1].split("[", 1)[0])
    return names


def _cleanup_abandoned_process_group(proc: subprocess.Popen[str]) -> None:
    """Kill descendants left behind when pytest hard-exits from a browser item."""
    if os.name != "posix":
        if proc.poll() is None:
            _kill_process_tree(proc)
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        return
    time.sleep(0.15)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass


def _await_durable_startup(proc, ready_path: Path, timeout: float) -> bool:
    """Independent parent deadline, even if the child's Python/GIL is wedged."""
    deadline = time.monotonic() + timeout
    while not ready_path.is_file():
        if proc.poll() is not None:
            return True  # communicate() will preserve the actual failure output.
        if time.monotonic() >= deadline:
            return False
        time.sleep(.05)
    return True


def _run_durable_job(job: Job, started: float, junit_path: Path, result_path: Path) -> Result:
    """Run durable E2E and resume only unrecorded items after a poisoned hard exit.

    The fast path remains one shared pytest/server/Chromium lifecycle. If one item
    reaches the last-resort hard exit, its JSONL failure record marks it complete.
    The verifier then starts a fresh pytest process for only the still-unrecorded
    tail, using the same on-disk E2E workspace so sequential durable state survives.
    """
    durable_path = (TESTS / DURABLE_E2E).resolve()
    names = _test_functions(durable_path)
    shared_home = Path(tempfile.mkdtemp(prefix="breadmaker-durable-resume-"))
    aggregate_output: list[str] = []
    outer_deadline = started + job.timeout
    attempt = 0
    previous_recorded = 0
    outer_timed_out = False
    try:
        while True:
            recorded_names = _durable_recorded_names(result_path)
            missing = [name for name in names if name not in recorded_names]
            if not missing:
                break
            if time.monotonic() >= outer_deadline:
                outer_timed_out = True
                aggregate_output.append(
                    "BREADMAKER_DURABLE_RESUME_TIMEOUT: outer durable watchdog expired before all items were recorded.\n"
                )
                break

            attempt += 1
            targets = [str(durable_path)] if attempt == 1 else [f"{durable_path}::{name}" for name in missing]
            attempt_junit = junit_path.with_name(junit_path.stem + f"-attempt-{attempt}" + junit_path.suffix)
            command = [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--tb=short",
                "--junitxml",
                str(attempt_junit),
                "-p",
                "no:cacheprovider",
                *targets,
            ]
            env = _pytest_env()
            env["BREADMAKER_VERIFY_RESULT_JSONL"] = str(result_path)
            env["BREADMAKER_DURABLE_E2E_HOME"] = str(shared_home)
            env["BREADMAKER_DURABLE_VERIFY"] = "1"
            ready_path = shared_home / f"browser-ready-{attempt}"
            env["BREADMAKER_DURABLE_BROWSER_READY_FILE"] = str(ready_path)
            proc = subprocess.Popen(
                command,
                cwd=ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=(os.name == "posix"),
            )
            remaining_outer = max(1.0, outer_deadline - time.monotonic())
            try:
                if not _await_durable_startup(proc, ready_path, min(45.0, remaining_outer)):
                    raise subprocess.TimeoutExpired("durable browser startup", 45)
                remaining_outer = max(1.0, outer_deadline - time.monotonic())
                output, _ = proc.communicate(timeout=remaining_outer)
                output = output or ""
                code = int(proc.returncode or 0)
            except subprocess.TimeoutExpired:
                outer_timed_out = True
                _kill_process_tree(proc)
                try:
                    output, _ = proc.communicate(timeout=1)
                except Exception:
                    output = ""
                code = 124
                if not ready_path.is_file():
                    output = "BREADMAKER_BROWSER_HARD_TIMEOUT: startup-parent-watchdog\n" + (output or "")
            finally:
                _cleanup_abandoned_process_group(proc)
                try:
                    attempt_junit.unlink(missing_ok=True)
                except Exception:
                    pass

            requested_count = len(names) if attempt == 1 else len(targets)
            aggregate_output.append(
                f"\n--- durable attempt {attempt}: requested {requested_count} item(s), exit {code} ---\n{output}"
            )
            now_recorded = len(_durable_recorded_names(result_path))
            if outer_timed_out:
                break
            if now_recorded <= previous_recorded:
                aggregate_output.append(
                    "BREADMAKER_DURABLE_RESUME_STALLED: recovery attempt recorded no additional pytest item.\n"
                )
                break
            previous_recorded = now_recorded

        output = "".join(aggregate_output)
        executed, passed, failed, skipped, child_failures = _parse_result_jsonl(result_path)
        startup_timed_out = False
        if executed == 0 and attempt and code != 0:
            phases = [line for line in output.splitlines() if line.startswith('BREADMAKER_BROWSER_')]
            startup_timed_out = code == 124
            child_failures.append((job.name,
                f"Browser/session startup failed before any durable test (exit {code}); "
                + ('; '.join(phases[-3:]) if phases else 'see subprocess traceback in report'),
                'startup'))
        missing_names = [name for name in names if name not in _durable_recorded_names(result_path)]
        if missing_names:
            output += (
                f"\nBREADMAKER_ITEM_ACCOUNTING_MISMATCH: expected {len(names)} pytest item(s), "
                f"recorded {executed}; unrecorded tail={len(missing_names)}.\n"
            )
            child_failures.append((
                job.name,
                f"expected {len(names)} pytest item(s), recorded {executed}; unrecorded: "
                + ", ".join(missing_names[:8])
                + (" …" if len(missing_names) > 8 else ""),
                "accounting",
            ))
        code = 0 if not outer_timed_out and not missing_names and failed == 0 else (124 if outer_timed_out or startup_timed_out else 1)
        if executed and passed == 0 and failed == 0 and skipped == executed:
            code = 5
            output += "\nBREADMAKER_BROWSER_NOT_VERIFIED: durable browser tests were skipped.\n"
        return Result(
            job,
            code,
            output,
            time.monotonic() - started,
            timed_out=outer_timed_out or startup_timed_out,
            executed_items=executed,
            passed_items=passed,
            failed_items=failed,
            skipped_items=skipped,
            child_failures=child_failures,
        )
    finally:
        shutil.rmtree(shared_home, ignore_errors=True)


def _run_job(job: Job, running: dict[str, float], lock: threading.Lock) -> Result:
    started = time.monotonic()
    with lock:
        running[job.name] = started
    handle = tempfile.NamedTemporaryFile(prefix="breadmaker-verify-", suffix=".xml", delete=False)
    junit_path = Path(handle.name)
    handle.close()
    result_handle = tempfile.NamedTemporaryFile(prefix="breadmaker-verify-", suffix=".jsonl", delete=False)
    result_path = Path(result_handle.name)
    result_handle.close()
    if job.category == "durable E2E":
        try:
            return _run_durable_job(job, started, junit_path, result_path)
        finally:
            for artifact in (junit_path, result_path):
                try:
                    artifact.unlink(missing_ok=True)
                except Exception:
                    pass
            with lock:
                running.pop(job.name, None)
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--tb=short",
        "--junitxml",
        str(junit_path),
        "-p",
        "no:cacheprovider",
        job.target,
    ]
    proc: subprocess.Popen[str] | None = None
    try:
        env=_pytest_env()
        module_name=Path(job.target.split("::",1)[0]).name
        if module_name in PREWARM_APP_CORE_MODULES:
            env["BREADMAKER_VERIFY_PRELOAD_APP"]="1"
        env["BREADMAKER_VERIFY_RESULT_JSONL"]=str(result_path)
        proc = subprocess.Popen(
            command,
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=(os.name == "posix"),
        )
        try:
            output, _ = proc.communicate(timeout=job.timeout)
            output = output or ""
            code = proc.returncode
            executed, passed, failed, skipped, child_failures = _parse_result_jsonl(result_path)
            if executed == 0:
                executed, passed, failed, skipped, child_failures = _parse_junit(junit_path, output)
            if job.expected_items and executed != job.expected_items:
                output += (f"\nBREADMAKER_ITEM_ACCOUNTING_MISMATCH: expected {job.expected_items} "
                           f"pytest item(s), recorded {executed}.\n")
                if code == 0:
                    code = 6
                if not child_failures:
                    child_failures.append((job.name, f"expected {job.expected_items} pytest item(s), recorded {executed}", "accounting"))
            # A browser job that skipped did not verify the UI. Core suites may
            # contain intentionally skipped platform contracts, but each browser
            # job must execute at least one real browser item.
            if job.category in {"current browser", "durable E2E"} and code == 0:
                if executed and passed == 0 and failed == 0 and skipped == executed:
                    code = 5
                    output += "\nBREADMAKER_BROWSER_NOT_VERIFIED: browser test was skipped.\n"
            return Result(
                job, code, output, time.monotonic() - started,
                executed_items=executed, passed_items=passed, failed_items=failed,
                skipped_items=skipped, child_failures=child_failures,
            )
        except subprocess.TimeoutExpired:
            _kill_process_tree(proc)
            output = ""
            try:
                remaining, _ = proc.communicate(timeout=1)
                output = remaining or ""
            except Exception:
                pass
            executed, passed, failed, skipped, child_failures = _parse_result_jsonl(result_path)
            if executed == 0:
                executed, passed, failed, skipped, child_failures = _parse_junit(junit_path, output)
            return Result(
                job, 124, output, time.monotonic() - started, timed_out=True,
                executed_items=executed, passed_items=passed, failed_items=failed,
                skipped_items=skipped, child_failures=child_failures,
            )
    finally:
        if proc is not None and proc.poll() is None:
            _kill_process_tree(proc)
        for artifact in (junit_path,result_path):
            try:
                artifact.unlink(missing_ok=True)
            except Exception:
                pass
        with lock:
            running.pop(job.name, None)


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _short_name(name: str, limit: int = 58) -> str:
    if len(name) <= limit:
        return name
    return "…" + name[-(limit - 1) :]


def _progress_line(
    label: str,
    completed: int,
    total: int,
    passed: int,
    failed: int,
    running: dict[str, float],
    started: float,
    last_failed: str | None = None,
) -> str:
    ratio = 1.0 if total == 0 else completed / total
    width = 26
    filled = min(width, int(ratio * width))
    bar = "█" * filled + "░" * (width - filled)
    now = time.monotonic()
    oldest = ""
    if running:
        name, when = min(running.items(), key=lambda item: item[1])
        oldest = f" | oldest {_short_name(name, 36)} {int(now - when)}s"
    failure = f" | last fail {_short_name(last_failed, 52)}" if last_failed else ""
    return (
        f"{label:<16} [{bar}] {completed:>3}/{total:<3} {ratio * 100:5.1f}% "
        f"| ✓ {passed:<3} ✗ {failed:<2} | {len(running)} active "
        f"| {_format_duration(now - started)}{failure}{oldest}"
    )


def run_stage(label: str, jobs: list[Job], workers: int) -> list[Result]:
    if not jobs:
        print(f"{label}: no jobs", flush=True)
        return []
    workers = max(1, min(workers, len(jobs)))
    expected_items=sum(max(1,job.expected_items) for job in jobs)
    print(
        f"\n{label}: {len(jobs)} execution job{'s' if len(jobs) != 1 else ''} covering "
        f"{expected_items} pytest item{'s' if expected_items != 1 else ''}, "
        f"{workers} worker{'s' if workers != 1 else ''}",
        flush=True,
    )
    running: dict[str, float] = {}
    lock = threading.Lock()
    results: list[Result] = []
    stage_started = time.monotonic()
    tty = sys.stdout.isatty()
    last_heartbeat = stage_started
    last_failed: str | None = None

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="breadmaker-verify") as executor:
        pending = {executor.submit(_run_job, job, running, lock): job for job in jobs}
        single_job_log = (not tty and len(jobs) == 1)
        if single_job_log:
            # Captured/non-interactive stdout cannot repaint a terminal line. For a
            # single aggregate job (notably Durable E2E: 92 pytest items in one
            # shared browser process), repeated 10-second heartbeat bars are noise.
            # Emit one 0/1 bar now and one 1/1 bar when the job completes.
            with lock:
                initial_snapshot = dict(running)
            print(_progress_line(label, 0, 1, 0, 0, initial_snapshot, stage_started), flush=True)
        while pending:
            done, _ = wait(pending, timeout=0.35, return_when=FIRST_COMPLETED)
            for future in done:
                pending.pop(future, None)
                result = future.result()
                results.append(result)
                if not result.ok:
                    last_failed = result.child_failures[0][0] if result.child_failures else result.job.name
            passed = sum(result.ok for result in results)
            failed = len(results) - passed
            with lock:
                snapshot = dict(running)
            line = _progress_line(
                label, len(results), len(jobs), passed, failed, snapshot, stage_started, last_failed
            )
            if tty:
                sys.stdout.write("\r\x1b[2K" + line)
                sys.stdout.flush()
            else:
                now = time.monotonic()
                if done or (not single_job_log and now - last_heartbeat >= HEARTBEAT_SECONDS):
                    print(line, flush=True)
                    last_heartbeat = now

    if tty:
        sys.stdout.write("\r\x1b[2K")
    passed = sum(result.ok for result in results)
    failed = len(results) - passed
    print(
        f"{label}: {passed}/{len(results)} passed, {failed} failed in {_format_duration(time.monotonic() - stage_started)}",
        flush=True,
    )
    return results


def retry_failed_current_browser(results: list[Result], workers: int) -> tuple[int, int, int]:
    """Retry failed current-browser items once in fresh pytest processes.

    Unit/Core failures are deterministic release gates and are never retried. Durable
    E2E owns its own shared-lifecycle recovery semantics and is likewise excluded.
    A recovered browser retry becomes the logical final result, while the report keeps
    the initial failure for flake visibility.
    """
    failed = [result for result in results if result.job.category == "current browser" and not result.ok]
    if not failed:
        return 0, 0, 0
    print(
        f"\nBrowser retry: scheduling {len(failed)} failed current-browser item"
        f"{'s' if len(failed) != 1 else ''} for one fresh-process retry at the end.",
        flush=True,
    )
    retries = run_stage("Browser retry", [result.job for result in failed], max(1, workers))
    retry_by_target = {result.job.target: result for result in retries}
    recovered = 0
    confirmed = 0
    for original in failed:
        retry = retry_by_target[original.job.target]
        original.retry_attempted = True
        original.retry_duration = retry.duration
        original.initial_output = original.output
        original.initial_timed_out = original.timed_out
        original.initial_child_failures = list(original.child_failures)
        original.retry_recovered = retry.ok
        # The retry is the final logical status of this item. Preserve the first
        # attempt above so a recovered flake remains visible in the report.
        original.returncode = retry.returncode
        original.output = retry.output
        original.timed_out = retry.timed_out
        original.executed_items = retry.executed_items
        original.passed_items = retry.passed_items
        original.failed_items = retry.failed_items
        original.skipped_items = retry.skipped_items
        original.child_failures = list(retry.child_failures)
        if retry.ok:
            recovered += 1
        else:
            confirmed += 1
    print(
        f"Browser retry result: {len(failed)} attempted / {recovered} recovered / {confirmed} confirmed failure"
        f"{'s' if confirmed != 1 else ''}.",
        flush=True,
    )
    return len(failed), recovered, confirmed


def _retry_counts(results: list[Result]) -> tuple[int, int, int]:
    attempted = sum(result.retry_attempted for result in results)
    recovered = sum(result.retry_attempted and result.retry_recovered for result in results)
    confirmed = attempted - recovered
    return attempted, recovered, confirmed


def _print_retry_accounting(results: list[Result]) -> None:
    attempted, recovered, confirmed = _retry_counts(results)
    if not attempted:
        return
    print("\nBROWSER RETRY ACCOUNTING", flush=True)
    print("-" * 78, flush=True)
    print(
        f"    - Fresh-process retries: {attempted} attempted / {recovered} recovered / {confirmed} confirmed",
        flush=True,
    )
    for result in results:
        if result.retry_attempted:
            outcome = "recovered" if result.retry_recovered else "confirmed failure"
            print(f"      · {result.job.name}: {outcome}", flush=True)


def _meaningful_output_lines(output: str) -> list[str]:
    lines=[]
    for raw in (output or "").splitlines():
        line=raw.strip()
        if not line:
            continue
        if "DeprecationWarning" in line or "warnings summary" in line:
            continue
        if line.startswith("-- Docs:") or line.startswith("=") or line.startswith("_"):
            continue
        lines.append(line)
    return lines


def _concise_reason(output: str, *, timed_out: bool = False) -> str:
    if timed_out:
        return "process watchdog timeout"
    lines=_meaningful_output_lines(output)
    for line in reversed(lines):
        if line.startswith("BREADMAKER_FAILURE_REASON::"):
            return line.split("::",3)[-1][:320]
    # Pytest short summaries are the best signal and already collapse traceback noise.
    for line in reversed(lines):
        if line.startswith(("FAILED ", "ERROR ")):
            parts=line.split(" - ",1)
            return parts[1] if len(parts)==2 else line
    for line in reversed(lines):
        if line.startswith("E   "):
            return line[4:].strip()
        if any(token in line for token in ("AssertionError", "TimeoutError", "TypeError", "ReferenceError", "BREADMAKER_")):
            return line[:320]
    return (lines[-1] if lines else "pytest exited unsuccessfully")[:320]


def _failure_entries(results: list[Result]) -> list[tuple[str,str,str,float]]:
    entries=[]
    for result in results:
        if result.ok:
            continue
        if result.child_failures:
            for nodeid,reason,phase in result.child_failures:
                label=reason
                if phase:
                    phase_label={"setup":"setup","call":"test body","teardown":"teardown"}.get(phase,phase)
                    label=f"[{phase_label}] {label}"
                entries.append((result.job.category,nodeid,label,result.duration))
            # A process watchdog can terminate after some child results were already
            # serialized. Keep the process timeout visible as its own diagnostic.
            if result.timed_out:
                entries.append((result.job.category,result.job.name,"process watchdog timeout after partial pytest results",result.duration))
            continue
        # Fallback for collection/import crashes or hard process termination before
        # pytest could emit JUnit child results.
        entries.append((result.job.category,result.job.name,_concise_reason(result.output,timed_out=result.timed_out),result.duration))
    return entries


def _normalize_reason(reason: str) -> str:
    import re
    text=" ".join(str(reason).split())
    text=re.sub(r"0x[0-9a-fA-F]+","0x…",text)
    text=re.sub(r"\b[0-9a-f]{24,}\b","…",text)
    text=re.sub(r"\b\d{4,}\b","#",text)
    return text[:240]


def _print_failure_digest(results: list[Result]) -> None:
    entries=_failure_entries(results)
    if not entries:
        return
    from collections import defaultdict
    groups=defaultdict(list)
    for category,name,reason,duration in entries:
        groups[_normalize_reason(reason)].append((category,name,duration))
    ordered=sorted(groups.items(),key=lambda item:(-len(item[1]),item[0]))
    print("\n"+"="*78,flush=True)
    print(f"FAILURE DIGEST — {len(entries)} failing test item{'s' if len(entries)!=1 else ''}",flush=True)
    print(f"Distinct failure signatures: {len(ordered)}; largest shared signature: {len(ordered[0][1])}/{len(entries)} item(s)",flush=True)
    print("="*78,flush=True)
    for index,(reason,items) in enumerate(ordered,1):
        print(f"\n[{index}] {len(items)} × {reason}",flush=True)
        for category,name,duration in items:
            print(f"    - {name}  [{category}]",flush=True)
    accounting=[]
    for result in results:
        if not result.job.expected_items:
            continue
        if result.job.category=="durable E2E" or result.executed_items!=result.job.expected_items:
            accounting.append(result)
    if accounting:
        print("\nACCOUNTING",flush=True)
        print("-"*78,flush=True)
        for result in accounting:
            missing=max(0,result.job.expected_items-result.executed_items)
            extra=max(0,result.executed_items-result.job.expected_items)
            if missing:
                detail=f"missing {missing}"
            elif extra:
                detail=f"extra {extra}"
            else:
                detail="complete"
            print(f"    - {result.job.name}: expected {result.job.expected_items}, recorded {result.executed_items} ({detail})",flush=True)
    print("\nFull tracebacks are stored in verification-report.txt.",flush=True)
    print("Re-run with --verbose-failures to also print full pytest output here.",flush=True)


def _print_verbose_failures(results: list[Result]) -> None:
    failures = [result for result in results if not result.ok]
    if not failures:
        return
    print("\n" + "=" * 78, flush=True)
    print(f"FULL FAILURE DETAILS ({len(failures)} jobs)", flush=True)
    print("=" * 78, flush=True)
    for index, result in enumerate(failures, 1):
        suffix = " [PROCESS TIMEOUT]" if result.timed_out else ""
        print(f"\n[{index}/{len(failures)}] {result.job.category}: {result.job.name}{suffix} ({result.duration:.1f}s)", flush=True)
        print("-" * 78, flush=True)
        print(result.output.strip() or "(pytest produced no output)", flush=True)


def _default_workers(kind: str) -> int:
    cpus = os.cpu_count() or 4
    if kind == "browser":
        return max(1, min(2, cpus // 4 or 1))
    return max(2, min(8, max(2, cpus - 2)))



def _result_item_counts(results: list[Result]) -> tuple[int,int,int,int]:
    executed=sum(result.executed_items for result in results)
    passed=sum(result.passed_items for result in results)
    failed=sum(result.failed_items for result in results)
    skipped=sum(result.skipped_items for result in results)
    return executed,passed,failed,skipped


def _summary_group(result: Result) -> str:
    category = result.job.category
    if category == "unit":
        return "Unit"
    if category == "durable E2E":
        return "Durable E2E"
    if category == "current browser":
        return "Current browser"
    return "Core / API"


def _group_item_counts(results: list[Result]) -> dict[str, tuple[int, int, int, int]]:
    groups = {name: [0, 0, 0, 0] for name in ("Unit", "Core / API", "Current browser", "Durable E2E")}
    for result in results:
        row = groups[_summary_group(result)]
        row[0] += result.executed_items
        row[1] += result.passed_items
        row[2] += result.failed_items
        row[3] += result.skipped_items
    return {name: tuple(values) for name, values in groups.items()}


def _print_stage_accounting(results: list[Result]) -> None:
    groups = _group_item_counts(results)
    print("\nSTAGE ACCOUNTING", flush=True)
    print("-" * 78, flush=True)
    for name in ("Unit", "Core / API", "Current browser", "Durable E2E"):
        executed, passed, failed, skipped = groups[name]
        print(f"    - {name}: {passed} passed / {failed} failed / {skipped} skipped ({executed} executed)", flush=True)


def _write_report(
    version: str,
    mode: str,
    results: list[Result],
    preflight_ok: bool,
    *,
    fatal_error: str | None = None,
) -> None:
    lines = [
        f"Breadmaker Editor v{version} verification",
        f"Mode: {mode}",
        f"Preflight: {'PASS' if preflight_ok else 'FAIL'}",
        "",
    ]
    for result in results:
        if result.retry_attempted and result.retry_recovered:
            status = "PASS-RETRY"
        elif result.retry_attempted and not result.ok:
            status = "FAIL-RETRY"
        else:
            status = "PASS" if result.ok else ("TIMEOUT" if result.timed_out else "FAIL")
        lines.append(
            f"[{status}] {result.job.category} | {result.job.name} | {result.duration:.2f}s"
            f"{f' + retry {result.retry_duration:.2f}s' if result.retry_attempted else ''} | "
            f"items={result.executed_items} pass={result.passed_items} fail={result.failed_items} skip={result.skipped_items}"
        )
        if result.retry_attempted:
            initial_reason = _concise_reason(result.initial_output, timed_out=result.initial_timed_out)
            if result.initial_child_failures:
                initial_reason = result.initial_child_failures[0][1]
            lines.append(
                f"  RETRY: {'recovered on fresh-process retry' if result.retry_recovered else 'failed again on fresh-process retry'}"
            )
            lines.append(f"    Initial failure: {initial_reason}")
            if result.initial_output.strip():
                lines.append("  --- initial browser attempt output ---")
                lines.append(result.initial_output.rstrip())
        for nodeid, reason, phase in result.child_failures:
            suffix=f" [{phase}]" if phase else ""
            lines.append(f"  CHILD FAILURE{suffix}: {nodeid}")
            lines.append(f"    {reason}")
        if not result.ok and result.output.strip():
            lines.append(result.output.rstrip())
            lines.append("")
    if fatal_error:
        lines.extend([
            "",
            "========== VERIFIER FATAL ERROR ==========",
            fatal_error,
        ])
    failed_jobs = sum(not result.ok for result in results)
    retry_attempts, retry_recovered, retry_confirmed = _retry_counts(results)
    executed,passed,failed_items,skipped=_result_item_counts(results)
    groups = _group_item_counts(results)
    lines.extend([
        "",
        "========== VERIFICATION SUMMARY ==========",
        f"Unit items: {groups['Unit'][1]} passed / {groups['Unit'][2]} failed / {groups['Unit'][3]} skipped ({groups['Unit'][0]} executed)",
        f"Core / API items: {groups['Core / API'][1]} passed / {groups['Core / API'][2]} failed / {groups['Core / API'][3]} skipped ({groups['Core / API'][0]} executed)",
        f"Current browser items: {groups['Current browser'][1]} passed / {groups['Current browser'][2]} failed / {groups['Current browser'][3]} skipped ({groups['Current browser'][0]} executed)",
        f"Durable E2E items: {groups['Durable E2E'][1]} passed / {groups['Durable E2E'][2]} failed / {groups['Durable E2E'][3]} skipped ({groups['Durable E2E'][0]} executed)",
        f"Execution jobs: {len(results) + retry_attempts}",
        f"Browser retry jobs: {retry_attempts} attempted / {retry_recovered} recovered / {retry_confirmed} confirmed",
        f"Failed execution jobs: {failed_jobs + retry_attempts}",
        f"Confirmed failed logical jobs: {failed_jobs}",
        f"Test items executed: {executed}",
        f"Test items passed: {passed}",
        f"Test items failed: {failed_items}",
        f"Test items skipped: {skipped}",
        f"Result: {'ALL CHECKS PASSED' if preflight_ok and not failed_jobs and not fatal_error else 'NEEDS ATTENTION'}"
        + (
            f" ({retry_recovered} browser retry recovered)"
            if preflight_ok and not failed_jobs and not fatal_error and retry_recovered
            else ""
        ),
        f"Verification target: Breadmaker Editor v{version}",
        "",
    ])
    REPORT.write_text("\n".join(lines), encoding="utf-8")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Breadmaker's comprehensive release regression gate.")
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Run preflight + the full fast Unit gate + latest critical core/browser smoke; default is the full release gate.",
    )
    parser.add_argument(
        "--core-workers",
        type=int,
        default=int(os.environ.get("BREADMAKER_VERIFY_CORE_WORKERS", _default_workers("core"))),
        help="Parallel isolated core/API pytest processes (default: %(default)s).",
    )
    parser.add_argument(
        "--browser-workers",
        type=int,
        default=int(os.environ.get("BREADMAKER_VERIFY_BROWSER_WORKERS", _default_workers("browser"))),
        help="Parallel isolated Chromium pytest processes (default: %(default)s, capped by your explicit value only).",
    )
    parser.add_argument("--list", action="store_true", help="List every verification job without executing it.")
    parser.add_argument(
        "--verbose-failures",
        action="store_true",
        help="Print complete pytest failure output to the console. By default the console stays compact and the report keeps full traces.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    try:
        unit_jobs = discover_unit_jobs()
        core_jobs = discover_core_jobs(quick=args.quick)
        browser_jobs = discover_browser_jobs(quick=args.quick)
    except Exception as exc:
        message = f"DISCOVERY FAILED: {exc}"
        print(f"\n{message}", file=sys.stderr, flush=True)
        _write_report(
            version,
            'QUICK' if args.quick else 'FULL',
            [],
            False,
            fatal_error=message,
        )
        print(f"Verification target: Breadmaker Editor v{version}", flush=True)
        return 2

    print(f"Breadmaker Editor v{version} release verification", flush=True)
    durable_count=0 if args.quick else len(_test_functions(TESTS / DURABLE_E2E))
    print(
        f"Mode: {'QUICK' if args.quick else 'FULL'} | "
        f"unit modules={len(unit_jobs)} | core jobs={len(core_jobs)} | browser jobs={len(browser_jobs)}"
        f"{f' (including {durable_count} sequential durable E2E tests)' if durable_count else ''} | "
        f"workers={max(1,args.core_workers)} core / {max(1,args.browser_workers)} browser",
        flush=True,
    )

    if args.list:
        for job in [*unit_jobs, *core_jobs, *browser_jobs]:
            print(f"{job.category:16} {job.name}")
        print(f"Verification target: Breadmaker Editor v{version}", flush=True)
        return 0

    try:
        run_preflight()
        print("\nCollecting exact unit pytest items (including parametrized cases)...", flush=True)
        unit_job = collect_exact_unit_job()
        print(f"  ✓ Exact unit items: {unit_job.expected_items} in one fast execution job", flush=True)
    except Exception as exc:
        print(f"\nPRE-FLIGHT FAILED: {exc}", file=sys.stderr, flush=True)
        _write_report(
            version,
            'QUICK' if args.quick else 'FULL',
            [],
            False,
            fatal_error=f"PRE-FLIGHT FAILED: {exc}",
        )
        print(f"Verification target: Breadmaker Editor v{version}", flush=True)
        return 2

    all_results: list[Result] = []
    # Unit tests are the first executable release gate: pure/deterministic contracts
    # fail quickly before we pay for FastAPI or SQLite integration suites.
    all_results.extend(run_stage("Unit", [unit_job], 1))

    # Pay for the real shared-browser lifecycle before the long core/API stages.
    # This runs every durable test exactly once, not a second smoke subset.
    durable_browser_jobs=[job for job in browser_jobs if job.category=='durable E2E']
    current_browser_jobs=[job for job in browser_jobs if job.category=='current browser']
    if not any(not result.ok for result in all_results):
        all_results.extend(run_stage("Durable E2E", durable_browser_jobs, 1))
    if any(not result.ok for result in all_results):
        _print_stage_accounting(all_results)
        _print_failure_digest(all_results)
        _write_report(version, 'QUICK' if args.quick else 'FULL', all_results, True)
        print("Verification stopped early: remaining stages NOT verified.", flush=True)
        print(f"Full report: {REPORT.name}", flush=True)
        print(f"Verification target: Breadmaker Editor v{version}", flush=True)
        return 1

    try:
        print("\nCollecting exact core pytest items (including parametrized cases)...", flush=True)
        exact_core_jobs = collect_exact_core_jobs(quick=args.quick)
        core_jobs = plan_core_execution_jobs(exact_core_jobs)
        print(f"  ✓ Exact core items: {len(exact_core_jobs)} across {len(core_jobs)} execution jobs", flush=True)
    except Exception as exc:
        print(f"\nCORE COLLECTION FAILED: {exc}", file=sys.stderr, flush=True)
        _write_report(
            version,
            'QUICK' if args.quick else 'FULL',
            all_results,
            True,
            fatal_error=f"CORE COLLECTION FAILED: {exc}",
        )
        print(f"Verification target: Breadmaker Editor v{version}", flush=True)
        return 2

    # The 10k-paragraph scale contract intentionally keeps the same hard 10-second
    # per-item budget, but it must measure Breadmaker rather than a machine that has
    # just finished a large parallel pytest/FastAPI batch. Run it first in a dedicated
    # no-contention lane; all other core jobs retain normal parallelism afterwards.
    scale_jobs=[job for job in core_jobs if Path(job.target.split('::',1)[0]).name=='test_scale.py']
    serial_core_jobs=[job for job in core_jobs if Path(job.target.split('::',1)[0]).name in NO_CONTENTION_CORE_MODULES]
    ordinary_core_jobs=[job for job in core_jobs if job not in scale_jobs and job not in serial_core_jobs]
    all_results.extend(run_stage("Core / API scale", scale_jobs, 1))
    all_results.extend(run_stage("Core / API serial", serial_core_jobs, 1))
    all_results.extend(run_stage("Core / API", ordinary_core_jobs, max(1, args.core_workers)))

    # Durable E2E already ran in its own no-contention lane before Core/API.
    # Current browser jobs retain their normal isolation and bounded retries.
    all_results.extend(run_stage("Current browser", current_browser_jobs, max(1, args.browser_workers)))
    retry_failed_current_browser(all_results, max(1, args.browser_workers))
    _print_retry_accounting(all_results)
    _print_stage_accounting(all_results)
    if args.verbose_failures:
        _print_verbose_failures(all_results)
    _print_failure_digest(all_results)

    failures = [result for result in all_results if not result.ok]
    retry_attempts, retry_recovered, retry_confirmed = _retry_counts(all_results)
    total_execution_jobs = len(all_results) + retry_attempts
    _write_report(version, 'QUICK' if args.quick else 'FULL', all_results, True)
    print(f"Full report: {REPORT.name}", flush=True)
    executed,passed,failed_count,skipped=_result_item_counts(all_results)
    if failures:
        failed_items=len(_failure_entries(all_results))
        timed_out=sum(result.timed_out for result in failures)
        print(
            f"\nBreadmaker verification FAILED: {failed_items} diagnostic failure(s) across {len(failures)} confirmed failing logical jobs; {total_execution_jobs} execution attempts; "
            f"pytest items: {passed} passed / {failed_count} failed / {skipped} skipped ({executed} executed); {timed_out} process timeout(s).",
            file=sys.stderr,
            flush=True,
        )
        print("Copy the FAILURE DIGEST above; the full scrolling output is not needed for diagnosis.",file=sys.stderr,flush=True)
        print(f"Verification target: Breadmaker Editor v{version}", flush=True)
        return 1

    durable_results=[result for result in all_results if result.job.category=="durable E2E"]
    for result in durable_results:
        print(f"Durable accounting: {result.executed_items}/{result.job.expected_items} pytest items recorded.",flush=True)
    print(
        f"\nAll Breadmaker verification gates passed: {passed} pytest items across {total_execution_jobs} execution attempts ({skipped} skipped)"
        + (f"; {retry_recovered} browser item{'s' if retry_recovered != 1 else ''} recovered on retry." if retry_recovered else "."),
        flush=True,
    )
    print(f"Verification target: Breadmaker Editor v{version}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
