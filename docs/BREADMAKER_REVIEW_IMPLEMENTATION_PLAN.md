# Breadmaker Review — Full Implementation Plan

> **Design and implementation record.** The original 8.x planning sections remain as history. For current user behavior, see [Using Breadmaker](USING_BREADMAKER.md).

**Program goal:** Build Review as a complete local-first Breadmaker feature, then add portable review packages and Manuscript integration without requiring accounts, servers, or subscriptions.

**Current implementation baseline:** Breadmaker 8.20.0  
**Primary surface:** Review tab  
**Secondary surface:** Manuscript Review working layer  
**Remote hosting:** Deferred until the local + portable model is proven  
**Removed concept:** Review Rooms

Implemented through **10.5.52**: the local Review domain now includes Scope, Permissions, Participants, reusable Questionnaires, immutable Review Snapshots, Reviewer Mode, Highlights/Comments, Suggestions, Feedback forms, Review processing/Tasks, lifecycle and visibility controls, portable `.breadreview` round trips, temporary drag-and-drop Reviewer Mode, optional authenticated passphrase encryption with hostile-package validation, Review-template Populate, and the Manuscript Review working layer. Categories were intentionally retired in 8.16.3; Populate creates editable Reviews directly. Remote transport remains future work.

---

# 1. Product Definition

A **Review** is a first-class object inside a Breadmaker project.

A Review defines, once:

- **Name**
- **Scope**
- **Permissions**
- **Participants**
- **Optional feedback questions**
- **Visibility policy**
- **Lifecycle/status**

All participants in the same Review share the same scope and permissions.

If one person needs different permissions, create a different Review.

Example:

```text
Review: Beta Reading
Scope: Chapters 1–3

Permissions
✓ Read
✓ Comment + Highlight
✕ Suggest
✓ Feedback

Participants
Rebeca
Ana
Miguel
```

Carlos needs Suggest permission:

```text
Review: Beta Reading — Carlos
Scope: Chapters 1–3

Permissions
✓ Read
✓ Comment + Highlight
✓ Suggest
✓ Feedback

Participants
Carlos
```

Breadmaker should make this cheap through **Duplicate Review**.

---

# 2. Core Invariants

These should be treated as architectural rules.

## 2.1 Review-level policy

Scope and permissions belong to the Review, not the participant.

```text
Review
├── Scope
├── Permissions
├── Questions
├── Visibility policy
└── Participants
```

Participants contribute attributed feedback but do not override Review policy.

## 2.2 Attribution

Every Review artifact stores its participant identifier.

```text
Comment
Highlight
Suggestion
Feedback answer
General feedback
```

always knows who created it.

## 2.3 Reviewer isolation by default

Default:

```text
Rebeca cannot see Ana's feedback.
Ana cannot see Rebeca's feedback.
Writer sees both.
```

A Review may explicitly enable:

```text
Allow reviewers to see other reviewers' feedback
```

This is a Review-level option.

## 2.4 Reviewer activity never directly edits the manuscript

Comments, highlights, suggestions and feedback are Review data.

A Suggestion is a proposal.

Only the writer can apply it to the canonical manuscript.

## 2.5 Review transport is not Review semantics

The same Review can eventually be accessed through:

- the writer's own machine;
- a full `.breadmaker` project copy;
- a portable review package;
- self-hosted remote access;
- Breadmaker-hosted remote access.

These are access/transport mechanisms.

There is no user-facing **Review Room** object.

---

# 3. Permissions

Review permissions are capabilities, not a progressive role ladder.

```text
Permissions

□ Read
□ Comment + Highlight
□ Suggest
□ Feedback
```

## 3.1 Read

Allows the participant to read the Review's manuscript scope.

This is useful by itself.

Example:

```text
Rebeca
Scope: Chapter 2
Permission: Read
```

The writer can hand Rebeca the laptop at a café, let her read the chapter, then discuss it verbally.

No writing inside Breadmaker is necessary.

## 3.2 Comment + Highlight

Allows:

- text highlights;
- anchored comments;
- optional tags;
- replies if enabled later.

This capability requires access to the relevant manuscript text.

## 3.3 Suggest

Allows proposals such as:

- insertion;
- deletion;
- replacement/rewrite.

Suggestions never mutate the writer manuscript directly.

Suggest does not automatically imply Feedback.

Whether Suggest also exposes Comment + Highlight should be implemented as explicit capability behavior rather than hidden role inheritance. The UI may automatically enable Read when a text-based capability is selected because the feature cannot operate without visible text.

## 3.4 Feedback

Feedback is deliberately separate from inline commenting.

It provides:

- optional creator-defined open questions;
- one always-available **General feedback** response.

Feedback does not automatically enable:

- Comment + Highlight;
- Suggest.

Example:

```text
Permissions
✓ Read
✕ Comment + Highlight
✕ Suggest
✓ Feedback
```

This is a valid beta-reading workflow.

---

# 4. Feedback Forms

Breadmaker should not become a general survey builder.

For the first complete implementation, feedback questions are **open text only**.

