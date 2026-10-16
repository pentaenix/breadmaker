# Import / Export ownership

`api.import_export` is a composition boundary only.

- `docx_parser.py` — DOCX block/image parsing and normalized manuscript projection.
- `uploads.py` — bounded upload streaming and DOCX archive validation.
- `docx_import.py` — import readiness plus replace/new-project/additional-document routes.
- `docx_export.py` — DOCX manuscript export.
- `pdf_export.py` — PDF rendering/export.
- `assets.py` — generic content-addressed asset upload route.

Callers target the canonical owner directly. Do not recreate `api/import_export.py` or re-export implementation helpers from this package.
