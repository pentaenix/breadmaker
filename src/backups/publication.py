from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

PUBLICATION_FORMAT = 'breadmaker-backup-publication'
PUBLICATION_VERSION = 2
PUBLICATION_STATE = 'PUBLISHED'
PUBLICATION_SUFFIX = '.publication.json'

PUBLICATION_STATES = (
    'SNAPSHOTTED',
    'PACKAGED',
    'LOCAL_VALIDATED',
    'TRANSFERRED',
    'DESTINATION_VERIFIED',
    'PUBLISHED',
)
PUBLICATION_STATE_ORDER = {state: index for index, state in enumerate(PUBLICATION_STATES)}


def sha256_file(path: Path, chunk_bytes: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as fh:
        for chunk in iter(lambda: fh.read(chunk_bytes), b''):
            digest.update(chunk)
    return digest.hexdigest()


def publication_filename(package_file: str) -> str:
    name = Path(str(package_file or '')).name
    if name != str(package_file or '') or not name.endswith('.breadmaker'):
        raise ValueError('invalid package filename')
    return name + PUBLICATION_SUFFIX


def build_publication_manifest(*, package_file: str, package_sha256: str, package_bytes: int,
                               package_info: dict[str, Any], package_locator: str = '',
                               published_at: str = '') -> dict[str, Any]:
    project_id = str(package_info.get('project_id') or '').strip()
    generation = int(package_info.get('project_generation') or 0)
    if not project_id or generation <= 0:
        raise ValueError('publication requires project id and canonical generation')
    if len(str(package_sha256 or '')) != 64:
        raise ValueError('publication requires sha256')
    return {
        'publication_format': PUBLICATION_FORMAT,
        'publication_version': PUBLICATION_VERSION,
        'state': PUBLICATION_STATE,
        'project_id': project_id,
        'project_generation': generation,
        'project_name': str(package_info.get('name') or 'Untitled Book'),
        'project_fingerprint': str(package_info.get('project_fingerprint') or ''),
        'canonical_state_hash': str(package_info.get('canonical_state_hash') or ''),
        'package_file': Path(package_file).name,
        'package_locator': str(package_locator or ''),
        'package_sha256': str(package_sha256),
        'package_bytes': int(package_bytes),
        'created_at': str(package_info.get('created_at') or ''),
        'recovery_metadata': dict(package_info.get('recovery_metadata') or {}) if isinstance(package_info.get('recovery_metadata'), dict) else {},
        'published_at': str(published_at or ''),
    }


def validate_publication_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError('publication manifest is not an object')
    if value.get('publication_format') != PUBLICATION_FORMAT or int(value.get('publication_version') or 0) != PUBLICATION_VERSION:
        raise ValueError('unsupported publication manifest')
    if value.get('state') != PUBLICATION_STATE:
        raise ValueError('publication is not published')
    project_id = str(value.get('project_id') or '').strip()
    generation = int(value.get('project_generation') or 0)
    package_file = str(value.get('package_file') or '')
    package_hash = str(value.get('package_sha256') or '')
    package_bytes = int(value.get('package_bytes') or -1)
    if not project_id or generation <= 0:
        raise ValueError('publication identity invalid')
    if Path(package_file).name != package_file or not package_file.endswith('.breadmaker'):
        raise ValueError('publication package filename invalid')
    if len(package_hash) != 64 or any(ch not in '0123456789abcdefABCDEF' for ch in package_hash):
        raise ValueError('publication package hash invalid')
    if package_bytes < 0:
        raise ValueError('publication package size invalid')
    out = dict(value)
    out['project_generation'] = generation
    out['package_bytes'] = package_bytes
    return out


def publication_candidate(value: dict[str, Any]) -> dict[str, Any]:
    row = validate_publication_manifest(value)
    return {
        'file': row['package_file'],
        'candidate': True,
        'verified': False,
        'verification_status': 'not_verified',
        'publication_state': 'published',
        'project_id': row['project_id'],
        'name': str(row.get('project_name') or 'Recovered Project'),
        'created_at': str(row.get('created_at') or row.get('published_at') or ''),
        'bytes': int(row.get('package_bytes') or 0),
        'fingerprint': str(row.get('project_fingerprint') or ''),
        'canonical_state_hash': str(row.get('canonical_state_hash') or ''),
        'project_generation': int(row.get('project_generation') or 0),
        'package_sha256': str(row.get('package_sha256') or ''),
        'package_locator': str(row.get('package_locator') or ''),
        'recovery_metadata': dict(row.get('recovery_metadata') or {}) if isinstance(row.get('recovery_metadata'), dict) else {},
        'workday': str((row.get('recovery_metadata') or {}).get('workday') or '') if isinstance(row.get('recovery_metadata'), dict) else '',
    }


def json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
