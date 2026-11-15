# Saved-word identity and aggregate candidate (uncomposed)

These are pure source interfaces, not live data readers or executable policy
writers. They perform no provider calls. No public handler accepts their inputs.
All evidence must be supplied by independently authenticated backend adapters;
caller JSON, matching email, display names and raw UID equality are not proof.

## Trusted identity and unique owner

`verify_identity` takes the configured issuer/subject, exact binding document
path/data, normalized trusted provider account creation metadata and hosted user
metadata. It checks SHA256(issuer + newline + subject) binding path and exact
issuer/subject, then resolves the separate hosted `usr_` ID. Provider creation
must be present and no later than binding creation epoch; a newly recreated
provider identity cannot silently inherit an older binding. Hosted-user creation
must be no later than the binding's actual created_at timestamp. Those two binding
timestamps are deliberately distinct because the existing canonical creation
captures epoch before generating user/binding ISO timestamps in its transaction.

The immutable WordIdentity pins issuer, subject, provider creation milliseconds,
hosted user ID and creation timestamp, binding creation epoch and ISO timestamp.
Every field must match fresh trusted evidence on policy use. Email changes do not
change attribution. Multiple identities for one hosted owner deduplicate to that
owner incarnation. Tenant identities fail closed: the current canonical source
uses a project-scoped issuer without a tenant component, so this design does not
pretend multi-tenant support exists.

`verify_owner` requires a complete, bounded query for role=owner (maximum two
matches) and an existing active hosted-user record. Exactly one matching member
path is required. Membership creation evidence must also be present and no earlier
than hosted-user creation, preventing an old membership from attaching to a
recreated hosted account. Editors/collaborators never receive a share of project words.
A missing or multiple-owner result is unknown. This verifier does not itself run
the query or establish query completeness.

No new identity association, account deletion, membership change or permission
change is implemented. Durable exclusion policy storage and executable review
confirmation remain absent. A future writer must pin the verified proof rather
than storing only a raw provider UID or using email joins.

## Candidate aggregate

`aggregate_verified_snapshot` takes at most100 current active project snapshots
and32 pinned exclusions. It emits the Admin candidate schema:

- schema_version1; source cloud_saved_words_v1.
- definition saved_text_unicode_words_v1; eligibility_scope active_cloud_projects;
  policy_scope saved_words_only. Active-only is still an explicit proposed scope.
- exclusion_revision; as_of.
- coverage: start,end,completeness,consistency=non_atomic,eligible_projects,
  covered_projects,unknown_projects,unattributed_projects.
- metrics: all_saved_words,included_saved_words,excluded_saved_words.

A project is counted once by its unique verified current owner incarnation, never
once per membership/provider alias. Its complete internal ledger must match its
canonical project revision and count definition. Any unproven project makes all
three word totals null, even if its exclusion would otherwise hide it. There is
no partial-total claim. Unknown population/policy/context makes all four coverage
denominators null as well. A complete verified empty population is zero.

The project inputs contain internal owner proofs and ledgers transiently; the
output has no project IDs, account IDs, names, content or individual counts.
Current saved block totals only: history, Undo snapshots, checkpoints and activity
are never added. No period-based growth or historical engagement is inferred.

## Policy context and observation freshness

WordContext validates exclusion-revision syntax ONLY. It does not authenticate
policy authority, create an epoch, or prove live freshness. No production policy
reader exists. The independently authoritative policy must be verified around
collection and at cache publication/use. A caller-supplied/minted revision is not
a valid substitute. Before/after checks alone cannot eliminate the final
policy-change-to-cache-publication race; the future shared-store publication must
CAS against the current policy head or provide equivalent atomic fencing.

The chosen design retains existing900s observation freshness with visible as_of,
not live-current claims. There is NO global coverage epoch and no added global
revision write on manuscript saves. Each observed project ledger must match its
observed canonical project revision; mixed revisions/unknown coverage are never
published as real totals. Ordinary changes after observation may be reflected on
a later admitted manual refresh. Policy edits invalidate old policy-bound totals
immediately, without triggering an automatic scan or resetting shared quota.
Keep the existing30attempts/month and900s manual cooldown in any later reader.
No transport, persistent policy store, quota change or activation exists here.
