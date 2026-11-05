"""Prefer the runner's existing HTTPS Ubuntu archive, retaining signed fallbacks.

GitHub's Ubuntu image configures /etc/apt/apt-mirrors.txt with Azure first.
A slow but successful Azure response never triggers its error-based failover.
This optional CI optimization changes priorities, not repositories, suites or
signing keys. Runners without the Azure mirror list retain their vendor sources.
"""
from __future__ import annotations

import os
from pathlib import Path
import platform
import sys

_AZURE = 'http://azure.archive.ubuntu.com/ubuntu/'
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
    if current == _PREFERRED:
        return text
    if current != _DEFAULT:
        raise ValueError('CI_APT_MIRROR_LAYOUT_CHANGED')
    # Keep every original URI and all formatting outside the priority values.
    return ''.join(line.replace(_DEFAULT[line.split()[0]], _PREFERRED[line.split()[0]])
                   if line.strip() else line for line in text.splitlines(keepends=True))


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
        return 2
    print('Ubuntu HTTPS archive preferred; existing repositories and signing keys preserved.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
