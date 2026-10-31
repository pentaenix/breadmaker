# Annotations ownership

`api.annotations` is a composition boundary only.

- `routes.py` — HTTP/domain behavior.
- `contracts.py` — request payload models owned by this domain.
- `editing_search.py` / `editing_lexicon.py` — pure English review matching and
  curated vocabulary for the local-only one-shot editing searches. Persistence
  remains in `routes.py`; see `docs/EXPERIMENTAL_EDITING_SEARCHES.md`.
- `editing_vocabulary.py` — expanded original English word families, compounds,
  spelling variants and phrase lists. Positive/negative coverage and whole-list
  executable checks live in `tests/unit/test_editing_vocabulary.py`.
- `marker_colors.py` — pure native equivalent of the frontend recommended marker
  colour algorithm; browser parity and contrast/uniqueness tests cover both.
- `editing_descriptions.py` — original course-informed explanations of why to
  review each category, what to try, and when retaining it can be appropriate.

Callers that need implementation helpers target `routes` explicitly. Do not
recreate `api/annotations.py` or sibling `annotations_contracts.py` facades.

The package loads its native router only when native composition requests it.
Importing the pure matcher must not initialize SQL, a workspace or either runtime.
Hosted owners are `hosted/editing_search.py` (one-shot publication),
`editing_packs.py` (canonical projection/edits/recovery) and `editing_http.py`
(default-off capability, session/CSRF boundary and bounded worker).
