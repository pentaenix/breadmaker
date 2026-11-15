"""Pure bounded aggregate contract. No runtime reader, transport or policy store.

All proofs/context inputs must come from trusted server readers. This module does
not authenticate caller dictionaries or manufacture an authoritative context.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from hosted.firestore.ids import require_project_id
from hosted.security.saved_word_projection import DEFINITION, Projection
from hosted.security.saved_word_identity import WordIdentity, WordOwner, match_pinned_identity

SOURCE = 'cloud_saved_words_v1'
MAX_PROJECTS = 100
MAX_EXCLUSIONS = 32
_REVISION = re.compile(r'[A-Za-z0-9_-]{1,128}')


@dataclass(frozen=True)
class WordContext:
    exclusion_revision: str

    def __post_init__(self):
        if any(not isinstance(value, str) or _REVISION.fullmatch(value) is None
               for value in (self.exclusion_revision,)):
            raise ValueError('WORD_CONTEXT_UNVERIFIED')


def _timestamp(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError('WORD_OBSERVATION_TIME')
    return value.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def aggregate_verified_snapshot(*, projects, pinned_exclusions, current_identities,
                                before_context, after_context, start, end, enabled=False):
    """Candidate envelope only; caller must prove full bounded root population.

    Each project input has exact project_id/status/generation/owner/ledger keys.
    Owner is a WordOwner verified from an independent unique-owner query. Ledgers
    are server-owned metadata, never content. No historical snapshots are summed.
    """
    if enabled is not True:
        return None
    if not isinstance(before_context, WordContext) or not isinstance(after_context, WordContext):
        raise ValueError('WORD_CONTEXT_UNVERIFIED')
    begin, finish = _timestamp(start), _timestamp(end)
    if not 0 <= (end-start).total_seconds() <= 60:
        raise ValueError('WORD_OBSERVATION_TIME')
    if len(projects) > MAX_PROJECTS or len(pinned_exclusions) > MAX_EXCLUSIONS:
        raise ValueError('WORD_AGGREGATE_LIMIT')
    valid_context = before_context == after_context
    excluded = set()
    policy_valid = len(current_identities) == len(pinned_exclusions)
    for pinned, current in zip(pinned_exclusions, current_identities):
        if not match_pinned_identity(pinned, current):
            policy_valid = False
        elif isinstance(current, WordIdentity):
            excluded.add((current.hosted_user_id, current.hosted_created_at))
    seen = set()
    covered = unattributed = unknown = 0
    all_words = excluded_words = 0
    for project in projects:
        if set(project) != {'project_id', 'status', 'generation', 'owner', 'ledger'}:
            raise ValueError('WORD_PROJECT_SNAPSHOT_INVALID')
        pid = require_project_id(project['project_id'])
        if pid in seen or project['status'] != 'active':
            raise ValueError('WORD_PROJECT_POPULATION_UNVERIFIED')
        seen.add(pid)
        owner = project['owner']
        if not isinstance(owner, WordOwner) or owner.project_id != pid:
            unattributed += 1; unknown += 1
            continue
        # A policy target with the same hosted ID but a different incarnation
        # must not silently become an included new account.
        if any(uid == owner.hosted_user_id and created != owner.hosted_created_at for uid, created in excluded):
            unattributed += 1; unknown += 1
            continue
        ledger = project['ledger']
        try:
            if (not isinstance(ledger, dict)
                    or set(ledger) != {'schema_version', 'definition', 'state', 'canonical_revision', 'blocks', 'words'}
                    or type(ledger['schema_version']) is not int or ledger['schema_version'] != 1
                    or ledger['definition'] != DEFINITION or ledger['state'] != 'complete'
                    or type(project['generation']) is not int
                    or ledger['canonical_revision'] != project['generation']):
                raise ValueError()
            projection = Projection(ledger['canonical_revision'], ledger['blocks'], ledger['words'])
            if projection.words is None:
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            unknown += 1
            continue
        covered += 1
        all_words += projection.words
        if (owner.hosted_user_id, owner.hosted_created_at) in excluded:
            excluded_words += projection.words
    complete = valid_context and policy_valid and unknown == 0
    population_known = valid_context and policy_valid
    return {
        'schema_version': 1, 'source': SOURCE, 'definition': DEFINITION,
        'eligibility_scope': 'active_cloud_projects', 'policy_scope': 'saved_words_only',
        'exclusion_revision': after_context.exclusion_revision,
        'as_of': finish,
        'coverage': {'start': begin, 'end': finish,
            'completeness': 'complete' if complete else 'unknown', 'consistency': 'non_atomic',
            'eligible_projects': len(projects) if population_known else None,
            'covered_projects': covered if population_known else None,
            'unknown_projects': unknown if population_known else None,
            'unattributed_projects': unattributed if population_known else None},
        'metrics': {'all_saved_words': all_words if complete else None,
            'included_saved_words': all_words-excluded_words if complete else None,
            'excluded_saved_words': excluded_words if complete else None},
    }
