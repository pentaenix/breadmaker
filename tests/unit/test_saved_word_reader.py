from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from hosted.security.saved_word_policy import WordPolicy
from hosted.security.saved_word_reader import GoogleFirestoreSavedWordsProducer, SavedWordsUnavailable

PID = 'a'*32
UID = 'usr_'+'a'*32
NOW = datetime(2026, 11, 15, tzinfo=timezone.utc)


def records():
    return {
        'projects/'+PID: {'status': 'active', 'generation': 2, 'title': 'NEVER_PROJECT'},
        'projects/'+PID+'/members/'+UID: {'role': 'owner', 'user_id': UID, 'created_at': '2023-11-14T22:13:21Z'},
        'users/'+UID: {'user_id': UID, 'status': 'active', 'created_at': '2023-11-14T22:13:20Z', 'email': 'NEVER_USER'},
        'admin_saved_word_coverage/'+PID: {'schema_version': 1, 'definition': 'saved_text_unicode_words_v1',
            'state': 'complete', 'canonical_revision': 2, 'blocks': 1, 'words': 3},
    }


class Client:
    def __init__(self, rows): self.rows, self.calls = rows, []
    def document(self, path):
        client = self
        class Ref:
            def get(self, **kwargs):
                client.calls.append(('get', path, kwargs))
                data = client.rows.get(path)
                return SimpleNamespace(exists=data is not None,
                    to_dict=lambda: {key: data[key] for key in kwargs['field_paths'] if key in data})
        return Ref()
    def collection(self, path):
        client = self
        class Query:
            def where(self, *, filter):
                self.filter = filter; return self
            def select(self, fields):
                self.fields = fields; return self
            def limit(self, limit):
                self.maximum = limit; return self
            def stream(self, **kwargs):
                client.calls.append(('query', path, self.fields, self.maximum, kwargs))
                rows = [(p,d) for p,d in sorted(client.rows.items())
                    if p.rsplit('/',1)[0] == path and d.get(self.filter.field_path) == self.filter.value]
                return iter([SimpleNamespace(reference=SimpleNamespace(path=p),
                    to_dict=lambda d=d: {key:d[key] for key in self.fields if key in d})
                    for p,d in rows[:self.maximum]])
        return Query()


class PolicyReader:
    def __init__(self, after='policy_1'): self.after = after
    def load_policy(self, *, deadline): return WordPolicy('policy_1')
    def read_policy_revision(self, *, deadline): return self.after


def producer(client, reader=None, account=None):
    return GoogleFirestoreSavedWordsProducer(client, policy_reader=reader or PolicyReader(),
        account_reader=account or (lambda **kwargs: (_ for _ in ()).throw(AssertionError('unneeded account read'))),
        issuer='identity-platform://synthetic', enabled=True, monotonic=lambda: 100, clock=lambda: NOW)


def test_bounded_content_free_collector_and_current_policy_fence():
    client = Client(records())
    result = producer(client).collect(deadline=130)
    assert result['metrics']['all_saved_words'] == 3
    assert 'NEVER' not in str(result) and UID not in str(result) and PID not in str(result)
    roots = [call for call in client.calls if call[0] == 'query' and call[1] == 'projects']
    assert len(roots) == 2 and all(call[3] == 101 for call in roots)
    assert all('email' not in call[2].get('field_paths', []) for call in client.calls if call[0] == 'get')
    assert all(call[-1]['retry'] is None and call[-1]['timeout'] <= 4 for call in client.calls)
    changed = producer(Client(records()), PolicyReader('policy_2')).collect(deadline=130)
    assert set(changed['metrics'].values()) == {None}
    assert changed['coverage']['eligible_projects'] is None


def test_missing_ledger_and_ambiguous_owner_are_unknown_not_zero():
    rows = records(); rows.pop('admin_saved_word_coverage/'+PID)
    result = producer(Client(rows)).collect(deadline=130)
    assert result['metrics']['all_saved_words'] is None
    assert result['coverage']['unknown_projects'] == 1
    rows = records()
    other = 'usr_'+'b'*32
    rows['projects/'+PID+'/members/'+other] = {'role':'owner','user_id':other,'created_at':'2023-11-14T22:13:21Z'}
    result = producer(Client(rows)).collect(deadline=130)
    assert result['coverage']['unattributed_projects'] == 1


def test_disabled_expired_and_root_overflow_make_no_false_totals():
    assert GoogleFirestoreSavedWordsProducer().collect(deadline=None) is None
    client = Client(records())
    with pytest.raises(SavedWordsUnavailable, match='DEADLINE'):
        producer(client).collect(deadline=99)
    assert client.calls == []
    rows = {'projects/'+format(i,'032x'): {'status':'active','generation':1} for i in range(101)}
    client = Client(rows)
    with pytest.raises(SavedWordsUnavailable, match='PROJECT_LIMIT'):
        producer(client).collect(deadline=130)
    assert len(client.calls) == 1


def test_trusted_identity_callback_and_owner_profile_are_deduplicated():
    from hosted.security.saved_word_identity import WordIdentity
    from hosted.firestore.ids import identity_binding_id
    issuer = 'identity-platform://synthetic'; subject = 'external'
    proof = WordIdentity(issuer, subject, 1700000000000, UID,
        '2023-11-14T22:13:20Z', 1700000001.0, '2023-11-14T22:13:21Z')
    rows = records()
    rows['identity_bindings/'+identity_binding_id(issuer+'\n'+subject)] = {
        'issuer': issuer, 'subject': subject, 'user_id': UID, 'status': 'active',
        'created_at_epoch': 1700000001.0, 'created_at': '2023-11-14T22:13:21Z'}
    class PinnedPolicy(PolicyReader):
        def load_policy(self, *, deadline): return WordPolicy('policy_1', (proof,))
    calls = []
    def account(**kwargs):
        calls.append(kwargs)
        return {'issuer':issuer,'subject':subject,'created_at_ms':1700000000000,'tenant_id':None}
    client = Client(rows)
    result = producer(client, PinnedPolicy(), account).collect(deadline=130)
    assert result['metrics']['excluded_saved_words'] == 3
    assert result['metrics']['included_saved_words'] == 0
    assert calls == [{'issuer':issuer,'subject':subject,'deadline':130}]
    assert sum(call[0] == 'get' and call[1] == 'users/'+UID for call in client.calls) == 1


def test_root_revision_change_prevents_publication():
    client = Client(records())
    original = client.document
    def document(path):
        reference = original(path)
        if path.startswith('admin_saved_word_coverage/'):
            old_get = reference.get
            def get(**kwargs):
                result = old_get(**kwargs)
                client.rows['projects/'+PID]['generation'] += 1
                return result
            reference.get = get
        return reference
    client.document = document
    with pytest.raises(SavedWordsUnavailable, match='ROOTS_CHANGED'):
        producer(client).collect(deadline=130)
