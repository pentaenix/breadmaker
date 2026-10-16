# Breadmaker Hosted Firestore Structured-State Adapter — Milestone 3

**Editor release:** 10.3.47  
**Local Schema:** 42, unchanged  
**Status:** candidate pending user verifier

Milestone 3 implements the server-only structured-state persistence boundary frozen by `HOSTED_DATA_MODEL_V1`. It does **not** integrate every Breadmaker API with the hosted repositories yet; that application migration remains Milestone 5. It does **not** deploy a Firestore database or require live Google credentials.

## Boundary

```text
local ./run                         hosted adapter
───────────                         ──────────────
SQLite/filesystem                   Firestore document model
local runtime                       server-only repository package
no Google SDK                       google-cloud-firestore in hosted requirements only
```

Google imports live only in `src/hosted/firestore/google_driver.py`. The ordinary local requirements remain cloud-free.

## Contract-driven registry

`src/hosted/firestore/model_registry.py` is generated from the frozen hosted data-model JSON. Collection/path/authorization/concurrency policy is therefore not reconstructed by inspecting the local persistence engine.

The generated registry is checked into source so the hosted image does not need the documentation tree at runtime.

If multiple logical mappings project onto the same Firestore document (for example avatar pointer fields on the user document), the registry exposes one canonical document owner rather than two independent CRUD surfaces. Overlay fields are updated through that owner repository so two logical concepts cannot fight over one document generation.

## Deterministic release-test driver

`MemoryDocumentStore` implements the same document/transaction primitive without ADC or network access. Release tests can deterministically exercise:

- transaction boundaries;
- create/read/update/delete;
- generation conflicts;
- retry-safe create/update operations;
- project membership authorization;
- identity binding uniqueness;
- missing-vs-forbidden project equivalence.

The user runs the release verifier. The model does **not** run pytest, Playwright, E2E, `./test`, or `./verify-release`.

## Generation CAS

Mutable documents carry an application generation. An update provides the generation it observed. Inside one transaction:

```text
read current
  ↓
current.generation == expected ?
  ├─ no  → HOSTED_GENERATION_CONFLICT
  └─ yes → apply patch
             generation += 1
             commit
```

Two writers using the same generation cannot both commit logically.

## Idempotency

Create/update APIs accept a server-generated operation id. The committed document records the operation plus a canonical payload fingerprint. Replaying the same operation and payload returns the already-committed result; reusing an operation for a different logical mutation fails rather than creating a duplicate entity.

Firestore may retry a transaction callback. Repository callbacks therefore mutate only the supplied transaction object and local values; they do not send email, publish queues, write blobs, or perform other external side effects.

## Project authorization

Project existence is not itself public information.

`HostedProjectRepository.get_for_user()` checks the project and membership together in a transaction. A missing project, missing membership, or tombstoned project produces the same `ProjectUnavailable("PROJECT_NOT_FOUND")` public class.

Owner-only membership mutation checks owner role inside the same transaction that changes membership/project generation.

Review-participant records are **not** treated as Breadmaker user identities. Milestone 3 authorizes ordinary project members only. External reviewer access remains fail-closed until the hosted runtime can present the frozen Milestone-2 server-issued reviewer session bound to `review_id + participant_id + active snapshot`; a participant ID, email, or user-shaped field is never authority.

## Identity

Breadmaker durable users remain `usr_<opaque>` identities. Provider UID binding is stored separately under its SHA-256 document id, while the exact UID is retained in the document to detect an impossible hash mismatch/collision condition. Email is not a key and is not merge authority.

Binding creation first proves the Breadmaker user exists and cannot silently rebind an existing provider UID to another user.

## Google driver

`GoogleFirestoreDocumentStore` is a thin adapter over the Google client. It is intentionally not instantiated by local runtime or release tests. ADC/live-project verification comes later through operator-run integration/deployment gates.

Hosted-only dependency pin for this milestone: `google-cloud-firestore==2.34.0`.

## What remains for later milestones

Milestone 4 adds Cloud Storage for manuscript/revision/assets and pointer-last publication. Milestone 5 migrates hosted APIs off the remaining local persistence debt and composes these repositories into the hosted runtime. Identity Platform and Cloud Run lifecycle work remain later gates.
