"""Build-only deterministic projection of the pinned Italian lexicon; no SQL at runtime."""
import hashlib
import json
from pathlib import Path

import gruut_lang_it
from narration.pronunciation import _italian_lexicon, _ITALIAN_LEXICON_SHA256


def main():
    root = Path(gruut_lang_it.__file__).parent
    target = root / 'breadmaker-lexicon.json'
    if target.exists():
        raise ValueError('NARRATION_LEXICON_BUILD_TARGET_EXISTS')
    lexicon = _italian_lexicon(root)
    raw = json.dumps(lexicon, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    if len(lexicon) != 28918 or hashlib.sha256(raw).hexdigest() != _ITALIAN_LEXICON_SHA256:
        raise ValueError('NARRATION_LANGUAGE_DATA_PIN_MISMATCH')
    target.write_bytes(raw)
    if _italian_lexicon(root) != lexicon:
        raise ValueError('NARRATION_LEXICON_PROJECTION_MISMATCH')
    print('Exact Italian lexicon projection PASS: 28918 words')


if __name__ == '__main__':
    main()
