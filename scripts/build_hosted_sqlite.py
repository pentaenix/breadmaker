"""Build the pinned upstream core only; never compile or copy SQLite's shell.

The existing CPython extension uses the same libsqlite3.so.0 ABI. Debian package
records describe the actual replacement, and source/binary provenance travels
with the runtime. This is a portable-package codec, not hosted durable storage.
"""
from __future__ import annotations

import hashlib
import argparse
import io
import json
from pathlib import Path
import subprocess
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FLAGS = ['-O2', '-fPIC', '-shared', '-Wl,-soname,libsqlite3.so.0',
         '-Wl,-z,relro,-z,now', '-DSQLITE_THREADSAFE=1', '-DSQLITE_USE_URI=1',
         '-DSQLITE_ENABLE_COLUMN_METADATA', '-DHAVE_USLEEP=1']


def sources(raw: bytes, pin: dict) -> dict[str, bytes]:
    if len(raw) > 4 * 1024**2 or hashlib.sha3_256(raw).hexdigest() != pin['sha3_256']:
        raise ValueError('SQLITE_SOURCE_HASH_MISMATCH')
    result = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for leaf, digest in pin['members'].items():
            names = [name for name in archive.namelist() if name.endswith('/' + leaf)]
            if len(names) != 1 or archive.getinfo(names[0]).file_size > 12 * 1024**2:
                raise ValueError('SQLITE_SOURCE_MEMBER_INVALID')
            content = archive.read(names[0])
            if hashlib.sha256(content).hexdigest() != digest:
                raise ValueError('SQLITE_SOURCE_MEMBER_HASH_MISMATCH')
            result[leaf] = content
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package-format', choices=('debian', 'none'), default='debian')
    args = parser.parse_args()
    pin = json.loads((ROOT / 'deploy/sqlite-source.json').read_text())
    with urllib.request.urlopen(pin['url'], timeout=30) as response:
        if response.url != pin['url']:
            raise ValueError('SQLITE_SOURCE_REDIRECT_REJECTED')
        content = sources(response.read(4 * 1024**2 + 1), pin)
    library = Path('/runtime-libs/libsqlite3.so.0')
    library.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        directory = Path(directory)
        for leaf, raw in content.items():
            (directory / leaf).write_bytes(raw)
        subprocess.run(['gcc', *FLAGS, str(directory / 'sqlite3.c'), '-o', str(library),
                        '-lm', '-ldl', '-lpthread'], check=True)
    evidence = dict(pin, compile_flags=FLAGS,
                    library_sha256=hashlib.sha256(library.read_bytes()).hexdigest(),
                    shell_included=False, fts5_included=False)
    destination = Path('/runtime-sqlite')
    destination.mkdir(exist_ok=True)
    (destination / 'provenance.json').write_text(json.dumps(evidence, sort_keys=True))
    # Non-Debian assembly records the real custom component in its own package
    # database after removing the replaced vendor library. Never forge dpkg data.
    if args.package_format == 'none':
        return
    architecture = subprocess.check_output(['dpkg', '--print-architecture'], text=True).strip()
    records = Path('/runtime-status')
    records.mkdir(exist_ok=True)
    (records / 'libsqlite3-0').write_text(
        'Package: libsqlite3-0\nStatus: install ok installed\nPriority: optional\n'
        'Section: libs\nMaintainer: Breadmaker release engineering\n'
        f'Architecture: {architecture}\nSource: sqlite3\nVersion: {pin["package_version"]}\n'
        'Description: Breadmaker checksum-pinned upstream SQLite core, without shell or FTS5\n'
        f'Homepage: {pin["url"]}\n')


if __name__ == '__main__':
    main()
