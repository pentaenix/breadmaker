"""Unwired account-source word observation; never sums legacy project ledgers.

This new source/schema needs paired Cloud/Admin approval and cache eligibility
validation before selection. Construction performs no IO. No client or policy
writer is constructed here and no numeric fleet counter is persisted.
"""
from hosted.firestore.ids import identity_binding_id
from hosted.security.saved_word_reader import SavedWordsUnavailable, USER_FIELDS, BINDING_FIELDS
from hosted.security.saved_word_identity import verify_identity, match_pinned_identity
from hosted.security.account_metric_eligibility import GoogleFirestoreAccountEligibility
from hosted.security.saved_word_policy import WordPolicy
from hosted.security.saved_word_projection import DEFINITION
from hosted.security.saved_word_aggregate import _timestamp
from hosted.security import account_word_state as records

SOURCE = 'cloud_account_saved_words_v1'


class GoogleFirestoreAccountWordsProducer(GoogleFirestoreAccountEligibility):
    def collect(self, *, deadline):
        if not self.enabled: return None
        if (self.client is None or self.policy_reader is None or not callable(self.account_reader)
                or type(self.issuer) is not str or not self.issuer.startswith('identity-platform://')):
            raise SavedWordsUnavailable('ACCOUNT_WORD_NOT_CONFIGURED')
        self._remaining(deadline); start = self.clock()
        try:
            policy = self.policy_reader.load_policy(deadline=deadline)
            if not isinstance(policy, WordPolicy): raise SavedWordsUnavailable('WORD_POLICY_UNVERIFIED')
            facts=self.snapshot(deadline=deadline)
            profiles,manifest=facts['profiles'],facts['manifests'];inactive={}
            excluded = []
            for pin in policy.entries:
                profile = profiles.get(pin.hosted_user_id)
                if profile is None:
                    if pin.hosted_user_id in facts['all_profiles']: continue
                    profile = self._get(('users', pin.hosted_user_id), USER_FIELDS, deadline)
                    if profile is not None and profile.get('status') == 'active':
                        raise SavedWordsUnavailable('ACCOUNT_WORD_POPULATION_CHANGED')
                    inactive[pin.hosted_user_id] = profile
                    continue  # retained policy for a deleted/inactive incarnation
                if pin.issuer != self.issuer or profile['created_at'] != pin.hosted_created_at:
                    raise SavedWordsUnavailable('ACCOUNT_WORD_POLICY_INCARNATION_CHANGED')
                provider = self.account_reader(issuer=pin.issuer, subject=pin.subject, deadline=deadline)
                self._remaining(deadline)
                if type(provider) is not dict or set(provider) != {'issuer', 'subject', 'created_at_ms', 'tenant_id'}:
                    raise SavedWordsUnavailable('WORD_IDENTITY_PROVIDER_UNVERIFIED')
                path = ('identity_bindings', identity_binding_id(pin.issuer+'\n'+pin.subject))
                binding = self._get(path, BINDING_FIELDS, deadline)
                if binding is None: raise SavedWordsUnavailable('WORD_IDENTITY_BINDING_MISSING')
                current = verify_identity(issuer=pin.issuer, subject=pin.subject, binding_path=path,
                    binding=binding, provider=provider, hosted_user=profile)
                if not match_pinned_identity(pin, current): raise SavedWordsUnavailable('WORD_IDENTITY_CHANGED')
                excluded.append(current.hosted_user_id)
            snapshots, covered = [], 0
            for uid, profile in profiles.items():
                raw = self._get(records.path(uid), tuple(records.FIELDS), deadline)
                snapshots.append(dict(profile=profile, words=raw, owned_projects=manifest[uid]))
                try:
                    records.sum_accounts([snapshots[-1]])
                    covered += 1
                except ValueError: pass
            # Explicitly non-atomic observation with all populations, owner and
            # incarnation pins rechecked. A retained cache must also revalidate
            # current eligibility on use; this producer alone cannot prove that.
            after=self.snapshot(deadline=deadline)
            if after['locks']!=facts['locks']:raise SavedWordsUnavailable('ACCOUNT_WORD_ELIGIBILITY_CHANGED')
            if after!=facts:raise SavedWordsUnavailable('ACCOUNT_WORD_POPULATION_CHANGED')
            for uid, expected in inactive.items():
                if self._get(('users', uid), USER_FIELDS, deadline) != expected:
                    raise SavedWordsUnavailable('ACCOUNT_WORD_POPULATION_CHANGED')
            revision = self.policy_reader.read_policy_revision(deadline=deadline)
            self._remaining(deadline); end = self.clock()
            if not 0 <= (end-start).total_seconds() <= 60: raise SavedWordsUnavailable('ACCOUNT_WORD_WINDOW')
            complete = covered == len(profiles) and revision == policy.revision
            metrics = records.sum_accounts(snapshots, excluded=excluded) if complete else {
                key: None for key in ('all_saved_words', 'included_saved_words', 'excluded_saved_words')}
            return dict(schema_version=2, source=SOURCE, definition=DEFINITION,
                eligibility_scope='active_cloud_accounts', policy_scope='saved_words_only',
                exclusion_revision=revision, as_of=_timestamp(end),eligibility_fingerprint=facts['fingerprint'],
                coverage=dict(start=_timestamp(start), end=_timestamp(end),
                    completeness='complete' if complete else 'unknown', consistency='non_atomic',
                    eligible_accounts=len(profiles), covered_accounts=covered,
                    unknown_accounts=len(profiles)-covered,
                    eligible_projects=sum(len(p) for p in manifest.values())), metrics=metrics)
        except SavedWordsUnavailable: raise
        except Exception: raise SavedWordsUnavailable('ACCOUNT_WORD_COLLECTION_UNAVAILABLE') from None
