"""Cooperative provider deadline shared with copied HTTP worker contexts."""
from contextvars import ContextVar
import time

deadline = ContextVar('narration_provider_deadline', default=None)


def io_timeout(maximum=10):
    until = deadline.get()
    if until is None:
        return maximum
    remaining = until - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('NARRATION_PROVIDER_DEADLINE')
    return min(maximum, remaining)
