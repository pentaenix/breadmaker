from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXCLUDED_PARTS = {
    '.git', '.venv', '.pytest_cache', '__pycache__', '_unconfigured',
    'htmlcov', '.mypy_cache', '.ruff_cache', 'playwright-report', 'test-results',
}
EXCLUDED_SUFFIXES = {
    '.pyc', '.pyo', '.sqlite3', '.sqlite3-wal', '.sqlite3-shm', '.lock', '.bak', '.tgz', '.log', '.tmp',
}
EXCLUDED_NAMES = {'.DS_Store', '.coverage', 'backup_credentials.json', 'credential_obfuscation.key', 'credential_encryption.key', 'google_oauth_client.json', 'product_config.json', 'verification-report.txt'}


def should_include(relative: Path) -> bool:
    if any(part in EXCLUDED_PARTS for part in relative.parts):
        return False
    if relative.name in EXCLUDED_NAMES:
        return False
    if relative.name.startswith('HANDOFF_') and relative.name.endswith('_WIP.md'):
        return False
    if relative.name.startswith('HARDENING_') and relative.name.endswith('.md'):
        return False
    # Never package accidental command-line artifacts such as '-h'/'--help'.
    if relative.name.startswith('-'):
        return False
    name=relative.name.lower()
    if any(name.endswith(suffix) for suffix in EXCLUDED_SUFFIXES):
        return False
    # Local patch/release backups may use numbered suffixes such as .bak541.
    # Treat every .bak* suffix as transient so emergency working copies can never
    # leak into a distributable source archive.
    if any(suffix.lower().startswith('.bak') for suffix in relative.suffixes):
        return False
    # Ad-hoc pre-release source snapshots (for example app.py.pre730) are also
    # development artifacts and must never ship in a release archive.
    if any(suffix.lower().startswith('.pre') for suffix in relative.suffixes):
        return False
    # Release archives are source packages, never containers for other archives.
    if relative.suffix.lower()=='.zip':
        return False
    return True


def build_release(destination: Path | None = None) -> Path:
    version=(ROOT/'VERSION').read_text(encoding='utf-8').strip()
    destination=Path(destination) if destination else ROOT.parent/f'BreadmakerEditor_v{version}.zip'
    destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.exists(): destination.unlink()
    with zipfile.ZipFile(destination,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for path in sorted(ROOT.rglob('*')):
            if not path.is_file(): continue
            relative=path.relative_to(ROOT)
            if not should_include(relative): continue
            archive.write(path,Path('BreadmakerEditor')/relative)
    return destination


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1] in {'-h','--help'}:
        print('Usage: python scripts/package_release.py [destination.zip]')
        raise SystemExit(0)
    target=Path(sys.argv[1]).expanduser().resolve() if len(sys.argv)>1 else None
    print(build_release(target))
