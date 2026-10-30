"""Standalone private Cloud Run helper: no application runtime or cloud SDK.

Cloud Run IAM MUST restrict invocation to the app principal. This process gets
NO data-plane/KMS permissions. Only this separate image installs IT/JA/ZH data.
No request/access logging, inference model, private persistence or runtime fetch.
"""
from concurrent.futures import ProcessPoolExecutor, TimeoutError as WorkerTimeout
import json
import multiprocessing
from threading import BoundedSemaphore
import time

import anyio
from fastapi import FastAPI, HTTPException, Request

from narration.pronunciation import PronunciationError, convert, validate_input

MAX_BODY = 256 * 1024
MAX_RESULT = 2 * 1024**2
_REQUESTS = BoundedSemaphore(1)


class PronunciationWorker:
    """One reusable child, forcibly killed on deadline or language change.

    A timed-out native dictionary call must not survive in a background thread.
    Switching languages restarts the child so three dictionaries cannot accumulate
    beyond the reviewed 1 GiB instance. Idle children do no work or polling.
    """
    def __init__(self, *, converter=convert, executor_factory=ProcessPoolExecutor):
        if not hasattr(executor_factory, 'kill_workers'):
            raise RuntimeError('NARRATION_HELPER_PYTHON_314_REQUIRED')
        self.converter, self.executor_factory = converter, executor_factory
        self.executor = self.language = None
        self.lock = BoundedSemaphore(1)

    def close(self):
        if self.executor is not None:
            executor, self.executor, self.language = self.executor, None, None
            executor.kill_workers()

    def run(self, language, text, words, *, deadline):
        validate_input(language, text, words)
        if deadline <= time.monotonic():
            raise PronunciationError('NARRATION_PRONUNCIATION_TIMEOUT')
        if not self.lock.acquire(blocking=False):
            raise PronunciationError('NARRATION_PRONUNCIATION_BUSY')
        try:
            if self.language != language:
                self.close()
            if self.executor is None:
                self.executor = self.executor_factory(max_workers=1,
                    mp_context=multiprocessing.get_context('spawn'))
                self.language = language
            future = self.executor.submit(self.converter, language, text, words)
            result = future.result(timeout=max(.001, min(25, deadline - time.monotonic())))
            if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode()) > MAX_RESULT:
                raise PronunciationError('NARRATION_PRONUNCIATION_RESULT_TOO_LARGE')
            return result
        except WorkerTimeout:
            self.close()
            raise PronunciationError('NARRATION_PRONUNCIATION_TIMEOUT') from None
        except (PronunciationError, ImportError):
            raise
        except Exception:
            self.close()
            raise PronunciationError('NARRATION_PRONUNCIATION_FAILED') from None
        finally:
            self.lock.release()


def build_helper(worker=None):
    worker = worker or PronunciationWorker()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get('/health')
    def health():
        return {'ok': True}  # IAM still required; no dictionary warming

    @app.post('/pronounce')
    async def pronounce(request: Request):
        if not _REQUESTS.acquire(blocking=False):
            raise HTTPException(429, 'NARRATION_PRONUNCIATION_BUSY', headers={'Retry-After': '1'})
        deadline = time.monotonic() + 27
        try:
            try:
                length = int(request.headers.get('content-length', '0'))
            except ValueError:
                raise HTTPException(400, 'REQUEST_CONTENT_LENGTH_INVALID') from None
            if length < 0:
                raise HTTPException(400, 'REQUEST_CONTENT_LENGTH_INVALID')
            if length > MAX_BODY:
                raise HTTPException(413, 'NARRATION_REQUEST_TOO_LARGE')
            pieces, size = [], 0
            with anyio.fail_after(2):
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_BODY:
                        raise HTTPException(413, 'NARRATION_REQUEST_TOO_LARGE')
                    pieces.append(chunk)
            try:
                data = json.loads(b''.join(pieces))
                if not isinstance(data, dict) or set(data) - {'language', 'text', 'words'}:
                    raise ValueError()
                language, text, words = data['language'], data.get('text', ''), data.get('words', [])
                validate_input(language, text, words)
            except (KeyError, TypeError, ValueError, UnicodeError):
                raise HTTPException(400, 'NARRATION_PRONUNCIATION_INVALID') from None
            try:
                return await anyio.to_thread.run_sync(lambda: worker.run(language, text, words, deadline=deadline))
            except ImportError:
                raise HTTPException(503, 'NARRATION_LANGUAGE_NOT_INSTALLED') from None
            except PronunciationError as exc:
                code = str(exc)
                status = 429 if code.endswith('_BUSY') else 504 if code.endswith('_TIMEOUT') else 422
                raise HTTPException(status, code, headers={'Cache-Control': 'private, no-store'}) from None
        except TimeoutError:
            raise HTTPException(408, 'NARRATION_REQUEST_TIMEOUT') from None
        finally:
            _REQUESTS.release()

    return app


app = build_helper()
