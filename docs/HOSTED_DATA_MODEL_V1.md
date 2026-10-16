# Breadmaker Hosted Data Model v1

**Milestone:** 2 — specification only  
**Editor release:** 10.3.47  
**Local source schema:** 42  
**Live GCP resources:** none  

This document is the canonical human-readable design for the serverless hosted rewrite. The machine-readable authority is `docs/HOSTED_DATA_MODEL_V1.json`; the exact source snapshot of Schema 42 is `docs/HOSTED_SCHEMA42_SOURCE_INVENTORY.json`; the source-controlled Firestore index plan is `docs/HOSTED_FIRESTORE_INDEXES_V1.json`.

Milestone 2 deliberately implements **no Firestore/GCS CRUD and creates no cloud resource**. It freezes the data shape before Milestones 3–5 write adapters.

## 1. Non-negotiable boundaries

- Local Breadmaker remains accountless SQLite + filesystem, Schema 42. This design does not migrate or replace local storage.
- Hosted Breadmaker is a separate persistence implementation behind the Milestone-1 runtime ports. Do not scatter `if cloud` branches through product logic.
- Hosted browser code never reads or writes Firestore/GCS directly. Cloud Run is the product data plane and enforces Breadmaker authorization server-side.
- Project IDs remain immutable public identifiers, but knowledge of a project ID never grants access.
- Email is never a durable identity or authorization key.
- No Firestore document is an unbounded manuscript, review snapshot, checkpoint, asset, export, or backup container.
- Immutable object publication is manifest/pointer-last: an orphan object is acceptable; a pointer to a missing object is not.
- Infrastructure/configuration will later be IaC-owned. This milestone is only the data contract.

## 2. Target storage responsibilities

| Layer | Owns | Does not own |
| --- | --- | --- |
| Firestore | users, projects, membership, metadata, current structured state, pointers, generations, operation records | large manuscript blobs, immutable snapshots, secrets |
| Cloud Storage | immutable revisions/snapshots, assets, oversized block payloads, imports/exports, backup objects | authorization, mutable membership, secrets |
| Identity Platform target | authentication subject and MFA/provider state after Milestone-6 feasibility gate | Breadmaker project authorization |
| Cloud Run | authorization, validation, transactions, object publication, app API | durable instance-local state |

## 3. Identity and project identity

### Breadmaker user ID

Hosted Breadmaker keeps a provider-independent internal user ID (`usr_<opaque>`). Identity Platform UID is bound server-side to this ID through `identity_bindings/{uid_hash}`. This is intentional even though Identity Platform itself has a stable UID: it keeps project ownership independent from a specific identity provider and preserves explicit linking/migration options.

Email is display/contact metadata only. It is never a document key, membership key, ownership key, or link target.

### Project ID

A hosted project uses the same semantic identity as local `projects.project_uid`: 32 lowercase hex, immutable, public, and **not a capability**. A direct `projects/{project_id}` request authorizes `projects/{project_id}/members/{user_id}` before exposing project existence/data.

Importing a `.breadmaker` package into hosted Breadmaker creates a fresh hosted project ID. The package project ID may be kept as audit/source metadata only; it cannot overwrite or claim an existing hosted project.

## 4. Canonical generation / concurrency model

`projects/{project_id}.generation` is the hosted successor to local `project_generation`. Each committed canonical mutation advances it exactly once. Mutable aggregate roots also carry an entity generation/revision so narrow writes can reject stale clients without relying on timestamps.

Normal bounded writes use Firestore transactions. A Chapter save checks the Chapter revision/generation and project generation before publishing the new current state.

### Large operations

A restore/import/restructure may exceed one Firestore transaction. Such an operation must use a durable operation record:

1. authorize and create an immutable pre-operation revision;
2. create a durable operation ID and lock the project mutation authority to it;
3. apply bounded, idempotent batches;
4. verify the complete target state;
5. atomically publish/finalize the new project generation;
6. only then expose the new state as committed;
7. a crashed operation must resume or restore the pre-operation revision; it may never leave partial state presented as canonical.

