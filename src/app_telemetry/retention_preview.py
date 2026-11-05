"""Optional synthetic retention worker; never registered or started by the app."""
import threading


class SyntheticRetentionRunner:
    def __init__(self, authority, *, synthetic_only, interval_seconds=60):
        if synthetic_only is not True or not .01 <= interval_seconds <= 60:
            raise ValueError('SYNTHETIC_RETENTION_CONFIGURATION_INVALID')
        self.authority = authority
        self.interval = interval_seconds
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is not None:
            return False
        self._thread = threading.Thread(target=self._run, daemon=True, name='synthetic-retention-preview')
        self._thread.start()
        return True

    def _run(self):
        while not self._stop.is_set():
            try:
                self.authority.maintain_synthetic_retention()
            except Exception:
                pass  # Optional maintenance must never propagate into editing.
            self._stop.wait(self.interval)

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=.2)
        return self._thread is None or not self._thread.is_alive()
