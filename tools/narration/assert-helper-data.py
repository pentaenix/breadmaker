"""Build-time public language-data integrity; never reads manuscripts or ADC."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sysconfig


def check(root=None):
    root = Path(root or sysconfig.get_path('purelib'))
    pins = json.loads((Path(__file__).with_name('language-data-pins.json')).read_text())
    if set(pins) != {'schema', 'files'} or pins['schema'] != 1 or len(pins['files']) != 10:
        raise ValueError('NARRATION_LANGUAGE_DATA_MANIFEST_INVALID')
    for item in pins['files']:
        path = root / item['path']
        if (path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve())
                or path.stat().st_size != item['size']):
            raise ValueError('NARRATION_LANGUAGE_DATA_PIN_MISMATCH')
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if path.stat().st_size != item['size'] or digest != item['sha256']:
            raise ValueError('NARRATION_LANGUAGE_DATA_PIN_MISMATCH')
    for relative in ('unidic_lite/dicdir/COPYING', 'unidic_lite/dicdir/BSD', 'unidic_lite/dicdir/AUTHORS'):
        if not (root / relative).is_file():
            raise ValueError('NARRATION_LANGUAGE_NOTICE_MISSING')
    return len(pins['files'])


if __name__ == '__main__':
    print(f'Pinned public pronunciation data PASS: {check()} files')
    # This assertion is specific to the dedicated helper image, not the native
    # development environment which legitimately has other Editor dependencies.
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--isolated-image', action='store_true')
    if parser.parse_args().isolated_image:
        assert all(importlib.util.find_spec(name) is None for name in ('google', 'backend', 'torch', 'transformers'))