Example:

```text
Do you think they will find the killer?
[                                      ]

Could you predict the next beat? (optional)
[                                      ]

General feedback
[                                      ]
[                                      ]
```

Rules:

- Questions are optional for the Review creator.
- Each question may be required or optional.
- General feedback is always present when Feedback permission is enabled.
- No numeric scales initially.
- No star ratings initially.
- No yes/no questions initially.
- No multiple choice initially.
- No branching form logic.

If users need survey research, dedicated form tools remain better suited to it.

---

# 5. Review Templates and Populate

Categories are intentionally not part of the Review model. They added a metadata field and management surface without changing Review behavior.

**Populate…** is the reusable starting-point system instead. It creates real Reviews directly, in the order a manuscript would commonly move through them:

```text
Alpha Reading
Developmental Review
Sensitivity / Authenticity Review
Technical / Subject Matter Review
Beta Reading
Line Review
Proofreading
```

Each populated Review is ordinary editable Review data with:

- a useful default description;
- full-manuscript scope;
- recommended permissions;
- private reviewer-note visibility by default;
- no participants.

After creation, the writer may rename it, change scope or permissions, attach Questionnaires, add Participants, or delete it. The preset creates no special workflow state and does not remain attached as metadata.

Populate is therefore a convenience for creating a sensible Review sequence, not a taxonomy or filter system.

---

# 6. Scope

Scope is configured once per Review.

Supported scope:

- Entire manuscript
- Selected Chapters
- Selected Scenes

Future scope may include combinations if needed, but the initial model should use existing stable Chapter/Scene identities.

Examples:

```text
Scope
Chapters 1–3
```

or:

```text
Scope
Chapter 4
  Scene 2
  Scene 5
```

A participant may never access manuscript material outside the Review scope through normal Review APIs/UI.

---

# 7. Review Source and Snapshot Policy

A Review should have a stable source version.

When a Review is opened for participation, Breadmaker should preserve the reviewed manuscript state so feedback never moves underneath participants.

Recommended model:

```text
Create Review
    ↓
Review draft
    ↓
Start Review
    ↓
Freeze Review Snapshot
```

This allows the writer to configure a Review before committing to a frozen source.

The snapshot stores only the Review scope, not an entire duplicate project.

Example:

```text
Review snapshot
source project generation: 481
scope: Chapters 1–3
content hash: ...
```

The writer may continue to generation 527.

The Review still represents generation 481.

This is necessary for:

- reliable comment anchors;
- portable review packages;
- returned feedback;
- multiple participants reviewing at different times;
- future remote publishing.

If the writer wants updated prose reviewed, they explicitly create a new snapshot/review rather than silently changing material under existing feedback.

---

# 8. Review Tab — Primary UX

The Review tab is the first and authoritative UI for the feature.

## 8.1 Empty state

```text
Review

Gather feedback on your manuscript.

[ Create Review ]
```

## 8.2 Main list

```text
Review

Filters
Participant  All
Permission   All
Status       Active

Beta Reading
Chapters 1–3
Rebeca · Ana · Miguel
Comment + Highlight · Feedback
12 comments · 3 feedback responses
Open

Beta Reading — Carlos
Chapters 1–3
Carlos
Suggest · Feedback
4 suggestions
Open

+ New Review
```

## 8.3 Review detail

```text
Beta Reading

Scope: Chapters 1–3

Permissions
Read
Comment + Highlight
Feedback

Participants
Rebeca       Completed
Ana          In progress
Miguel       Not started

Feedback
12 comments
18 highlights
3 responses

[ Review manuscript ]
[ Add participant ]
[ Duplicate Review ]
[ Export Review ]
[ Finish Review ]
```

---

# 9. Review Creation Flow

Recommended steps:

```text
Create Review
    ↓
Name
    ↓
Scope
    ↓
Permissions
    ↓
Feedback questions
    ↓
Participants
    ↓
Visibility
    ↓
Create
```

Keep this compact.

The user should be able to go backward and edit any choice before starting the Review.

When a Review is still a draft, its source is not frozen.

`Start Review` creates/finalizes the Review Snapshot.

---

# 10. Participant Identity

Participants are simple project-level Review identities.

Minimum:

```text
participant_id
display_name
```

Optional later:

```text
email
notes
```

Do not require an account.

Examples:

```text
Rebeca
Carlos
Mom
Editor
Writing Group — Anna
```

Participant identity is used to attribute:

- highlights;
- comments;
- suggestions;
- question answers;
- general feedback.

The same named person may participate in multiple Reviews.

---

# 11. Reviewer Mode

Breadmaker needs a constrained Review-only mode.

From Review:

```text
Open as reviewer
    ↓
Rebeca
```

Reviewer Mode displays:

- Review name;
- participant identity;
- permitted manuscript scope;
- reading controls;
- allowed Review tools;
- feedback form if enabled;
- Finish Review.

Reading controls should follow Breadmaker's existing view controls where appropriate:

- font;
- size;
- comfortable manuscript reading layout.

