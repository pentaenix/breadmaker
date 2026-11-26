# Optional hosted activity

Collection requires the separately activated `BREADMAKER_ACTIVITY_ENABLED`,
an exact producer-bundle fingerprint and UTC collection start, Identity Platform
hosted mode, an active account and its current explicit `hosted-usage-v1` grant.
Local/developer mode never starts the producer. No client event types, actor IDs,
timestamps, counter amounts, text, filenames or keystrokes are accepted.

`journal.py` observes actual canonical before/after images inside the existing
transaction. Statistics and canonical publication succeed together; failed saves,
idempotent replay and abandoned object uploads produce no event. Numeric records
are outside structural Undo. Restore updates observed task states without counting
Undo/Redo as new creations. Manuscript counts represent commit transactions with
paragraph/text payload-pointer publication, including initial saved drafts; they
are not word growth, elapsed writing time or a count of paragraphs.

The foreground sender runs only with saved permission, enabled collection, a
visible document and browser focus. The server deduplicates UTC account-minute
buckets across tabs/devices. Foreground accounts mean accepted pulses within 90
seconds, not proof of attention or active typing. Changing consent stops new
collection; a new grant revision never revives a previous grant's records.

Task states come from normal authorized edits that already need the plaintext,
or a numeric state binding to randomized ciphertext previously observed by that
account. Analytics never decrypts, scans or backfills task/manuscript content.
Unknown ciphertext restores remain unknown. The task panel covers latest observed
states in the retained window, not the full current inventory of untouched tasks.
Closed/deleted accounts and current exclusion-policy entries are removed by the
reader. Full legacy task-stock initialization remains separately reviewed work.

Records expire after 28 days, leaving cleanup headroom within the notice's 30-day
maximum. Firestore TTL policies own asynchronous physical cleanup; expired data
is excluded immediately even while cleanup is pending. Account-day data contains
bounded numeric counts, a minute set and hashed task-state observations. Separate
ciphertext-version receipts contain only enum/timestamp bindings. Neither is a
content/ownership authority or a permission to erase customer project data.

The reader caps the population at 200 profiles, 6,000 numeric account-day rows,
5,000 task observations and a fixed deadline. Overflow, corrupt records, drift or
an unavailable provider returns unknown, never a partial zero. Reads stream rather
than loading the whole ledger. Only settled days older than yesterday can be
reused for up to an hour, bound to the exact current consent/exclusion cohort;
today/yesterday, account grants and erasure locks are always checked again.
The response contains aggregate numbers only, with UTC coverage and timestamps.
There is no reconstruction of pre-collection history, and 7/30/90-day UI selections
show only their available intersection with the retained collection window.