## 5. Manuscript current state

Current manuscript structure is queryable Firestore state: tabs, Chapters, Scenes, blocks, annotations, Markers and references. The unit of prose storage is a block, not an entire book/Chapter document.

A block Firestore document has a soft serialized limit of 128 KiB for inline prose. Larger block payloads move transparently to an immutable GCS object at `projects/{project_id}/block-payloads/{block_id}/{content_hash}.json.gz`; Firestore retains the block identity/order/hash/pointer. The project-wide Firestore soft document guard is 512 KiB, comfortably below the platform hard limit.

Heavy prose/context fields are unindexed unless a real query requires them. The index plan is source-controlled instead of accepting console-created indexes as architecture.

## 6. Immutable revisions and publication order

Every durable revision has a Firestore metadata record plus an immutable GCS revision object. Required logical envelope fields include format/version, project ID, revision ID, project generation, parent revision ID, content/state hashes, internal author user ID, authenticated provider UID, timestamp, scope and manifest. The Firestore publication metadata records the object name, GCS object generation, hash and publication time.

Publication order is fixed:

1. write the immutable GCS object with `ifGenerationMatch=0`;
2. verify returned object generation and content hash;
3. Firestore transaction checks the expected current generation and publishes the current-state/revision pointer;
4. only then report save success.

If step 3 fails, the blob is an unreferenced orphan. That is safe and may be collected later. The reverse state—a Firestore pointer to a missing/unverified blob—is forbidden.

## 7. Checkpoints and Review snapshots

Large frozen structures intentionally stop being relational in hosted storage.

- A Checkpoint has small Firestore metadata and one immutable GCS snapshot object. Checkpoint image bytes are project-scoped content-addressed GCS objects; refs remain explicit metadata.
- A Review Snapshot has small Firestore metadata and one immutable GCS snapshot envelope. Local `review_snapshot_chapters`, `review_snapshot_scenes`, `review_snapshot_blocks`, `review_snapshot_questions`, `review_snapshot_markers`, and `review_snapshot_checkpoints` become named sections inside that object.
- Review anchors and reviewer feedback remain Firestore because they are mutable/queryable collaboration state against an immutable snapshot.

This avoids six large snapshot collections and prevents frozen manuscript bodies from inflating Firestore reads/indexes.

## 8. Delete, trash, recovery and garbage collection

Project deletion is a tombstone, not an immediate purge. `status=deleted`, deletion metadata and retention deadline make the project inaccessible to normal APIs while preserving an owner-authorized recovery path. Physical Firestore/GCS deletion is later, idempotent GC.

Child current-state deletions can be physical inside a committed generation because immutable project history remains the recovery source until retention expires. Immutable snapshot/revision/assets are never rewritten in place and are deleted only when reachability + retention permit it.

## 9. Project membership and collaborator state

Membership authority is `projects/{project_id}/members/{user_id}`. Exactly one owner is required. Editors are explicit members. The member document is also a safe place for collaborator-specific ordering metadata such as `last_opened_at`, but manuscript authorization must use role/permissions, not convenience metadata.

Local `bookmarks` and `ui_state` are installation/project-scoped. Hosted versions are intentionally per-member so collaborators do not overwrite each other’s navigation/preferences.

## 10. Review participant identity

`review_participants` remains a review-local identity and is not automatically a Breadmaker account. Participant access later requires a server-issued reviewer session bound to review + participant + active snapshot. Knowing a participant ID is never authority. Writer/member access continues through project membership.

## 11. GCS object naming

Object paths are server-generated from validated opaque IDs and hashes only. User-provided filenames, emails, project titles, URLs and path fragments never become authority-bearing GCS segments.

Examples:

```text
projects/<project_id>/assets/sha256/<hash>.<ext>
projects/<project_id>/revisions/<generation20>/<revision_id>.revision.json.gz
projects/<project_id>/checkpoints/<checkpoint_id>/<snapshot_hash>.checkpoint.json.gz
projects/<project_id>/reviews/<review_id>/snapshots/<snapshot_id>/<content_hash>.snapshot.json.gz
imports/<user_id>/<upload_id>/<server_filename>
```

