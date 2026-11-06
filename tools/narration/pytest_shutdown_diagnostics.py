"""Observe slow API fixture teardown without changing pytest's watchdog."""
import faulthandler
import json
import os
import time
from pathlib import Path
import pytest

_ROOT=Path(os.environ['BM_SHUTDOWN_DIAGNOSTICS'])
_ROOT.mkdir(parents=True,exist_ok=True)
_TRACE=(_ROOT/'thread-stacks.log').open('w')


def _record(item,phase,started):
    with (_ROOT/'phase-times.jsonl').open('a') as output:
        output.write(json.dumps({'node':item.nodeid,'phase':phase,
            'seconds':round(time.monotonic()-started,4)})+'\n')


@pytest.hookimpl(hookwrapper=True,tryfirst=True)
def pytest_runtest_setup(item):
    started=time.monotonic()
    try:yield
    finally:_record(item,'setup',started)


@pytest.hookimpl(hookwrapper=True,tryfirst=True)
def pytest_runtest_call(item):
    started=time.monotonic()
    try:yield
    finally:_record(item,'call',started)


@pytest.hookimpl(hookwrapper=True,tryfirst=True)
def pytest_runtest_teardown(item,nextitem):
    started=time.monotonic()
    _TRACE.write('\nTEARDOWN '+item.nodeid+'\n');_TRACE.flush()
    faulthandler.dump_traceback_later(1,repeat=True,file=_TRACE)
    try:yield
    finally:
        faulthandler.cancel_dump_traceback_later()
        _record(item,'teardown',started)


def pytest_unconfigure(config):
    faulthandler.cancel_dump_traceback_later();_TRACE.close()
