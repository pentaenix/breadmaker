# Project JSON format ownership

- `validation.py` — constants, parsing, normalization, validation, public-ID checks.
- `markup.py` — Breadmaker Project JSON inline markup conversion.
- `layout.py` — deterministic Canvas layout reconstruction.
- `importer.py` — validated document -> active database import and Developmental Version configuration.
- `exporter.py` — active database -> Project JSON export.

Callers import the canonical owner directly. `formats/project_json.py` must not return.
