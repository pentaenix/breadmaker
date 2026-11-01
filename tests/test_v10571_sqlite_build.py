"""Pinned native-core source validation; no download/compiler in unit tests."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_hosted_sqlite', ROOT / 'scripts/build_hosted_sqlite.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def archive():
    members = {'sqlite3.c': b'core source fixture', 'sqlite3.h': b'core header fixture'}
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as z:
        for name, raw in members.items():
            z.writestr('sqlite/' + name, raw)
        z.writestr('sqlite/shell.c', 'this shell must never enter the build inputs')
    raw = output.getvalue()
    pin = dict(sha3_256=hashlib.sha3_256(raw).hexdigest(),
               members={name: hashlib.sha256(data).hexdigest() for name, data in members.items()})
    return raw, pin, members


def test_pinned_archive_selects_only_exact_core_members():
    raw, pin, members = archive()
    assert builder.sources(raw, pin) == members


def test_modified_archive_or_member_is_rejected_before_compilation():
    raw, pin, _ = archive()
    with pytest.raises(ValueError, match='SOURCE_HASH_MISMATCH'):
        builder.sources(raw + b'changed', pin)
    pin['members']['sqlite3.c'] = '0' * 64
    with pytest.raises(ValueError, match='MEMBER_HASH_MISMATCH'):
        builder.sources(raw, pin)


def test_duplicate_core_member_is_rejected_even_with_matching_archive_pin():
    raw, pin, _ = archive()
    output = io.BytesIO(raw)
    with zipfile.ZipFile(output, 'a') as z:
        z.writestr('second/sqlite3.c', b'core source fixture')
    raw = output.getvalue()
    pin['sha3_256'] = hashlib.sha3_256(raw).hexdigest()
    with pytest.raises(ValueError, match='MEMBER_INVALID'):
        builder.sources(raw, pin)


def test_hosted_builder_keeps_real_package_and_source_provenance():
    pin = json.loads((ROOT / 'deploy/sqlite-source.json').read_text())
    assert tuple(map(int, pin['version'].split('.'))) >= (3, 54, 0)
    assert pin['url'].startswith('https://www.sqlite.org/')
    assert set(pin['members']) == {'sqlite3.c', 'sqlite3.h'}
    assert len(pin['sha3_256']) == 64
    docker = (ROOT / 'deploy/Dockerfile.hosted').read_text()
    assert 'scripts/build_hosted_sqlite.py' in docker
    assert 'scripts/assert_hosted_sqlite.py' in docker
    assert 'COPY --from=build /runtime-sqlite /usr/share/breadmaker/sqlite' in docker
    assert 'COPY --from=build /runtime-status /var/lib/dpkg/status.d' in docker


def test_synthetic_portable_export_reopens_with_identical_text_and_schema():
    probe_spec = importlib.util.spec_from_file_location('assert_hosted_sqlite', ROOT / 'scripts/assert_hosted_sqlite.py')
    probe = importlib.util.module_from_spec(probe_spec)
    probe_spec.loader.exec_module(probe)
    probe.portable_package()