Reviewer Mode does not expose:

- Canvas;
- Maps;
- Tasks;
- Cuts;
- unrelated manuscript scope;
- backup configuration;
- project settings;
- Review management;
- writer editing commands.

This is application-level protection.

It is not a claim that a person holding the entire raw `.breadmaker` file cannot extract private bytes.

---

# 12. Read-Only Review

Read-only is a real, useful workflow.

Example:

```text
Review: Chapter 7 Read
Scope: Chapter 7

Permissions
✓ Read
✕ Comment + Highlight
✕ Suggest
✕ Feedback

Participant
Rebeca
```

Reviewer Mode becomes essentially a polished reading view.

Use case:

```text
Writer at café
    ↓
Open Review as Rebeca
    ↓
Hand over laptop
    ↓
Rebeca reads
    ↓
Exit Review
    ↓
Discuss verbally
```

No Review output is required.

Completion may simply record that Rebeca completed the Review.

---

# 13. Inline Review Artifacts

## 13.1 Highlight

Stores:

- participant;
- Review;
- Snapshot;
- anchor;
- optional tag;
- created time.

## 13.2 Comment

Stores:

- participant;
- Review;
- anchor;
- body;
- status;
- optional tag.

## 13.3 Suggestion

Stores:

- participant;
- Review;
- anchor;
- operation;
- original context;
- proposed text;
- status.

Operations:

```text
insert
delete
replace
```

Reviewer suggestions never become manuscript text automatically.

---

# 14. Stable Anchoring

Review annotations must not depend solely on transient DOM offsets.

Conceptual anchor:

```text
snapshot_id
chapter_id
scene_id
block_id
selected_text
start_offset
end_offset
context_before
context_after
context_hash
```

Writer-side resolution:

```text
exact identity + matching text
    → exact anchor

identity exists, content moved/changed
    → conservative re-anchor

insufficient confidence
    → DETACHED
```

Breadmaker must never silently guess.

Detached feedback remains readable with its original Snapshot context.

---

# 15. Writer Feedback Processing

The Review tab is initially the main processing surface.

Filters:

```text
Review
Participant
Permission/type
Feedback type
Status
```

Feedback type:

```text
Highlights
Comments
Suggestions
Feedback responses
```

Example item:

```text
Carlos
Suggestion

Chapter 4 · Scene 2

"This motivation changes too abruptly."

Suggested rewrite:
...

[ Open in manuscript ]
[ Accept ]
[ Reject ]
[ Create task ]
```

---

# 16. Existing Link System Integration

Review should use Breadmaker's existing link/navigation concepts rather than inventing parallel navigation.

Inline Review artifacts link to their manuscript location.

From Review:

```text
Open in manuscript
```

jumps to:

```text
Chapter
Scene
anchored passage
```

If the live manuscript no longer contains a safe equivalent:

```text
Original passage no longer maps cleanly
```

and Breadmaker shows the Snapshot context rather than guessing.

---

# 17. Review → Task Integration

Any actionable Review artifact should eventually support:

```text
Create task
```

Example:

Carlos suggests a rewrite.

The writer agrees there is a problem but dislikes Carlos's proposed replacement.

They create:

```text
Task:
Rewrite scene and change her motivation
```

Breadmaker automatically links the Task to:

1. the Review artifact;
2. the relevant manuscript location.

Conceptually:

```text
Task
├── review_item_link
└── manuscript_link
```

Opening the Task can answer:

```text
Why does this task exist?
→ original Review feedback

Where does it apply?
→ manuscript location
```

This should reuse Breadmaker's existing linked-item mechanisms wherever possible.

---

# 18. Suggestion Outcomes

Do not force writer decisions into only Accept/Reject.

A Review artifact may conceptually become:

```text
Open
Accepted
Rejected
Task created
Resolved
```

`Task created` does not necessarily mean the suggestion was accepted.

The writer may preserve the insight while rejecting the proposed wording.

Applying an accepted suggestion must use the normal manuscript mutation path and create a normal new project generation.

---

# 19. Multiple Concurrent Reviews

Multiple Reviews may exist at once.

Example:

```text
Beta Reading
Rebeca · Ana · Miguel

Beta Reading — Carlos
Carlos

Proofread — Chapters 1–5
Editor

Agent Notes
Agent
```

They may overlap in scope.

They remain independent Review contracts.

The Review tab must make filtering and identification easy.

---

# 20. Duplicate Review

Because permissions are Review-level, duplication is an important convenience.

```text
Beta Reading
    ↓
Duplicate
    ↓
Beta Reading — Carlos
```

Copy:

- Scope
- Permissions
- Questions
- Visibility

Do not copy:

- Participants by default
- Existing feedback
- Completion state

The creation UI opens immediately so the writer can modify permissions and participants.

---

# 21. Full `.breadmaker` Sharing

A writer may share the entire `.breadmaker` file.

This remains supported.

Use case:

- trusted editor;
- trusted co-worker;
- another personal machine.

The recipient can open Breadmaker and enter the appropriate Review.

