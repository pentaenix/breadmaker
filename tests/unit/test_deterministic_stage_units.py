from __future__ import annotations

import threading
import pytest

from reliability.deterministic import DeterministicStageController


def test_unpaused_hook_records_trace_without_blocking():
    controller = DeterministicStageController()
    controller.hook("save")("committed", {"generation": 3})
    trace = controller.trace()
    assert len(trace) == 1
    assert trace[0].operation == "save"
    assert trace[0].stage == "committed"
    assert trace[0].context == {"generation": 3}


def test_pause_rejects_duplicate_stage_key():
    controller = DeterministicStageController()
    controller.pause("save", "before_commit")
    with pytest.raises(ValueError, match="ALREADY_PAUSED"):
        controller.pause("save", "before_commit")


def test_wait_and_release_coordinate_exact_stage():
    controller = DeterministicStageController()
    controller.pause("save", "before_commit")
    thread = threading.Thread(target=lambda: controller.hook("save")("before_commit", {"id": 9}))
    thread.start()
    record = controller.wait_until("save", "before_commit", timeout=0.5)
    assert record.context == {"id": 9}
    controller.release("save", "before_commit")
    thread.join(timeout=0.5)
    assert not thread.is_alive()


@pytest.mark.parametrize("method", ["wait", "release"])
def test_unknown_pause_key_is_rejected(method):
    controller = DeterministicStageController()
    with pytest.raises(ValueError, match="NOT_PAUSED"):
        if method == "wait":
            controller.wait_until("missing", "stage", timeout=0)
        else:
            controller.release("missing", "stage")
