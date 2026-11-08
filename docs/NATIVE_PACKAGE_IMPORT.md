# Native project import preflight

Native Schema-42 `.breadmaker` packages are decoded transiently in process memory.
SQLite is not hosted storage, and neither the original database nor its decoded
content is written to a server filesystem. Hosted project and membership authority
is still created only after all staged product records and assets succeed.

`hosted.native_package.prepare_project_package` is a provider-free planning entry
point for both native-v2 and hosted-v3 packages. Its result contains the title,
ordered product records with complete parent IDs, logical annotations, and checked
asset bytes. Native preflight verifies:

- ZIP member/expanded-size limits, original file hashes and optional fingerprint;
- the standalone SQLite image, integrity, foreign keys, schema, project identity,
  source generation and supplied canonical-state hash;
- trusted Schema-42 columns, live relationships, registered hosted paths, and
  context-specific semantic references (including Canvas/Map links);
- frozen Review/Checkpoint source hashes and their separate rebased rendering
  projections, including historical image bytes;
- safe HTML, image format/size checks and complete referenced asset availability.

Missing or conflicting required parents fail during planning, before any product
record or object upload. Import receipts may already exist, but no project or
membership is published. Package IDs and archived participant metadata never grant
hosted access. Source timestamps are lineage evidence, not hosted write authority.

## Standalone SQLite backups marked WAL

SQLite's [`sqlite3_deserialize` documentation](https://www.sqlite.org/c3ref/deserialize.html)
requires journal-format bytes 18 and 19 to be 1, even when a complete backup image
retains the WAL markers 2/2. Native exports can legitimately have those markers
because SQLite's backup API preserves the source journal setting.

The importer hashes the **original** archive bytes first. It permits only the
native standalone database and asset members, so it never ignores a packaged WAL
or journal sidecar. A WAL-marked image must provide a canonical-state hash. Only a
private in-memory copy has those two header bytes changed; the resulting logical
state, source generation and project identity must still match the manifest.
A stale main database whose committed state exists only in an omitted WAL fails
these checks. These hashes are integrity checks, not proof of a trusted author.
The untrusted database remains query-only with trusted-schema behavior disabled.
Before integrity or product queries, its table/index DDL must match the trusted
current schema. Views, virtual tables, generated expressions and altered CHECK
constraints are rejected. A separate SQLite VM/time budget bounds database work.
Legitimate generation triggers remain inert because the connection cannot write.

## Performance and verification scope

Planning performs no Firestore or object-store calls. The resumable transport can
consume the plan in bounded batches; the existing inline import preserves its
unpublished staging and account-erasure checks. Memory-store timings do not predict
Cloud latency. Synthetic tests retain the normal release watchdogs; a full novel
fixture belongs in a separately bounded validation run, not a larger pytest timeout.


Hosted-v3 preflight also rejects provider object/encryption pointers recursively,
requires real hybrid bodies, checks path/payload identity agreement, validates
rich HTML, and proves image-reference closure. The legacy inline v3 route and
resumable transport use the same plan. Public exports omit provider metadata and
participant account bindings; sealed private recovery keeps its separate policy.
Imported participants retain feedback state, but capability/account fields and
unknown participant schemas are rejected before staging.

## Asset names and v3 compatibility

A native package may give identical image bytes multiple safe basenames. Hosted
asset rows preserve those API paths while objects remain content-addressed. V3
exports write each digest blob once. When aliases exist, the optional manifest
`asset_paths` map binds each archive asset name to its preserved API paths; imports
validate the entire map, each digest, extensions, uniqueness and reference closure.
No provider key or generation is accepted in this mapping. Original frozen source
bytes and hashes are not rewritten.

Canonical v3 archives need no alias map and remain compatible with prior importers.
An archive containing noncanonical aliases requires the updated importer: older
versions ignore the optional mapping. Published hosted asset records remain in the
existing storage format and remain readable by the previous runtime.

Frozen Review/Checkpoint rendering projections also validate their row-container
shapes and nested identity fields before staging. Positive native integer join
IDs and existing opaque source IDs are preserved without rewriting; markup,
container-valued IDs and malformed row arrays are rejected. Historical references
need not still exist in the live manuscript. The separate inert `native_source`
evidence remains byte-for-byte portable. Renderer escaping remains defense in depth.

Imported live Review markers, feedback colors and frozen Review/Checkpoint color
fields accept only empty values or six-digit hex colors, matching the local color
contract. CSS declarations and URLs fail preflight; valid frozen values and hashes
are not normalized or rewritten.
