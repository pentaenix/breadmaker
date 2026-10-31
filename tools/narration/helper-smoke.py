"""Short isolated-image/offline helper proof, synthetic text only; no cloud I/O."""
import json
from pathlib import Path
import time

from hosted.narration.pronunciation_helper import PronunciationWorker


def main():
    worker = PronunciationWorker()
    cases = [('it', '', ['ciao', 'mondo']), ('ja', 'こんにちは世界。', []),
             ('zh-CN', '你好世界。', [])]
    results = []
    try:
        for language, text, words in cases:
            start = time.monotonic()
            result = worker.run(language, text, words, deadline=start + 25)
            assert result and isinstance(result, dict)
            assert result.get('items') if language != 'it' else result.get('phonemes')
            results.append({'language': language, 'seconds': round(time.monotonic() - start, 3)})
        peak = Path('/sys/fs/cgroup/memory.peak')
        print(json.dumps({'synthetic_offline_samples': results,
                          'container_peak_bytes': int(peak.read_text()) if peak.exists() else None}))
    finally:
        worker.close()


if __name__ == '__main__':
    main()
