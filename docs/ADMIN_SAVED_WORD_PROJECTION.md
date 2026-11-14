# Saved-word projection foundation (not connected)

This source is unwired and default-off. It makes no Firestore, object-store, KMS,
identity, or network calls, and does not establish coverage for any live project.
It is not an Admin producer or a backfill implementation.

`count_saved_text` counts already-authorized saved plaintext using the existing
History/checkpoint Unicode regex `[\w’'-]+`, version `saved_text_unicode_words_v1`.
This differs from the visible editor's whitespace count; even punctuation-only
hyphen runs count under this existing definition. No text or digest is retained.
The function must run in canonical App saves before encryption, never in Admin.

`prepare_complete_snapshot` prepares a bounded candidate for a full verified
canonical manifest. Empty input is zero only when the writer has verified the
entire current manuscript is empty. The function cannot itself verify coverage.
`prepare_replacement` prepares a revision-fenced delta for known complete data.
Unknown coverage cannot become complete after a subset edit. Disabled interfaces
return unknown without consuming the contribution iterable.

## Required integration before activation

- Atomically commit contribution metadata, aggregate and content revision with
  canonical writes; operation replay must return before applying a delta.
- Cover domain create/update/delete, chapter replacements/deletions, native and
  document imports, encrypted live restores, Undo/Redo/rollback and fallback.
- Import/restore candidates stay staged until complete manifests are validated
  and publication commits. A failed operation must not affect the current total.
- Missing contributions, unsupported paths or mismatched revision invalidate
  coverage. Initial zero is allowed only at verified new empty project creation.
- Legacy coverage requires separately approved bounded content processing. A
  resumable scan must verify canonical generation before publishing; edits during
  scans invalidate or restart it. No implicit telemetry decryption is allowed.
- Validate actual removed block identities/contributions inside the canonical
  transaction. These pure arithmetic interfaces cannot prove input provenance.

## Attribution and reporting decisions still outstanding

Proposed scope is current saved manuscript text across all books in ACTIVE Cloud
projects, excluding history/cuts/annotations/title fields and unsaved UI text.
ACTIVE-only versus all retained manuscripts remains a product decision.

Exclusions should initially affect only the new word metric, not silently alter
existing stock, recency or map-line feeds. Membership owner is a hosted usr_ ID,
not Identity Platform UID. Resolve issuer+subject through the authoritative
identity binding before deduplication, and fail closed on mapping/incarnation
ambiguity. Multiple provider identities may represent one hosted owner.

A future aggregate must state count definition, eligibility scope, policy scope,
immutable exclusion revision, observation window and coverage. All/included/
excluded totals remain unknown when coverage/ownership cannot be proven. Admin
receives no manuscript content. Policy changes invalidate the old filtered
snapshot and do not automatically scan or reset the shared refresh allowance.

## Verification

Synthetic tests cover tokenizer examples, disabled lazy inputs, unknown coverage,
create/edit/delete/Undo arithmetic, revision conflicts and invalid/negative data.
These are foundation tests, not proof of canonical transaction integration.
No runtime switch, persistent metadata write, live scan, IAM or credential is
introduced by these files. Any integration requires reviewed synthetic transaction
and recovery tests plus normal exact-head CI before a separate activation decision.