However, this is intentionally **not the recommended privacy-preserving transfer mechanism**.

The project contains substantially more data than the Review.

Breadmaker should eventually explain:

```text
This file contains the complete Breadmaker project.
Reviewer Mode limits normal application access but
does not remove private project data from the file.
```

---

# 22. Returned Full-Project Copies

A returned reviewer project must never replace newer writer state simply because it contains new feedback.

Example:

```text
Reviewer copy
based on generation 481

Writer project
currently generation 527
```

When returned:

```text
Validate project identity
    ↓
Discover Review feedback/events
    ↓
Import Review-domain changes only
    ↓
Keep generation-527 manuscript canonical
```

No "last file wins."

Review event IDs must make repeated imports idempotent.

---

# 23. Portable BreadReview Package

A dedicated portable Review format is part of the complete target.

Working product concept:

```text
BreadReview
```

A file might be called:

```text
rebeca.breadreview
```

## 23.1 Important extension decision

`.br` is already commonly associated with **Brotli-compressed data** on the web.

Breadmaker can technically register and recognize `.br`, but this creates avoidable ambiguity for operating systems, browsers, servers and MIME handling.

Recommended release decision:

```text
Canonical extension: .breadreview
Product name: BreadReview / BR
```

If the product strongly prefers `.br`, Breadmaker should use an internal magic header and explicit file association, but this should be an intentional tradeoff rather than an accidental collision.

---

# 24. BreadReview Package Contents

A BreadReview package contains:

```text
Package identity
Project identity
Review identity
Snapshot identity

Review
├── name
├── scope
├── permissions
├── visibility policy
└── questions

Participant
├── participant_id
└── display_name

Allowed manuscript snapshot

Feedback
├── highlights
├── comments
├── suggestions
├── question answers
└── general feedback
```

It does not contain:

```text
Canvas
Maps
Tasks
Cuts
other manuscript content
other Reviews
backup configuration
backup history
revision history
project settings
writer-only notes
```

---

# 25. BreadReview Open Behavior

Double-clicking:

```text
rebeca.breadreview
```

opens Breadmaker directly into Review Mode.

No normal project interface is available because the package does not contain a full project.

Flow:

```text
Open file
    ↓
Validate package
    ↓
Optional passphrase
    ↓
Review: Beta Reading
Reviewer: Rebeca
    ↓
Review Mode
```

The package itself is updated with the participant's Review feedback.

The participant returns the same package to the writer.

---

# 26. BreadReview Import

Writer:

```text
Review
    ↓
Import Review...
    ↓
rebeca.breadreview
```

Breadmaker verifies:

```text
package integrity
project identity
review identity
snapshot identity
participant identity
event identities
```

Then merges Review data only.

Importing the same package twice:

```text
first import
→ new feedback imported

second import
→ same immutable event IDs
→ zero duplicates
```

A package for the wrong project or Review fails closed.

---

# 27. BreadReview Passphrase Protection

Portable Review packages may optionally be protected by passphrase.

If Breadmaker calls this protection or encryption, the content must actually be encrypted.

Do not implement:

```text
password check
+
plaintext package contents
```

Recommended model:

```text
passphrase
    ↓
memory-hard key derivation
    ↓
authenticated encryption
    ↓
encrypted Review payload
```

Store only the cryptographic parameters needed for key derivation/decryption.

Use established cryptographic libraries and formats rather than designing a custom cipher.

Wrong passphrase must reveal no Review content.

---

# 28. BreadReview Integrity

Even without a passphrase, portable packages should include integrity/versioning information.

Conceptually:

```text
format_version
package_id
project_id
review_id
snapshot_id
participant_id
payload hash
```

Malformed or partially corrupted packages fail safely.

Unknown future format versions should produce an explicit unsupported-version error rather than partial import.

---

# 29. Review Events and Merge Model

Portable/full-project feedback imports should use immutable event identities.

Conceptually:

```text
review_event_id
review_id
participant_id
event_type
payload
created_at
```

Examples:

```text
HIGHLIGHT_CREATED
COMMENT_CREATED
COMMENT_RESOLVED
SUGGESTION_CREATED
QUESTION_ANSWERED
GENERAL_FEEDBACK_UPDATED
PARTICIPANT_COMPLETED
```

Where mutable state exists, define explicit state transitions rather than relying on file timestamps.

Do not use "latest file wins."

---

# 30. Manuscript Review Integration — Implemented in 8.20.0

The Review tab/domain remains authoritative. Manuscript integration is a working layer over those same Review rounds and artifacts; it does not duplicate Review storage or administration.

The **Reviews** toolbar button in Manuscript opens that working layer.

Its purpose is:

```text
View Review feedback in manuscript context
```

not:

```text
Create an entirely separate Review system
```

---

# 31. Manuscript Review Button

Recommended toolbar concept:

```text
[ ... ] [ Review ]
```

or a compact Review/comment glyph consistent with Breadmaker's toolbar language.

States could communicate:

```text
No relevant Review feedback
Review feedback exists
Unresolved feedback exists
```

Avoid noisy permanent badges everywhere unless testing proves they help.

Clicking the icon opens a compact Review panel/filter rather than navigating away immediately.

---

# 32. Manuscript Review Panel

Implemented structure:

```text
LIST
Open | All | Closed
Developmental Review · Round 2 · Open
Beta Reading · Round 1 · Closed
        ↓ select
DETAIL
Developmental Review · Round 2
Everyone | Rebeca | Carlos
Feedback 4 / 19   ↑ ↓
[ feedback in manuscript order ]
```

The manuscript displays overlays only for the selected round and selected reviewer filter. Clicking a panel item navigates to its manuscript anchor; clicking a manuscript Review marker selects the same item in the panel. Questionnaire answers and General Feedback live in a focused modal with the existing Create Task workflow. Round Close/Reopen delegates to the canonical Review lifecycle. Full administration remains in Reviews via **Open in Reviews**.

Inline feedback can be resolved/reopened or converted to Tasks from the compact item menu. Suggestions continue to use the canonical Review suggestion state; acceptance/rejection remains part of the Review processing model rather than duplicated state.

The underlying Review data remains owned by the Review domain.

---

# 33. Review Visibility Inside Manuscript

Because multiple Reviews may overlap, overlays need strong filtering.

Default behavior:

```text
Review overlays Off
```

until the user opens the Manuscript Reviews panel and selects a round. Then only that round (and the current reviewer filter) becomes visible.

This prevents the manuscript from becoming permanently covered in feedback from every historical Review.

Finished/archived Reviews should not automatically clutter the text.

---

# 34. Review ↔ Manuscript Navigation

Navigation must work in both directions.

```text
Review item
    ↓
Open in manuscript
```

and:

```text
Manuscript annotation
    ↓
Open Review item
```

Both use the same stable Review anchor/link identity.

No duplicated annotation records.

---

# 35. Remote Access — Later Infrastructure Layer

After local Review, BreadReview packages and Manuscript integration are stable:

```text
Review
    ↓
Remote access...
```

Possible providers:

```text
Self-hosted
Breadmaker Cloud
```

Remote access publishes the existing Review.

It does not create a Review Room.

---

# 36. Self-Hosted Review

The user may eventually configure a compatible Review Service.

Concept:

```text
Review Service URL
credentials
```

Then:

```text
Publish Review
```

uploads only:

- Review metadata;
- Review Snapshot;
- participant access state;
- feedback data required by the service.

No unrelated project content is uploaded.

---

# 37. Breadmaker-Hosted Review

Breadmaker Cloud implements the same protocol.

Cloud requirements come later:

- accounts;
- identity;
- authorization;
- subscription;
- secure links;
- email delivery;
- storage;
- audit logs;
- rate limiting;
- monitoring;
- deletion;
- retention.

The local Review model must not depend on those systems.

---

# 38. Proposed Implementation Program

## 7.10.0 — Review Domain

Implement:

- Review persistence;
- participant identity;
- Review status;
- shared Review-level scope/permissions;
- visibility policy;
- Review tab shell;
- create/list/open/delete Review.

Acceptance:

- survives restart;
- existing projects unaffected;
- multiple concurrent Reviews supported.

---

## 7.10.1 — Review Creation + Snapshot

Implement:

- creation flow;
- scope selection;
- permissions;
- participants;
- optional feedback questions;
- Start Review;
- immutable scoped Review Snapshot;
- Duplicate Review.

Acceptance:

- writer may continue editing;
- started Review content remains frozen;
- duplicate creates clean Review without feedback.

---

### Implemented shell progress

- **8.1.x** — Review identity, persistence, collection/workspace shell, recovery-safe settings.
- **8.2.0** — Categories.
- **8.3.0** — Scope.
- **8.4.x** — Review-level permissions.
- **8.5.0** — Participants.
- **8.5.1** — Review workspace overview: Category, Scope, Permissions, and Participants move into the selected Review viewport while the rail returns to navigation/filtering. No schema change.
- **8.6.0** — Start Review + immutable structured Review Snapshot; Draft → Open; Scope and Permissions freeze at start while Name, Category, and Participants may still be managed.

## 7.10.2 — Reviewer Mode + Read

Implement:

- open as participant;
- Review-only UI;
- font/size reading controls;
- scope enforcement;
- Read permission;
- completion state;
- reviewer isolation foundation.

Acceptance:

- café/laptop read-only workflow works fully offline;
- reviewer cannot navigate into normal project features through supported UI/actions.

---

## 7.10.3 — Highlights + Comments

Implement:

- stable selections;
- highlights;
- comments;
- attribution;
- tags if desired;
- reviewer privacy;
- writer Review-item list;
- link back to manuscript.

Acceptance:

- two participants can independently annotate the same Review;
- neither sees the other's work by default;
- writer sees both.

---

## 7.10.4 — Suggestions

Implement:

- insert;
- delete;
- replace;
- proposal rendering;
- Accept;
- Reject;
- manuscript-generation integration;
- idempotent application.

