# Import / Export ownership

`api.import_export` is a composition boundary only.

- `docx_parser.py` — DOCX block/image parsing and normalized manuscript projection.
- `uploads.py` — bounded upload streaming and DOCX archive validation.
- `docx_import.py` — import readiness plus replace/new-project/additional-document routes.
- `docx_export.py` — local DOCX download; shared rendering is in `formats/manuscript_docx.py`.
- `pdf_export.py` — PDF rendering/export.
- `readable_export.py` — local readable ZIP download from one existing portable snapshot.
- `formats/readable_archive.py` — runtime-independent readable files and unchanged portable snapshot assembly.
- `assets.py` — generic content-addressed asset upload route.

Callers target the canonical owner directly. Do not recreate `api/import_export.py` or re-export implementation helpers from this package.

See [Readable exports](../../../docs/READABLE_EXPORTS.md) for the contents and data boundaries.
