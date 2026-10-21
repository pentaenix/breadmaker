"""Shared, bounded browser lifecycle for verification; never loads user profiles."""
from __future__ import annotations

import faulthandler
import os
from pathlib import Path
import shutil
import sys
import threading
from contextlib import contextmanager


@contextmanager
def _deadline(seconds, phase):
    done = threading.Event()

    def guard():
        if not done.wait(seconds):
            os.write(2, f'\nBREADMAKER_BROWSER_HARD_TIMEOUT: {phase[0]}\n'.encode())
            faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
            os._exit(124)

    threading.Thread(target=guard, name='breadmaker-browser-lifecycle-guard', daemon=True).start()
    try:
        yield
    finally:
        done.set()


def _phase(current, name):
    current[0] = name
    print(f'BREADMAKER_BROWSER_PHASE: {name}', flush=True)


def start_browser(*, startup_seconds=30.0, hard_seconds=40.0, driver_factory=None):
    # Do not raise SIGALRM inside Playwright's greenlet/asyncio machinery: that
    # can interrupt driver initialization and strand its synchronous cleanup.
    if driver_factory is None:
        from playwright.sync_api import sync_playwright
        driver_factory = sync_playwright
    current = ['driver-start']
    playwright = None
    with _deadline(hard_seconds, current):
        try:
            _phase(current, 'driver-start')
            playwright = driver_factory().start()
            configured = os.environ.get('BREADMAKER_TEST_BROWSER')
            managed = Path(playwright.chromium.executable_path)
            executable = configured or (str(managed) if managed.is_file() else next(
                (shutil.which(name) for name in ('chromium', 'chromium-browser', 'google-chrome', 'google-chrome-stable', 'chrome') if shutil.which(name)), None))
            if not executable or not Path(executable).is_file():
                raise RuntimeError('No Chromium runtime; run python -m playwright install chromium')
            _phase(current, 'chromium-launch')
            browser = playwright.chromium.launch(
                executable_path=executable, headless=True, args=['--no-sandbox'],
                timeout=startup_seconds * 1000,
            )
            _phase(current, 'ready')
            return playwright, browser, executable
        except BaseException:
            _phase(current, 'startup-failed')
            if playwright is not None:
                _phase(current, 'failed-start-driver-stop')
                try:
                    playwright.stop()
                except Exception:
                    pass
            raise


def close_browser(playwright, browser, *, hard_seconds=40.0):
    current = ['chromium-close']
    with _deadline(hard_seconds, current):
        try:
            _phase(current, 'chromium-close')
            browser.close()
        finally:
            _phase(current, 'driver-stop')
            playwright.stop()


def probe():
    playwright, browser, executable = start_browser()
    try:
        # Local synthetic page only: prove rendering/JavaScript, not just a file.
        page = browser.new_page()
        page.set_content('<title>Breadmaker browser probe</title><main>ready</main>', timeout=10000)
        if page.title() != 'Breadmaker browser probe' or page.locator('main').inner_text() != 'ready':
            raise RuntimeError('Browser page probe failed')
        print(f'BREADMAKER_BROWSER_EXECUTABLE::{executable}', flush=True)
    finally:
        close_browser(playwright, browser)


if __name__ == '__main__':
    probe()