Acceptance:

- reviewers cannot mutate canonical manuscript;
- writer Accept creates a normal new generation;
- repeated Accept cannot apply the same Suggestion twice.

---

## 7.10.5 — Feedback

Implement:

- Feedback permission;
- open-text questions;
- required/optional questions;
- always-present General feedback;
- participant responses;
- writer response view;
- filtering.

Acceptance:

- Feedback works independently from Comment and Suggest.

---

## 7.10.6 — Review Processing + Tasks

Implement:

- filters:
  - Review;
  - participant;
  - permission;
  - feedback type;
  - status;
- Review → manuscript navigation;
- detached anchor handling;
- Create Task from Review artifact;
- automatic Task links to Review item + manuscript location.

Acceptance:

- writer can turn feedback into editorial work without losing its origin/context.

---

## 7.10.7 — Full `.breadmaker` Review Exchange

Implement:

- Review event IDs;
- feedback-only merge from returned project copies;
- project identity validation;
- Review identity validation;
- duplicate import protection;
- divergent generation handling.

Acceptance:

```text
writer sends generation 100
writer continues to 140
reviewer returns generation-100 copy with feedback
feedback imports
generation-140 manuscript remains canonical
```

---

## 7.10.8 — Review Hardening

Test:

- multiple concurrent Reviews;
- overlapping scope;
- deleted/changed manuscript content;
- detached anchors;
- duplicate feedback;
- corrupted Review state;
- wrong project;
- reviewer privacy leakage;
- permission bypass attempts;
- crash during suggestion Accept;
- crash during Task creation;
- backup/recovery with active Reviews.

Acceptance:

- Review domain is reliable enough to become the source for portable packages.

---

## 7.11.0 — BreadReview Package Export

Implement:

- portable Review package schema;
- selected participant export;
- only allowed Snapshot content;
- versioning;
- integrity;
- open directly in Reviewer Mode.

Decide final extension before shipping file association:

- recommended `.breadreview`;
- `.br` only if the Brotli collision is explicitly accepted.

Acceptance:

- recipient receives no unrelated project content.

---

## 7.11.1 — BreadReview Return + Merge

Implement:

- feedback saved into same portable package;
- return/import workflow;
- participant attribution;
- event merge;
- duplicate import idempotency;
- conflict handling.

Acceptance:

- complete offline send → review → return → merge workflow.

---

## 7.11.2 — BreadReview Passphrase Encryption

Implement:

- optional passphrase;
- established memory-hard KDF;
- authenticated encryption;
- encrypted payload;
- safe wrong-password behavior;
- corruption detection.

Acceptance:

- protected package contents cannot be read without the passphrase through ordinary package inspection.

---

## 7.11.3 — BreadReview Hardening

Test:

- wrong password;
- corrupted ciphertext;
- truncated file;
- modified header;
- duplicate return;
- wrong project;
- wrong Review;
- stale Snapshot;
- maliciously modified feedback;
- unsupported future version;
- very large Review;
- non-ASCII participant/content;
- cross-platform paths/file association.

Acceptance:

- portable Review is suitable for real-world offline exchange.

---

## 7.12.0 — Manuscript Review Button

Implement:

- Review toolbar/icon;
- active feedback indicator;
- compact Review filter panel;
- toggle overlays;
- Review selection;
- participant selection.

Acceptance:

- Manuscript remains uncluttered when Review overlays are off.

---

## 7.12.1 — Manuscript Review Overlays

Implement:

- highlight overlays;
- comments;
- suggestions;
- click/open behavior;
- current Review filters;
- detached-state indicators.

Acceptance:

- Review tab and Manuscript show the same underlying Review artifacts.

---

## 7.12.2 — Manuscript Review Actions

Implement:

- resolve;
- Accept/Reject;
- Create Task;
- Open full Review;
- two-way navigation.

Acceptance:

- writer can perform the common Review-processing workflow without leaving Manuscript.

---

## 7.12.3 — Manuscript Integration Hardening

Test:

- many overlapping Reviews;
- hidden/archived Reviews;
- large manuscript;
- thousands of annotations;
- deleted content;
- moved scenes;
- accepted suggestions;
- Tasks;
- localization;
- keyboard navigation;
- themes;
- Editor's Eye interaction;
- split view.

Acceptance:

- Review overlays do not destabilize or noticeably degrade the normal manuscript editor.

---

# 39. Infrastructure Handoff Gate

Only after the following is true should hosted Review become the primary objective:

```text
Local Review ✓
Read-only review ✓
Comments/highlights ✓
Suggestions ✓
Feedback forms ✓
Review-template Populate ✓
Multiple participants ✓
Reviewer privacy ✓
Review → Task ✓
Full project exchange ✓
Portable BreadReview ✓
Passphrase protection ✓
Manuscript integration ✓
```

At that point the infrastructure objective is narrow:

```text
Implement remote transport for the Review model we already trust.
```

Not:

```text
Invent Review while simultaneously inventing cloud architecture.
```

