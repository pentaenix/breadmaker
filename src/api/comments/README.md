# Comments ownership

`api.comments` is a composition boundary only.

- `routes.py` — HTTP/domain behavior.
- `contracts.py` — request payload models owned by this domain.

Callers that need implementation helpers target `routes` explicitly. Do not
recreate `api/comments.py` or sibling `comments_contracts.py` facades.

Hosted HTTP handlers delegate to `hosted.notes.HostedNotes`: native-shaped thread payloads, source-authorized anchors, filtered lists and atomic thread/message/anchor mutations. All product writes share one root-generation commit and the existing structural Undo journal. Bounded Firestore transactions and request receipts preserve retries without partial publication. Private-codec mode remains explicitly unsupported by this owner rather than writing plaintext.

Previously saved raw `body/items` threads are projected without rewriting or dropping their original text. The original body remains editable through its exact thread identity; existing nested replies remain intact.