All immutable creates use GCS generation preconditions. A create-only write uses generation match `0`; mutation/delete of an existing object must use the previously observed generation when applicable.

## 12. Schema-42 mapping matrix

Every Schema-42 table is mapped exactly once below. `hybrid` means small/queryable metadata in Firestore plus large/immutable payload in GCS. `gcs` means no current Firestore row is required for that source table’s payload representation.

| Local table | Hosted entity | Store | Hosted path / object | Key difference |
| --- | --- | --- | --- | --- |
| `projects` | project | firestore | `projects/{project_id}` | Local integer projects.id disappears; hosted document id is local project_uid semantics. generation is the authoritative global logical generation. |
| `storage_meta` | hosted_model_metadata | firestore | `projects/{project_id}/system/model` | Does not reproduce SQLite storage_meta. Records hosted model/version/features only; local Schema 42 remains local. |
| `document_tabs` | document_tab | firestore | `projects/{project_id}/tabs/{tab_id}` |  |
| `chapters` | chapter | firestore | `projects/{project_id}/chapters/{chapter_id}` | Local chapters.content_html/content_hash are not authoritative in hosted storage; chapter current content is block-based. |
| `scenes` | scene | firestore | `projects/{project_id}/scenes/{scene_id}` |  |
| `manuscript_blocks` | manuscript_block | hybrid | `projects/{project_id}/blocks/{block_id} + GCS projects/{project_id}/block-payloads/{block_id}/{content_hash}.json.gz` | Normal paragraph/image metadata may be inline. If serialized prose fields exceed the inline guard, content_html/text_content move to immutable GCS payload and Firestore keeps hash/pointer. No Firestore document may approach the 1 MiB platform limit. |
| `markup_labels` | markup_label | firestore | `projects/{project_id}/markup_labels/{label_id}` |  |
| `icon_markers` | icon_marker | firestore | `projects/{project_id}/icon_markers/{icon_marker_id}` |  |
| `annotations` | annotation | firestore | `projects/{project_id}/annotations/{annotation_id}` |  |
| `bookmarks` | member_bookmark | firestore | `projects/{project_id}/members/{user_id}/state/bookmark` | Local table is project singleton because local has one active profile. Hosted bookmark is collaborator-scoped. |
| `ui_state` | member_ui_state | firestore | `projects/{project_id}/members/{user_id}/ui_state/{key}` | Hosted UI state is collaborator-scoped and may be dropped without manuscript loss. |
| `comment_threads` | comment_thread | firestore | `projects/{project_id}/comment_threads/{thread_id}` |  |
| `comment_messages` | comment_message | firestore | `projects/{project_id}/comment_threads/{thread_id}/messages/{message_id}` |  |
| `references_map` | cross_feature_reference | firestore | `projects/{project_id}/references/{reference_id}` |  |
| `cuts` | cut | hybrid | `projects/{project_id}/cuts/{cut_id} + GCS projects/{project_id}/cuts/{cut_id}/{content_hash}.json.gz` | Portable cut HTML/body is object data; Firestore stores title/source metadata/hash/pointer only. |
| `checkpoints` | checkpoint | hybrid | `projects/{project_id}/checkpoints/{checkpoint_id} + GCS projects/{project_id}/checkpoints/{checkpoint_id}/{snapshot_hash}.checkpoint.json.gz` | Local snapshot_blob moves to immutable GCS. Firestore checkpoint metadata stores object generation/hash and restore scope. |
| `checkpoint_assets` | checkpoint_asset_blob | gcs | `projects/{project_id}/checkpoint-assets/sha256/{asset_hash}` | Project-scoped content-addressed object; no cross-project dedupe authority. |
| `checkpoint_asset_refs` | checkpoint_asset_reference | firestore | `projects/{project_id}/checkpoints/{checkpoint_id}/asset_refs/{asset_ref_id}` |  |
| `canvases` | canvas | firestore | `projects/{project_id}/canvases/{canvas_id}` |  |
| `canvas_nodes` | canvas_node | firestore | `projects/{project_id}/canvases/{canvas_id}/nodes/{node_id}` |  |
| `canvas_edges` | canvas_edge | firestore | `projects/{project_id}/canvases/{canvas_id}/edges/{edge_id}` |  |
| `canvas_versions` | canvas_version | firestore | `projects/{project_id}/canvases/{canvas_id}/versions/{version_id}` |  |
| `canvas_version_items` | canvas_version_item | firestore | `projects/{project_id}/canvases/{canvas_id}/versions/{version_id}/items/{item_id}` |  |
| `story_maps` | story_map | firestore | `projects/{project_id}/story_maps/{map_id}` |  |
| `map_lines` | map_line | firestore | `projects/{project_id}/story_maps/{map_id}/lines/{line_id}` |  |
| `map_items` | map_item | firestore | `projects/{project_id}/story_maps/{map_id}/lines/{line_id}/items/{item_id}` |  |
| `map_junctions` | map_junction | firestore | `projects/{project_id}/story_maps/{map_id}/junctions/{junction_id}` |  |
| `tasks` | task | firestore | `projects/{project_id}/tasks/{task_id}` |  |
| `task_milestones` | task_milestone | firestore | `projects/{project_id}/tasks/{task_id}/milestones/{milestone_id}` |  |
| `review_marker_labels` | review_marker_label | firestore | `projects/{project_id}/review_marker_labels/{marker_label_id}` |  |
| `questionnaires` | questionnaire | firestore | `projects/{project_id}/questionnaires/{questionnaire_id}` |  |
| `questionnaire_questions` | questionnaire_question | firestore | `projects/{project_id}/questionnaires/{questionnaire_id}/questions/{question_id}` |  |
| `reviews` | review | firestore | `projects/{project_id}/reviews/{review_id}` |  |
| `review_participants` | review_participant | firestore | `projects/{project_id}/reviews/{review_id}/participants/{participant_id}` | participant_id is a review-local identity, not a Breadmaker user_id or email. |
| `review_scope_chapters` | review_scope_chapter | firestore | `projects/{project_id}/reviews/{review_id}/scope_chapters/{chapter_id}` |  |
| `review_scope_scenes` | review_scope_scene | firestore | `projects/{project_id}/reviews/{review_id}/scope_scenes/{scene_id}` |  |
| `review_markers` | review_marker_assignment | firestore | `projects/{project_id}/reviews/{review_id}/markers/{review_marker_id}` |  |
| `review_questionnaires` | review_questionnaire_assignment | firestore | `projects/{project_id}/reviews/{review_id}/questionnaires/{questionnaire_id}` |  |
| `review_snapshots` | review_snapshot | hybrid | `projects/{project_id}/reviews/{review_id}/snapshots/{snapshot_id} + GCS projects/{project_id}/reviews/{review_id}/snapshots/{snapshot_id}/{content_hash}.snapshot.json.gz` | Snapshot manuscript/configuration payload is one immutable object. The six local snapshot projection tables below become named sections of that object. |
| `review_snapshot_checkpoints` | review_snapshot_checkpoint_refs | gcs | `projects/{project_id}/reviews/{review_id}/snapshots/{snapshot_id}/{content_hash}.snapshot.json.gz` | Physical representation intentionally differs: local relational rows become a section inside the immutable hosted review snapshot envelope. |
| `review_snapshot_markers` | review_snapshot_markers | gcs | `projects/{project_id}/reviews/{review_id}/snapshots/{snapshot_id}/{content_hash}.snapshot.json.gz` | Physical representation intentionally differs: local relational rows become a section inside the immutable hosted review snapshot envelope. |
| `review_snapshot_chapters` | review_snapshot_chapters | gcs | `projects/{project_id}/reviews/{review_id}/snapshots/{snapshot_id}/{content_hash}.snapshot.json.gz` | Physical representation intentionally differs: local relational rows become a section inside the immutable hosted review snapshot envelope. |
| `review_snapshot_scenes` | review_snapshot_scenes | gcs | `projects/{project_id}/reviews/{review_id}/snapshots/{snapshot_id}/{content_hash}.snapshot.json.gz` | Physical representation intentionally differs: local relational rows become a section inside the immutable hosted review snapshot envelope. |
| `review_snapshot_blocks` | review_snapshot_blocks | gcs | `projects/{project_id}/reviews/{review_id}/snapshots/{snapshot_id}/{content_hash}.snapshot.json.gz` | Physical representation intentionally differs: local relational rows become a section inside the immutable hosted review snapshot envelope. |
| `review_snapshot_questions` | review_snapshot_questions | gcs | `projects/{project_id}/reviews/{review_id}/snapshots/{snapshot_id}/{content_hash}.snapshot.json.gz` | Physical representation intentionally differs: local relational rows become a section inside the immutable hosted review snapshot envelope. |
| `review_anchors` | review_anchor | firestore | `projects/{project_id}/reviews/{review_id}/snapshots/{snapshot_id}/anchors/{anchor_id}` | References immutable snapshot block IDs; never points at mutable current manuscript blocks. |
| `review_highlights` | review_highlight | firestore | `projects/{project_id}/reviews/{review_id}/highlights/{highlight_id}` |  |
| `review_comments` | review_comment | firestore | `projects/{project_id}/reviews/{review_id}/comments/{comment_id}` |  |
| `review_comment_replies` | review_comment_reply | firestore | `projects/{project_id}/reviews/{review_id}/comments/{comment_id}/replies/{reply_id}` |  |
| `review_suggestions` | review_suggestion | firestore | `projects/{project_id}/reviews/{review_id}/suggestions/{suggestion_id}` |  |
| `review_question_answers` | review_question_answer | firestore | `projects/{project_id}/reviews/{review_id}/answers/{answer_id}` |  |
| `review_general_feedback` | review_general_feedback | firestore | `projects/{project_id}/reviews/{review_id}/general_feedback/{feedback_id}` |  |
| `review_events` | review_event | firestore | `projects/{project_id}/reviews/{review_id}/events/{event_id}` |  |
| `review_imports` | review_import_receipt | firestore | `projects/{project_id}/reviews/{review_id}/imports/{import_id}` |  |

