"""Bounded scheduling contracts; no network, credentials, or real projects."""
from contextvars import ContextVar
from threading import Event, Lock

import pytest

from hosted.package_import import PREPARE_WORKERS, _parallel


def test_refills_slots_without_waiting_for_slowest_wave_and_preserves_order():
    released = Event()
    def work(index):
        if index == 0:
            assert released.wait(2), 'later work must refill an idle worker'
        if index == 4:
            released.set()
        return index
    assert _parallel(work, list(range(12)), workers=4) == list(range(12))


def test_bounded_concurrency_and_isolated_request_context():
    context = ContextVar('import-test-context', default='missing')
    context.set('owner-staging')
    gate = Event();lock = Lock();active = peak = 0
    def work(index):
        nonlocal active, peak
        assert context.get() == 'owner-staging'
        context.set(index)
        with lock:
            active += 1;peak = max(peak, active)
            if active == PREPARE_WORKERS:
                gate.set()
        assert gate.wait(2)
        assert context.get() == index
        with lock: active -= 1
        return index
    assert _parallel(work, list(range(64)), workers=PREPARE_WORKERS) == list(range(64))
    assert peak == PREPARE_WORKERS
    assert context.get() == 'owner-staging'


def test_failed_work_does_not_schedule_remaining_suffix():
    seen = []
    def work(index):
        seen.append(index)
        raise RuntimeError('synthetic publication rejected')
    with pytest.raises(RuntimeError, match='rejected'):
        _parallel(work, list(range(64)), workers=1)
    assert seen == [0]


@pytest.mark.parametrize('workers', [0, -1, PREPARE_WORKERS + 1])
def test_rejects_unbounded_worker_configuration(workers):
    with pytest.raises(ValueError, match='WORKER_LIMIT'):
        _parallel(lambda value: value, [1], workers=workers)


def test_empty_parallel_work_has_no_callback():
    assert _parallel(lambda value: pytest.fail('no item'), []) == []
