# Existing-project import and recovery

The hosted editor now offers replacement and append for short, text-only documents.
These are deliberately bounded operations, not the resumable novel-sized import path.

## Current guarantees

- The file is parsed before any manuscript changes.
- Text objects are staged, verified, and only then referenced by a single metadata transaction.
- Membership, project generation, idempotent replay, account erasure fences, and the structural Undo journal retain their existing owners.
- Replacement preserves other document tabs and removes the main manuscript's old chapters, blocks, and scene metadata. Planning and review records are not rewritten.
- Append preserves the existing text and inserts tabs in the requested order, including after gaps left by deleted tabs.
- Five-character confirmation is required in the UI when the project already contains writing. Cancellation while preparation is pending cannot reopen a stale confirmation.
- A failed or oversized operation cannot leave part of the new manuscript visible.
- The journal's 100-record / 450,000-byte limits are unchanged. These operations reserve room by limiting product changes to 95 records and 300,000 bytes of journal metadata. A block, chapter, tab, or affected scene counts as a record, so the limit is not a word count.
- Documents with images and larger documents still require **Import as New Project**. The UI explains this limitation rather than disabling all existing-project options.
- Chapter deletion uses one bounded transaction and offers Undo immediately after success. A chapter above the safe journal limit is rejected without deleting its contents.

## Larger imports: proposed follow-on, not implemented

A larger safe replacement cannot be achieved by increasing a Firestore transaction or journal limit. It needs a versioned publication model:

1. Reserve an actor-bound import job within the target project, recording the expected project generation.
2. Stage immutable text, images, chapter metadata, and verified receipts in an unpublished document revision. Ordinary readers cannot resolve this revision.
3. After all bounded batches complete, recheck membership and erasure fences and atomically change the tab's active revision pointer using the original generation CAS.
4. Journal the pointer change. Undo selects the retained previous revision rather than rewriting thousands of records.
5. Make central reads and writes validate the active revision, including cached responses, direct block reads, exports, Reviews, History, and recovery packages.
6. Retain referenced prior revisions until Undo/History retention permits collection. Account/project erasure must enumerate all staged and retained revision objects.

Required tests include interruption at every phase, replica changes, stale writes,
revoked membership, erasure freezes, concurrent edits, immutable-object corruption,
replay after Undo, inaccessible staged content, package round trips, and complete
retention/erasure coverage. This design must be implemented and reviewed as a unit;
partially exposing staged records is not an acceptable interim solution.
