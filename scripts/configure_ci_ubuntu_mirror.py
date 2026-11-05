"""Prefer the runner's existing HTTPS Ubuntu archive, retaining signed fallbacks.

GitHub's Ubuntu image configures /etc/apt/apt-mirrors.txt with Azure first.
A slow but successful Azure response never triggers its error-based failover.
This optional CI optimization changes priorities, not repositories, suites or
signing keys. Runners without the Azure mirror list retain their vendor sources.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
import platform
import sys
from urllib.parse import urlsplit

_AZURE = 'http://azure.archive.ubuntu.com/ubuntu/'
_AWS = 'http://us-east-2.ec2.archive.ubuntu.com/ubuntu/'
_ARCHIVE = 'https://archive.ubuntu.com/ubuntu/'
_SECURITY = 'https://security.ubuntu.com/ubuntu/'
_DEFAULT = {_AZURE: 'priority:1', _ARCHIVE: 'priority:2', _SECURITY: 'priority:3'}
_PREFERRED = {_AZURE: 'priority:2', _ARCHIVE: 'priority:1', _SECURITY: 'priority:3'}


def prefer_https_archive(text: str) -> str:
    rows = [line.split() for line in text.splitlines() if line.strip()]
    if (len(rows) != 3 or any(len(row) != 2 for row in rows)
            or len({row[0] for row in rows}) != 3):
        raise ValueError('CI_APT_MIRROR_LAYOUT_CHANGED')
    current = dict(rows)
    # Observed AWS-hosted image: its regional mirror is already appropriate.
    # Recognize the exact signed-vendor fallback layout without rewriting it.
    if current == {_AWS:'priority:1',_ARCHIVE:'priority:2',_SECURITY:'priority:3'}:
        return text
    if current == _PREFERRED:
        return text
    if current != _DEFAULT:
        raise ValueError('CI_APT_MIRROR_LAYOUT_CHANGED')
    # Keep every original URI and all formatting outside the priority values.
    return ''.join(line.replace(_DEFAULT[line.split()[0]], _PREFERRED[line.split()[0]])
                   if line.strip() else line for line in text.splitlines(keepends=True))


def safe_layout_summary(text: str) -> list[dict]:
    """Expose public feed shape, stripping credentials and arbitrary fields."""
    result=[]
    for line in text.splitlines()[:12]:
        fields=line.split()
        if not fields:continue
        try:
            uri=urlsplit(fields[0])
            trusted=uri.hostname in {'azure.archive.ubuntu.com','archive.ubuntu.com','security.ubuntu.com'} or bool(uri.hostname and uri.hostname.endswith('.ec2.archive.ubuntu.com'))
            feed=f'{uri.scheme}://{uri.hostname}'+(uri.path if uri.path in {'/ubuntu','/ubuntu/',''} else '/<redacted>') if trusted else '<unrecognized>'
        except ValueError:
            feed='<unrecognized>'
        result.append({'feed':feed,'fields':[field if re.fullmatch(r'(?:priority|arch|architecture):[0-9A-Za-z_,-]+',field) else '<redacted>' for field in fields[1:]]})
    return result


def main() -> int:
    if (sys.platform != 'linux' or os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('RUNNER_OS') != 'Linux' or os.geteuid() != 0):
        print('CI_APT_MIRROR_REQUIRES_GITHUB_LINUX_ROOT', file=sys.stderr)
        return 2
    release = platform.freedesktop_os_release()
    if release.get('ID') != 'ubuntu' or release.get('VERSION_ID') != '24.04':
        print('CI_APT_MIRROR_REQUIRES_UBUNTU_24_04', file=sys.stderr)
        return 2
    path = Path('/etc/apt/apt-mirrors.txt')
    original=None
    try:
        original = path.read_text()
        updated = prefer_https_archive(original)
        if updated != original:
            path.write_text(updated)
    except FileNotFoundError:
        # Some hosted images do not use the Azure mirror list. There is no
        # priority to optimize; leave all vendor APT sources and keys untouched.
        print('Azure mirror list absent; runner repositories left unchanged.')
        return 0
    except (OSError, ValueError) as error:
        print(f'CI_APT_MIRROR_CONFIGURATION_BLOCKED:{type(error).__name__}', file=sys.stderr)
        if isinstance(error,ValueError) and isinstance(original,str):
            print('CI_APT_MIRROR_LAYOUT '+json.dumps(safe_layout_summary(original)),file=sys.stderr)
        return 2
    if _AWS in original:
        print('Regional Ubuntu mirror retained; repositories and signing keys unchanged.')
    else:
        print('Ubuntu HTTPS archive preferred; existing repositories and signing keys preserved.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