---

# 40. Final End-to-End Acceptance Scenario

The Review program is locally complete when this works:

```text
Writer creates project
    ↓
Uses Populate or creates Review:
  Beta Reading
    ↓
Configures Review:
  Beta Reading
  Chapters 1–3
  Read + Comment/Highlight + Feedback
    ↓
Adds Rebeca and Ana
    ↓
Starts Review
    ↓
Snapshot frozen
    ↓
Rebeca reviews on writer's laptop
    ↓
Ana receives a portable BreadReview package
    ↓
Both leave independent feedback
    ↓
Neither can see the other's feedback
    ↓
Ana returns package
    ↓
Writer imports it
    ↓
Writer filters Review by Rebeca / Ana
    ↓
Writer jumps from comment to manuscript
    ↓
Carlos needs Suggest permission
    ↓
Writer duplicates Review
    ↓
"Beta Reading — Carlos"
    ↓
Enables Suggest
    ↓
Carlos returns a rewrite suggestion
    ↓
Writer dislikes exact rewrite
    ↓
Creates Task:
"Rewrite and change her motivation"
    ↓
Task automatically links to:
  Carlos's suggestion
  manuscript passage
    ↓
Writer opens Manuscript Review overlay
    ↓
Processes remaining feedback in text context
    ↓
Finishes Review
    ↓
Restarts Breadmaker
    ↓
All Review history, attribution and links remain intact
```

That is the target.

---

# Implementation Progress

Implemented through **8.16.6**:

- 8.1.x — persistent Review entities and workspace shell
- 8.2.0 — Categories
- 8.3.0 — Review-level Scope
- 8.4.0 — Review-level Permissions (Read, Comment + Highlight, Suggest, Feedback)
- 8.5.0 — persistent named Participants per Review; shared Review policy is inherited without participant-level overrides
- 8.5.1 — selected Review workspace overview for Category, Scope, Permissions, and Participants
- 8.6.0 — Start Review freezes the scoped manuscript into an immutable structured snapshot and opens the Review
- 8.7.0 — participant-scoped Reviewer Mode renders the frozen Snapshot as a restricted read-only surface with independent font/size controls
- 8.8.0 — schema 29 reserves the complete known local Review storage foundation (lifecycle, visibility, Category permission defaults, multi-block anchors, Highlights, Comments/replies, Suggestions, Feedback questions/answers, General Feedback, Review events, and portable import receipts) so the remaining planned local pipeline should not require repeated schema resets
- 8.9.0 — Reviewer Mode Highlights + Comments over frozen Snapshot anchors; participant-attributed feedback is private by default and surfaced to the writer in the Review workspace
- 8.10.0 — Reviewer Suggestions (insert/delete/replace), writer Accept/Reject, exact unchanged-source automatic application, and fail-closed handling for changed or multi-block manuscript targets
- 8.11.0 — independent Review Feedback forms with Draft-only open-text question configuration, required/optional questions, always-present General Feedback, private participant answers, writer-attributed responses, and Feedback-only Reviewer Mode without manuscript disclosure

