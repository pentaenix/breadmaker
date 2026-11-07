"""Assemble an allowlisted vendor runtime; retain real package/source evidence.

Runs in the disposable build stage only. APK signatures remain enabled. The
reviewed package graph must match before removing bootstrap/unused SQLite code.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / 'deploy/vendor-runtime.json'
FIELDS = {'P', 'V', 'A', 'C', 'o', 'c'}


def records(raw):
    result = {}
    for block in raw.strip().split('\n\n'):
        if not block: continue
        header = block.split('\nF:', 1)[0]
        fields = {line[0]: line[2:] for line in header.splitlines()
                  if len(line) > 2 and line[1] == ':' and line[0] in FIELDS}
        name = fields.get('P')
        if not name or name in result: raise ValueError('VENDOR_PACKAGE_DATABASE_INVALID')
        result[name] = (fields, block)
    return result


def payload(block):
    directory = None
    for line in block.splitlines():
        if line.startswith('F:'): directory = line[2:]
        elif line.startswith('R:'):
            leaf = line[2:]
            if directory is None or '/' in leaf: raise ValueError('VENDOR_PACKAGE_PATH_INVALID')
            path = PurePosixPath(directory) / leaf
            if path.is_absolute() or '..' in path.parts: raise ValueError('VENDOR_PACKAGE_PATH_INVALID')
            yield path


def locked_packages(raw, contract):
    found = records(raw)
    architecture = found.get('python-3.14-base', ({}, ''))[0].get('A')
    expected = contract['packages'].get(architecture)
    if expected is None or sorted((v[0] for v in found.values()), key=lambda v:v['P']) != expected:
        raise ValueError('VENDOR_PACKAGE_GRAPH_MISMATCH')
    return found, architecture


def install(root, contract):
    if (not root.is_absolute() or root.resolve()==Path('/') or root.is_symlink()
            or root.exists() and any(root.iterdir())):
        raise ValueError('VENDOR_INSTALL_ROOT_MUST_BE_EMPTY')
    (root / 'etc/apk').mkdir(parents=True, exist_ok=True)
    shutil.copytree('/etc/apk/keys', root / 'etc/apk/keys', dirs_exist_ok=True)
    shutil.copyfile('/etc/apk/repositories', root / 'etc/apk/repositories')
    # Pin the complete reviewed graph: mutable repositories otherwise allow
    # transitive upgrades. Unavailable pins must fail, never refresh the lock.
    roots = {row['P']: row['V'] for row in contract['packages']['x86_64']}
    if any(not re.fullmatch(r'[A-Za-z0-9_.+\-]+', k+v) for k,v in roots.items()):
        raise ValueError('VENDOR_PACKAGE_PIN_INVALID')
    subprocess.run(['apk','--root',str(root),'--initdb','--no-scripts','add','--no-cache',
                    *[f'{name}={version}' for name,version in sorted(roots.items())]],check=True)


def stage(root, mode, base_digest, *, contract_path=CONTRACT, sqlite_root=Path('/runtime-sqlite'),
          sqlite_library=Path('/runtime-libs/libsqlite3.so.0')):
    if mode not in {'helper', 'hosted'}: raise ValueError('VENDOR_RUNTIME_MODE_INVALID')
    root = root.resolve()
    if root == Path('/') or not root.is_dir(): raise ValueError('VENDOR_STAGING_ROOT_INVALID')
    raw_contract = contract_path.read_bytes(); contract = json.loads(raw_contract)
    if contract['schema'] != 1 or contract['family'] != 'wolfi' or base_digest != contract['base_digest']:
        raise ValueError('VENDOR_BASE_PROVENANCE_MISMATCH')
    database = root / 'lib/apk/db/installed'
    found, architecture = locked_packages(database.read_text(), contract)
    sbom = root / f"var/lib/db/sbom/zlib-{contract['zlib_package']}.spdx.json"
    source = f"https://github.com/madler/zlib/archive/{contract['zlib_source']}.tar.gz"
    if not any(p.get('downloadLocation') == source for p in json.loads(sbom.read_bytes())['packages']):
        raise ValueError('VENDOR_ZLIB_SOURCE_MISMATCH')
    removed = []
    owners = {}
    for name, (_, block) in found.items():
        for path in payload(block): owners.setdefault(path, set()).add(name)
    for name in ('sqlite-libs', 'py3-pip-wheel'):
        if name not in found: raise ValueError('VENDOR_REMOVAL_PACKAGE_MISSING')
        for relative in payload(found[name][1]):
            path = root / relative
            if not path.resolve().is_relative_to(root) or owners[relative] != {name}:
                raise ValueError('VENDOR_REMOVAL_PATH_UNSAFE')
            if path.is_file() or path.is_symlink(): path.unlink(); removed.append(str(relative))
        del found[name]
    stdlib = root / 'usr/lib/python3.14'
    for name in ('ensurepip', 'sqlite3' if mode == 'helper' else 'sqlite3/__main__.py'):
        path = stdlib / name
        if path.is_dir(): shutil.rmtree(path); removed.append(str(path.relative_to(root)))
        elif path.is_file(): path.unlink(); removed.append(str(path.relative_to(root)))
    if mode == 'helper':
        for path in (stdlib / 'lib-dynload').glob('_sqlite3*'):
            path.unlink(); removed.append(str(path.relative_to(root)))
    blocks = [block for _, block in found.values()]
    if mode == 'hosted':
        # This is a real custom component, never a claimed vendor package. Its
        # upstream origin/version remains visible to the unchanged OS scanner.
        destination = root / 'opt/python-libs/libsqlite3.so.0'; destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(sqlite_library,destination)
        evidence = json.loads((sqlite_root/'provenance.json').read_bytes())
        if hashlib.sha256(destination.read_bytes()).hexdigest() != evidence['library_sha256']:
            raise ValueError('SQLITE_STAGED_LIBRARY_MISMATCH')
        target = root / 'usr/share/breadmaker/sqlite'; shutil.copytree(sqlite_root,target,dirs_exist_ok=True)
        version=evidence['version'];checksum=base64.b64encode(hashlib.sha256(destination.read_bytes()).digest()).decode()
        blocks.append(f'P:breadmaker-sqlite-core\nV:{version}-r0\nA:{architecture}\nT:Breadmaker pinned SQLite core without shell or FTS5\nL:blessing\no:sqlite\nm:Breadmaker release engineering\nF:opt/python-libs\nR:libsqlite3.so.0\nZ:Q2{checksum}')
    database.write_text('\n\n'.join(blocks)+'\n\n')
    provenance=root/'usr/share/breadmaker/vendor-runtime';provenance.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(contract_path,provenance/'contract.json')
    (provenance/'assembly.json').write_text(json.dumps(dict(schema=1,mode=mode,base_digest=base_digest,
        contract_sha256=hashlib.sha256(raw_contract).hexdigest(),architecture=architecture,
        original_packages=contract['packages'][architecture],removed_files=sorted(removed)),sort_keys=True)+'\n')
    (root/'tmp').mkdir(exist_ok=True);(root/'tmp').chmod(0o1777)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('/runtime'))
    parser.add_argument('--mode',choices=('helper','hosted'),required=True)
    parser.add_argument('--base-digest',required=True);parser.add_argument('--install',action='store_true')
    args=parser.parse_args()
    contract=json.loads(CONTRACT.read_bytes())
    if args.base_digest != contract['base_digest']: raise ValueError('VENDOR_BASE_PROVENANCE_MISMATCH')
    if args.install: install(args.root,contract)
    stage(args.root,args.mode,args.base_digest)


if __name__=='__main__': main()
