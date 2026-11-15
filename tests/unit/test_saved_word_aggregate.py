from datetime import datetime, timezone
import pytest
from hosted.security.saved_word_aggregate import aggregate_verified_snapshot, WordContext
from hosted.security.saved_word_identity import WordOwner, WordIdentity

STAMP = '2023-11-14T22:13:20+00:00'
UID = 'usr_'+'a'*32
NOW = datetime(2026, 11, 15, tzinfo=timezone.utc)
CONTEXT = WordContext('policy_1')


def project(pid='a'*32, words=5):
    return {'project_id': pid, 'status': 'active', 'generation': 3,
        'owner': WordOwner(pid, UID, STAMP, STAMP),
        'ledger': {'schema_version': 1, 'definition': 'saved_text_unicode_words_v1',
            'state': 'complete', 'canonical_revision': 3, 'blocks': 1, 'words': words}}


def proof(subject='external'):
    return WordIdentity('identity-platform://synthetic', subject, 1700000000000,
                        UID, STAMP, 1700000001.0, '2023-11-14T22:13:21+00:00')


def collect(rows, **kwargs):
    return aggregate_verified_snapshot(projects=rows, pinned_exclusions=kwargs.pop('pins', []),
        current_identities=kwargs.pop('identities', []), before_context=CONTEXT,
        after_context=kwargs.pop('after', CONTEXT), start=NOW, end=NOW, enabled=True, **kwargs)


def test_all_included_excluded_and_alias_deduplication():
    rows = [project(), project('b'*32, 7)]
    all_data = collect(rows)
    assert all_data['metrics'] == {'all_saved_words':12, 'included_saved_words':12, 'excluded_saved_words':0}
    identities = [proof('one'), proof('two')]
    excluded = collect(rows, pins=identities, identities=identities)
    assert excluded['metrics'] == {'all_saved_words':12, 'included_saved_words':0, 'excluded_saved_words':12}


@pytest.mark.parametrize('change', ['missing_owner', 'stale', 'unknown'])
def test_unproven_coverage_never_returns_partial_totals(change):
    row = project()
    if change == 'missing_owner': row['owner'] = None
    if change == 'stale': row['ledger']['canonical_revision'] = 2
    if change == 'unknown': row['ledger'].update(state='unknown', blocks=None, words=None)
    result = collect([project('b'*32), row])
    assert set(result['metrics'].values()) == {None}
    assert result['coverage']['unknown_projects'] == 1
    assert result['coverage']['covered_projects'] == 1


def test_policy_or_context_change_invalidates_entire_snapshot():
    changed = WordContext('policy_2')
    for result in [collect([project()], after=changed), collect([project()], pins=[proof()], identities=[])]:
        assert set(result['metrics'].values()) == {None}
        assert result['coverage']['eligible_projects'] is None


def test_same_hosted_id_different_incarnation_is_not_silently_included():
    row = project()
    row['owner'] = WordOwner(row['project_id'], UID, '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z')
    result = collect([row], pins=[proof()], identities=[proof()])
    assert result['metrics']['included_saved_words'] is None
    assert result['coverage']['unattributed_projects'] == 1


def test_verified_empty_zero_and_unverified_off():
    assert collect([])['metrics']['all_saved_words'] == 0
    assert aggregate_verified_snapshot(projects=None, pinned_exclusions=None,
        current_identities=None, before_context=None, after_context=None,
        start=None, end=None) is None


def test_duplicate_project_never_double_counts():
    with pytest.raises(ValueError):
        collect([project(), project()])