## 13. Non-SQL durable/runtime-state mapping

| Current local state | Hosted representation | Notes |
| --- | --- | --- |
| `workspace/profiles/index.json` | `users/{user_id}` | No active_profile_id in hosted global state. Request identity chooses the current user; last_project_id may remain a user preference. |
| `Identity provider subject binding (replaces local profile activation)` | `identity_bindings/{uid_hash}` | Separate Breadmaker user_id is locked in Milestone 2; this permits provider migration/linking without making email or provider UID the product authorization key. |
| `workspace/profiles/{usr}/avatar.webp` | `users/{user_id}` | User document stores current avatar hash/object generation/revision. Old versions are retention-GC candidates. |
| `workspace/projects/{project}/local.json` | `projects/{project_id}/members/{user_id}` | Exactly one owner role is required. member_id remains an opaque audit identity; user_id is the document key. |
| `workspace/registry.json + project manifest.json` | `projects/{project_id} + membership collection-group query` | Hosted has no machine-global active project registry. Per-user last-opened state lives on membership/user metadata. Portable manifests are generated views, not authority. |
| `projects/{project}/assets/*` | `projects/{project_id}/assets/{asset_id}` | Asset object name is server-generated from validated bytes/hash. Firestore metadata preserves logical API asset identity and reference lifecycle; no user path becomes a GCS key. |
| `revision_history/v1/*` | `projects/{project_id}/revisions/{revision_id}` | Hosted revision format is a new cloud format, not a byte-for-byte copy of local revision store v1. It preserves generation lineage, state hash, author identity, parent revision, object hash/generation and manifest-last publication semantics. |
| `projects/{project}/backups/recovery_state.json` | `projects/{project_id}/system/recovery` | Scheduling/retention state is reconstructible from published immutable backup/revision manifests. It is not manuscript authority. |
| `project manifest backup_destinations + credential files` | `projects/{project_id}/backup_destinations/{destination_id}` | Non-secret destination metadata may be Firestore. OAuth refresh tokens/passwords/API secrets must live outside Firestore/GCS project data (Secret Manager or provider-managed credential storage). Third-party backup authorization remains independent of Breadmaker login. |
| `projects/{project}/exports/*` | `projects/{project_id}/exports/{export_id}/{server_filename}` | Temporary/derived with lifecycle TTL. Never the only copy of manuscript state. |
| `uploaded .breadmaker/.docx/.breadreview/.breadcuts packages` | `imports/{user_id}/{upload_id}/{server_filename}` | Short TTL. Validation happens before canonical publication. Import creates a new hosted project_id and cannot overwrite an existing project by package ID. |
| `workspace trash directory` | `projects/{project_id}.status=deleted` | No directory move in hosted storage. Trash is authoritative project tombstone + retention deadline; normal membership reads fail closed while owner recovery endpoint can restore. |
| `workspace logs + project logs` | `Cloud Logging` | Never project authority. OAuth codes/tokens/secrets/manuscript bodies must remain excluded by logging policy. |
| `session undo journal / process-local UI editing state` | `not durable server authority` | Cloud Run restart may discard this state only if committed project state remains correct. Durable undo/history is revision-backed, not instance-memory-backed. |

