"""Offline final-image identity, package coverage and security-surface proof."""
from __future__ import annotations
import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import ssl
import sys
import zlib

ROOT=Path(__file__).resolve().parents[1]


def check(mode):
    if os.geteuid()!=10001 or os.getegid()!=10001:
        raise ValueError('VENDOR_RUNTIME_NONROOT_REQUIRED')
    contract_raw=(ROOT/'deploy/vendor-runtime.json').read_bytes();contract=json.loads(contract_raw)
    stored=Path('/usr/share/breadmaker/vendor-runtime')
    assembly=json.loads((stored/'assembly.json').read_bytes())
    if ((stored/'contract.json').read_bytes()!=contract_raw or assembly['mode']!=mode
            or assembly['base_digest']!=contract['base_digest']
            or assembly['contract_sha256']!=hashlib.sha256(contract_raw).hexdigest()
            or assembly['original_packages']!=contract['packages'][assembly['architecture']]
            or tuple(sys.version_info[:3])!=tuple(map(int,contract['python_runtime'].split('.')))
            or zlib.ZLIB_RUNTIME_VERSION!=contract['zlib_runtime']
            or ssl.OPENSSL_VERSION!=contract['openssl_runtime']):
        raise ValueError('VENDOR_RUNTIME_IDENTITY_MISMATCH')
    actual={}
    for block in Path('/lib/apk/db/installed').read_text().strip().split('\n\n'):
        header=block.split('\nF:',1)[0]
        row={line[0]:line[2:] for line in header.splitlines() if len(line)>2 and line[1]==':' and line[0] in 'PVACoc'}
        if not row.get('P') or row['P'] in actual:raise ValueError('VENDOR_RUNTIME_PACKAGE_INVALID')
        actual[row['P']]=row
    expected={row['P']:row for row in assembly['original_packages'] if row['P'] not in {'sqlite-libs','py3-pip-wheel'}}
    if mode=='hosted':
        pin=json.loads((ROOT/'deploy/sqlite-source.json').read_bytes())
        expected['breadmaker-sqlite-core']={'P':'breadmaker-sqlite-core','V':pin['version']+'-r0','A':assembly['architecture'],'o':'sqlite'}
    if actual!=expected:raise ValueError('VENDOR_RUNTIME_PACKAGE_MISMATCH')
    if 'ID=wolfi' not in Path('/etc/os-release').read_text().splitlines():raise ValueError('VENDOR_RUNTIME_OS_MISMATCH')
    for name in ('apk','sh','bash','busybox','gcc','g++','pip','pip3','sqlite3'):
        if shutil.which(name):raise ValueError('VENDOR_RUNTIME_TOOL_PRESENT')
    if any(importlib.util.find_spec(name) is not None for name in ('pip','ensurepip')):
        raise ValueError('VENDOR_RUNTIME_BOOTSTRAP_PRESENT')
    context=ssl.create_default_context()
    if context.minimum_version<ssl.TLSVersion.TLSv1_2 or context.verify_mode!=ssl.CERT_REQUIRED or not context.check_hostname or not context.get_ca_certs():
        raise ValueError('VENDOR_RUNTIME_TLS_POLICY_INVALID')
    if mode=='helper':
        if any(importlib.util.find_spec(name) is not None for name in ('sqlite3','_sqlite3')):raise ValueError('HELPER_SQLITE_PRESENT')
        try:ctypes.CDLL('libsqlite3.so.0')
        except OSError:pass
        else:raise ValueError('HELPER_SQLITE_PRESENT')
    return dict(mode=mode,family=contract['family'],base_digest=contract['base_digest'],
                contract_sha256=assembly['contract_sha256'],python=contract['python_runtime'],
                zlib=zlib.ZLIB_RUNTIME_VERSION,openssl=ssl.OPENSSL_VERSION,packages=len(actual),
                architecture=assembly['architecture'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--mode',choices=('helper','hosted'),required=True)
    print(json.dumps(check(parser.parse_args().mode),sort_keys=True))
