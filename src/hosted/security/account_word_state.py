"""Account-owned saved-word state. No provider, content reader or global counter.

All state is backend-owned under users/{uid}. Canonical project generations and
owner incarnations remain independent completeness proofs, not numeric sources
for Admin. Absence or partial initialization never means a complete zero.
"""
from copy import deepcopy
from hosted.firestore.ids import require_user_id, require_project_id
from hosted.security.saved_word_identity import _stamp
from hosted.security.saved_word_projection import DEFINITION

MAX_PROJECTS = 100
MAX_WORDS = 100_000_000
MAX_BLOCKS = 100_000
MAX_REVISION = 2**53 - 1
PROJECT_FIELDS = {'project_created_at', 'canonical_revision', 'blocks',
                  'known_blocks', 'known_words', 'initialized'}
FIELDS = {'schema_version', 'definition', 'user_id', 'user_created_at',
          'initialized', 'projects', 'state', 'saved_words', 'current_cloud_projects'}


def path(user_id):
    return ('users', require_user_id(user_id), 'metrics', 'saved_words_v1')


def _integer(value, maximum):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError('ACCOUNT_WORD_COUNT_INVALID')
    return value


def project_entry(*, created_at, revision, blocks, known_blocks, known_words, initialized):
    _stamp(created_at)
    if not _integer(revision, MAX_REVISION) or type(initialized) is not bool:
        raise ValueError('ACCOUNT_WORD_PROJECT_INVALID')
    _integer(blocks, MAX_BLOCKS); _integer(known_blocks, blocks); _integer(known_words, MAX_WORDS)
    if known_blocks == 0 and known_words != 0:
        raise ValueError('ACCOUNT_WORD_PROJECT_INVALID')
    return dict(project_created_at=created_at, canonical_revision=revision,
        blocks=blocks, known_blocks=known_blocks, known_words=known_words, initialized=initialized)


def state(user_id, created_at, *, projects=None, initialized=False):
    uid = require_user_id(user_id); _stamp(created_at)
    if type(initialized) is not bool or (projects is not None and type(projects) is not dict):
        raise ValueError('ACCOUNT_WORD_STATE_INVALID')
    rows = deepcopy({} if projects is None else projects)
    if len(rows) > MAX_PROJECTS:
        raise ValueError('ACCOUNT_WORD_PROJECT_LIMIT')
    for pid, row in rows.items():
        require_project_id(pid)
        if type(row) is not dict or set(row) != PROJECT_FIELDS:
            raise ValueError('ACCOUNT_WORD_PROJECT_INVALID')
        if project_entry(created_at=row['project_created_at'], revision=row['canonical_revision'],
            blocks=row['blocks'], known_blocks=row['known_blocks'], known_words=row['known_words'],
            initialized=row['initialized']) != row:
            raise ValueError('ACCOUNT_WORD_PROJECT_INVALID')
    complete = initialized and all(row['initialized'] and row['known_blocks'] == row['blocks']
                                   for row in rows.values())
    total = sum(row['known_words'] for row in rows.values()) if complete else None
    if total is not None: _integer(total, MAX_PROJECTS * MAX_WORDS)
    return dict(schema_version=1, definition=DEFINITION, user_id=uid, user_created_at=created_at,
        initialized=initialized, projects=rows, state='complete' if complete else 'unknown',
        saved_words=total, current_cloud_projects=len(rows) if initialized else None)


def parse(raw, user_id, created_at):
    if (type(raw) is not dict or set(raw) != FIELDS or type(raw['schema_version']) is not int
            or raw['schema_version'] != 1 or raw['definition'] != DEFINITION
            or raw['user_id'] != require_user_id(user_id) or raw['user_created_at'] != created_at):
        raise ValueError('ACCOUNT_WORD_STATE_INVALID')
    expected = state(user_id, created_at, projects=raw['projects'], initialized=raw['initialized'])
    if raw['saved_words'] is not None: _integer(raw['saved_words'], MAX_PROJECTS * MAX_WORDS)
    if raw['current_cloud_projects'] is not None: _integer(raw['current_cloud_projects'], MAX_PROJECTS)
    if expected != raw:
        raise ValueError('ACCOUNT_WORD_STATE_INVALID')
    return expected


def sum_accounts(accounts, *, excluded=()):
    """Trusted backend snapshots only. No source totals are stored globally.

    Each row has active profile incarnation, account state and an independently
    verified owned-project manifest {pid:(created_at,generation)}. The caller
    proves complete account/population enumeration and current exclusion pins.
    Inactive accounts contribute nothing even while their retained docs remain.
    """
    if type(accounts) is not list or len(accounts) > 100:
        raise ValueError('ACCOUNT_WORD_ACCOUNT_LIMIT')
    if type(excluded) not in (tuple, list, set, frozenset) or len(excluded) > 100:
        raise ValueError('ACCOUNT_WORD_EXCLUSION_INVALID')
    excluded = {require_user_id(uid) for uid in excluded}
    seen_users, seen_projects = set(), set()
    result = dict(all_saved_words=0, included_saved_words=0, excluded_saved_words=0)
    for row in accounts:
        if type(row) is not dict or set(row) != {'profile', 'words', 'owned_projects'}:
            raise ValueError('ACCOUNT_WORD_SNAPSHOT_INVALID')
        profile = row['profile']; uid = require_user_id(profile['user_id'])
        if uid in seen_users: raise ValueError('ACCOUNT_WORD_DUPLICATE_ACCOUNT')
        seen_users.add(uid)
        if profile.get('status') != 'active': continue
        record = parse(row['words'], uid, profile['created_at'])
        manifest = row['owned_projects']
        if type(manifest) is not dict or record['state'] != 'complete':
            raise ValueError('ACCOUNT_WORD_COVERAGE_UNKNOWN')
        for pid, pin in manifest.items():
            require_project_id(pid)
            if type(pin) is not tuple or len(pin) != 2 or not _integer(pin[1], MAX_REVISION):
                raise ValueError('ACCOUNT_WORD_MANIFEST_INVALID')
            _stamp(pin[0])
        expected = {pid: (item['project_created_at'], item['canonical_revision'])
                    for pid, item in record['projects'].items()}
        if expected != manifest:
            raise ValueError('ACCOUNT_WORD_COVERAGE_STALE')
        if seen_projects.intersection(manifest):
            raise ValueError('ACCOUNT_WORD_DUPLICATE_PROJECT')
        seen_projects.update(manifest)
        words = record['saved_words']
        result['all_saved_words'] += words
        result['excluded_saved_words' if uid in excluded else 'included_saved_words'] += words
    return result
