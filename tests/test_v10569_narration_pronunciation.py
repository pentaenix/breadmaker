"""Private helper protocol/worker bounds without dictionaries, IAM or models."""
from concurrent.futures import TimeoutError as WorkerTimeout
import time
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hosted.narration.pronunciation_helper import PronunciationWorker, build_helper
from narration.pronunciation import PronunciationError, _ipa, validate_input


class Future:
    def __init__(self, outcome):
        self.outcome = outcome
    def result(self, *, timeout):
        assert 0 < timeout <= 25
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class Executor:
    outcome = {'items': [{'type': 'phonetic', 'value': 'a ', 'subtitles': 'Synthetic'}]}
    made = []
    def __init__(self, *, max_workers, mp_context):
        assert max_workers == 1 and mp_context.get_start_method() == 'spawn'
        self.killed = False
        self.calls = []
        self.made.append(self)
    def submit(self, fn, *args):
        self.calls.append(args)
        return Future(self.outcome)
    def kill_workers(self):
        self.killed = True


@pytest.fixture
def worker():
    Executor.made = []
    Executor.outcome = {'items': [{'type': 'phonetic', 'value': 'a ', 'subtitles': 'Synthetic'}]}
    return PronunciationWorker(executor_factory=Executor)


def test_same_frontend_reused_but_language_switch_releases_dictionary_process(worker):
    call = lambda language: worker.run(language, 'Synthetic', [], deadline=time.monotonic() + 27)
    call('ja'); first = Executor.made[-1]
    call('ja'); assert len(Executor.made) == 1
    call('zh-CN'); assert first.killed and len(Executor.made) == 2
    worker.close(); assert Executor.made[-1].killed


def test_timeout_kills_worker_instead_of_abandoning_native_thread(worker):
    Executor.outcome = WorkerTimeout()
    with pytest.raises(PronunciationError, match='TIMEOUT'):
        worker.run('ja', 'Synthetic', [], deadline=time.monotonic() + 27)
    assert Executor.made[-1].killed and worker.executor is None


def test_expired_or_busy_call_does_not_start_another_worker(worker):
    with pytest.raises(PronunciationError, match='TIMEOUT'):
        worker.run('ja', 'Synthetic', [], deadline=0)
    with worker.lock:
        with pytest.raises(PronunciationError, match='BUSY'):
            worker.run('ja', 'Synthetic', [], deadline=time.monotonic() + 27)
    assert not Executor.made


def test_private_helper_rejects_extra_fields_invalid_types_and_oversized_body(worker):
    http = TestClient(build_helper(worker))
    assert http.post('/pronounce', json={'language': 'ja', 'text': 'Synthetic', 'uid': 'foreign'}).status_code == 400
    assert http.post('/pronounce', json={'language': 'ja', 'text': 123}).status_code == 400
    assert http.post('/pronounce', content=b'x' * (256 * 1024 + 1)).status_code == 413
    assert not Executor.made


def test_health_does_not_load_or_warm_a_dictionary_and_results_use_same_payload(worker):
    http = TestClient(build_helper(worker))
    assert http.get('/health').json() == {'ok': True}
    assert not Executor.made
    response = http.post('/pronounce', json={'language': 'ja', 'text': 'Synthetic'})
    assert response.status_code == 200 and response.json() == Executor.outcome
    assert Executor.made[-1].calls == [('ja', 'Synthetic', [])]


def test_raw_frontend_exception_never_exposes_private_input(worker):
    Executor.outcome = RuntimeError('Synthetic private input must not appear')
    response = TestClient(build_helper(worker)).post('/pronounce', json={'language': 'ja', 'text': 'Synthetic'})
    assert response.status_code == 422 and response.json()['detail'] == 'NARRATION_PRONUNCIATION_FAILED'
    assert 'private input' not in response.text and Executor.made[-1].killed


def test_ipa_and_native_input_rules_are_retained():
    assert _ipa('t͡ʃg') == 'ʧɡ'
    with pytest.raises(PronunciationError, match='UNSUPPORTED'):
        _ipa('Unsupported 😀')
    validate_input('it', '', ['ciao'])
    for args in [('it', 'not allowed', ['ciao']), ('ja', 'text', ['word']), ('zh-CN', ' ', []),
                 ('ja', 'x' * 20001, []), ('it', '', ['x' * 301])]:
        with pytest.raises(PronunciationError, match='INVALID'):
            validate_input(*args)


def _synthetic_child(language, text, words):
    if text.startswith('wait:'):
        Path(text[5:]).write_text(str(os.getpid()))
        time.sleep(5)
    return {'pid': os.getpid()}


def test_real_spawned_worker_is_reused_and_killed_on_timeout(tmp_path):
    worker = PronunciationWorker(converter=_synthetic_child)
    try:
        first = worker.run('ja', 'Synthetic', [], deadline=time.monotonic() + 4)
        assert worker.run('ja', 'Synthetic', [], deadline=time.monotonic() + 2) == first
        ready = tmp_path / 'synthetic-worker-pid'
        with pytest.raises(PronunciationError, match='TIMEOUT'):
            worker.run('ja', 'wait:' + str(ready), [], deadline=time.monotonic() + .5)
        assert int(ready.read_text()) == first['pid']
        gone = False
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            try:
                os.kill(first['pid'], 0)
            except ProcessLookupError:
                gone = True
                break
            time.sleep(.01)
        assert gone and worker.executor is None
        assert worker.run('ja', 'Synthetic', [], deadline=time.monotonic() + 4)['pid'] != first['pid']
    finally:
        worker.close()
