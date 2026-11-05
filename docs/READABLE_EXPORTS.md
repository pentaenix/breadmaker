# Manuscript and readable project exports

Project menu → Export → Readable ZIP creates ordinary files that can be extracted
and opened outside Breadmaker. It does not replace the existing `.breadmaker`
portable project or Project JSON formats.

The archive contains:

- `START-HERE.html` and `README.txt` for opening and understanding the files.
- Numbered Word-compatible `.docx`, `.html` and UTF-8 `.txt` copies of every document tab.
- `assets/` with original project image files, referenced relatively by the HTML copies.
- `data/project-content.json`, a readable reference for current manuscript structure,
  markers, Canvas, Maps, Tasks and links. This is explicitly **not** an import format.
- `Project.breadmaker`, the unchanged portable project snapshot used for restoration.

The browser saves pending writing before export and shows a disabled “Creating ZIP…”
button while the download is prepared. Failed requests can be retried. Completing an
older export does not close a newer dialog. The UI uses the existing Project submenu,
PDF dialog body, modal lifecycle and download helper; no new CSS or panel language.

## Data and runtime boundaries

The readable files are all derived from one portable snapshot. Local exports reuse the
existing frozen canonical-generation package exporter, then render only that snapshot.
Hosted exports reuse the existing membership-authorized, generation-fenced hosted-to-local
codec, including its experimental-encryption refusal and unsupported-state checks. A
single per-instance worker limits hosted archive assembly; the existing heavy-request
rate limit covers the new route. No local Workspace is created by hosted export.

The ZIP never fetches remote images. Asset paths must be canonical; archive paths are
fixed or use safe, numbered filenames. HTML titles/attributes are escaped and manuscript
inline formatting uses the canonical sanitizer. Credentials, installations and account
membership authority remain excluded by the existing portable-project policy. Temporary
snapshot/archive files are cleaned after download or failure.

`formats/manuscript_docx.py` now owns the shared Word renderer used by local export,
hosted export and ZIP copies. Semantic headings, bold, italics, underline, tabs, line
breaks, supported images, image rows, widths, captions and descriptions are retained.
Clean exports omit editor-only markers and ghost scene headings. Fonts, themes, exact
screen pagination and Editor's Eye are not exported. Unsupported image formats such as
WebP use an explicit placeholder in DOCX; original images remain in the ZIP and HTML.
PDF remains a separate existing command.

The native Schema 42 `annotations.thread_id` column is a logical link without a SQL
foreign key. Hosted portability now translates that reference through `comment_threads`
so projects containing notes can export and preserve their message links. Missing targets
still fail closed.

## Verified baseline on 5 November 2026

The signed-in live UI produced a real Word-compatible DOCX (37,454 bytes) containing the
synthetic QA manuscript and one embedded image, and a PDF (3,258 bytes, valid PDF header).
The live native-project command failed with
`HOSTED_PORTABLE_INTEGER_REFERENCE_INVALID:annotations.thread_id`; the correction above
has a synthetic hosted export/import round-trip regression.

Current local manuscript import supports DOCX and text; legacy DOC depends on an installed
LibreOffice converter. Hosted manuscript import supports DOCX and text and rejects legacy
DOC. Neither path imports PDF. Native packages already use a ZIP container internally;
local “all data” ZIP is installation backup rather than readable manuscript export.

A separate existing hosted PDF limitation was identified in `hosted.document_io` /
`hosted.http_compat`: the PDF endpoint accepts the dialog options but renders its stable
server defaults. This change does not claim those options are honored.

## Validation

Focused API and unit coverage verifies format structure, Word formatting, images, every
document tab, native restore, hosted round trips, note-thread relationships, membership
and encrypted-mode refusal, safe filenames, HTML sanitization, path traversal rejection,
frozen content during concurrent editing and transient-file cleanup. Browser regressions
exercise actual local/hosted downloads, pending-save flushing, responsive dialog geometry,
keyboard focus, busy/error feedback and stale-modal completion.

Full Editor and Cloud release gates and authenticated live QA remain required before
production promotion. The release coordinator owns version allocation and deployment;
this feature does not change production independently of that release.