- 8.12.0 — Review/participant lifecycle, private/shared inline reviewer visibility, Review-side processing filters, Resolve/Reopen, and Task links to Review feedback. Manuscript Review UI remains deferred.
- 8.13.0 — portable `.breadreview` export/open/return/import. Packages contain only the participant-scoped frozen Review plus permitted assets, open directly into portable Reviewer Mode without activating a project, save feedback back into the same file, and merge returned Review-domain changes idempotently after project/Review/snapshot/participant/anchor verification.
- 8.13.1 — whole-window `.breadreview` drag-and-drop. Dropped packages open as temporary isolated Reviewer Mode sessions layered over the active project; Exit restores the exact prior workspace without importing. Changed packages are written back through a browser file handle when available, with download fallback. Explicit import remains separate.
- 8.14.0 — BreadReview package format v2 and hostile-input hardening. Optional passphrase protection encrypts and authenticates the complete inner Review package with AES-256-GCM; scrypt derives the key; only minimal format/crypto metadata remains outside the ciphertext. Direct-open, drag/drop, rewrite, returned-file download, and writer import share the same in-memory unlock path. v1 packages are intentionally unsupported in pre-alpha. Validation rejects tampering, truncation, unsafe/duplicate archive paths, symlinks, compression bombs, oversized/deep JSON, invalid asset inventories, future formats, permission escalation, malformed events, and stale mutable feedback updates. Project schema remains 29.
- 8.15.0 — Review UI consolidation. Category filtering moves to an icon menu, New Review becomes a direct `+` action, and Scope/Permissions/Participants/Questions collapse into one-click icon controls with Breadmaker tooltips. The Review body becomes feedback-first; Review selects/actions use Breadmaker styling; the Find & Replace tooltip ampersand is corrected; all seven locales and all three themes stay in parity. No schema change.
- 8.15.1 — Manuscript-first Review UI correction. The temporary rail Category/`+` controls and in-body icon strip are removed. New Review returns to the collection `…` menu; Categories becomes a standard toolbar tool opening a Checkpoints/Markers-style searchable right panel with New Category, background Populate, optional descriptions, and delayed description flyers. Scope/Permissions/Participants/Questions and the one-click reviewer shortcut live in the real Review toolbar using the shared Manuscript tooltip path. Optional Category descriptions advance the pre-alpha project schema to 30; schema 29 is intentionally not migrated.
- 8.15.2 — Review UI contract correction. Categories/Participants/Questions/State are the only distinct toolbar tools and all use coordinated Manuscript-style right panels with desktop push and narrow overlay behavior. Scope/Permissions stay solely in Details; lifecycle actions move solely to State; Participants owns the plain-language inline-note visibility control; Review descriptions become canonical data with standard delayed flyers; Category creation is available directly beside the Details category picker. Review descriptions advance the pre-alpha schema to 31; schema 30 is intentionally not migrated.
- 8.16.0 — Reusable Review rounds and Review UX freedom pass. Reviews remain editable reusable configuration while each round freezes an immutable checkpoint of scope, permissions, manuscript content, and attached Questionnaire prompts. Reviews support pause/resume, multiple rounds, duplicate, participant reuse, project-level reusable Questionnaires, Review-cycle Populate presets, round/history navigation, frozen-round viewing, live manuscript links from feedback, and Tasks that retain both Review-feedback and manuscript-range references when available. Reviewer Mode is pageless/Focus-style and Review panels continue to follow Manuscript/Canvas component contracts. Schema advances intentionally to 32; schema 31 is not migrated.
- 8.16.1 — Review UI consistency correction. Maps immediately reapplies its theme colors on global theme changes; Review Category uses the established Marker-style select/create pattern; Category and Participant panel actions use fixed primary + `…` controls with Populate available there and on panel-background right-click; standard Category Populate gains Sensitivity / Authenticity and Technical / Subject Matter; the Participants visibility checkbox alignment is normalized; Questionnaire icon/editor spacing is corrected; new Questionnaires start with zero questions and only their question list scrolls; Rounds & History uses a forward-history icon. Schema remains 32.
- 8.16.2 — Reviewer reading-annotation parity and global toolbar polish. Reviewer Mode reuses Manuscript margin markers/hover flyers for comments and highlights, adds one participant bookmark per frozen round (portable through Review events), hardens selection-toolbar positioning for first-character selections, repairs the Review-cycle Populate modal, and gives Quick Open / Undo / Redo / Theme the standard Breadmaker tooltip treatment. Seven locales remain synchronized. Schema remains 32.
- 8.16.3 — Categories retired from the active architecture. Populate creates ordered editable Review templates directly; Review rail items become quiet blocks; toolbar reduces to Participants / Questionnaires / Review Activity; notification-like counts are removed; reviewer-note visibility returns to Details; the Participants action becomes a connected split button; seven locales are updated. Pre-alpha schema advances to 33 with no migration.
- 8.16.5 — Corrective UI startup repair: restores the Participants panel controller removed during Category retirement, allowing startup localization and the Review toolbar to initialize normally. Schema remains 33.
- 8.16.6 — Review planning UI: descriptions leave the Review rail (remaining available on hover/Details), Reviews gain persisted manuscript-style ordering, and Populate becomes four repeatable workflow presets with explicit contents and automatic numbering for repeated Review types. Copy Edit becomes a first-class preset Review type. Schema advances to 34.
- 8.16.7 — Review UI consistency: Review rows reuse Manuscript chapter-row geometry, redundant Populate helper copy is removed, Review Details groups note visibility inside Permissions, and feedback filters use Breadmaker menus instead of native browser selects. No schema change.
- 8.20.0 — Manuscript Review working layer. Open / All / Closed round list, per-round reviewer filtering, synchronized manuscript highlights/gutter markers and feedback navigation, canonical Close/Reopen, Questionnaire/General Feedback modal, Create Task reuse, strict exact-text fail-closed anchor painting, and Open in Reviews administration handoff. Schema remains 41.
- 8.15.3 — Repository source-layout cleanup. Application Python modules and their internal packages move under `src/`; runtime assets remain in named top-level resource directories. Launchers, tests, subprocesses, and release verification explicitly resolve `src/`. No Review behavior or project schema changes.

Permissions are shared by the entire Review. Comment + Highlight and Suggest operationally require Read; Feedback remains independent. Participants are named identities only at this stage: they inherit the Review's Scope and Permissions and do not carry overrides.

---

# 41. Product Model Summary

The final mental model should stay small:

```text
REVIEW
├── Scope
├── Permissions
│   ├── Read
│   ├── Comment + Highlight
│   ├── Suggest
│   └── Feedback
├── Questions
├── Participants
├── Visibility
├── Snapshot
└── Feedback
```

Access methods:

```text
Same machine
Full .breadmaker file
BreadReview portable package
Self-hosted remote access
Breadmaker-hosted remote access
```

No Review Rooms.

One Review domain.

Multiple transports.

Local first.