## 14. Firestore index ownership

`docs/HOSTED_FIRESTORE_INDEXES_V1.json` is the Milestone-2 source of truth. It currently defines 15 composite indexes and 18 field-index exemptions. Later IaC must reproduce this plan; creating an index only in the console is drift.

The first planned compound queries cover project membership listing, Chapters/blocks ordering, canvases, reviews, tasks, references, participant/event/feedback views, revisions and backup-destination status. Large body fields are explicitly unindexed.

## 15. Backup/export/import semantics

- Imports upload to short-lived GCS staging objects, validate fully, and then create a new project identity. Staging objects are not authority.
- Exports are derived GCS objects with lifecycle TTL and may be regenerated.
- Hosted project backup/recovery is defined by immutable revision/checkpoint/backup manifests, not by VM snapshots or Cloud Run filesystem state.
- Third-party backup destination metadata may be structured state, but OAuth refresh tokens/passwords/secrets stay outside project Firestore/GCS data.

## 16. Source-controlled provider constraints used by this design

The design was checked against current Google documentation on 2026-10-05:

- Firestore document hard limit: 1 MiB; transaction/API limits mean the application keeps much lower soft limits and has a durable bulk-operation protocol. https://cloud.google.com/firestore/quotas
- Cloud Storage generation-match preconditions, including `ifGenerationMatch=0` create-only publication. https://cloud.google.com/storage/docs/request-preconditions
- Firestore composite indexes and index exemptions can be source-controlled/deployed from JSON/IaC rather than created only in the console. https://firebase.google.com/docs/reference/firestore/indexes

These provider constraints are guards, not product semantics. If provider limits change, future implementation may tighten limits without weakening the invariants above.

## 17. Milestone-2 exit criteria

- `HOSTED_SCHEMA42_SOURCE_INVENTORY.json` exactly describes all 54 current Schema-42 tables.
- `HOSTED_DATA_MODEL_V1.json` maps those 54 tables exactly once.
- Every mapping states storage/path, ID, ownership, authorization, size, concurrency, deletion and recovery policy.
- Non-SQL profile/membership/avatar/assets/history/recovery/import/export/trash state is explicitly mapped.
- No email-as-identity mapping exists.
- No unbounded Firestore manuscript/blob container exists.
- Every GCS mutation class has an explicit precondition/idempotency rule.
- Index plan is source-controlled.
- No Firestore/GCS SDK is added to local requirements and no live cloud resources are created.
- User runs the complete Editor + Cloud verifier; the model does not run it.

Once the user reports this milestone green, Milestone 3 may implement the Firestore structured-state adapter exactly behind this contract. Collection names/paths must not be invented ad hoc outside this specification.
