# Hosted runtime images

The hosted application and pronunciation helper use pinned build/runtime bases,
an explicit source allowlist, non-root users and mandatory container scans.
Compilers, shell programs and package managers are build-only dependencies.

## SQLite core

`sqlite-source.json` pins SQLite 3.54.1's official source ZIP, both compiled
members and source identity. `scripts/build_hosted_sqlite.py` compiles only the
core shared library with the existing CPython ABI. FTS5 is not enabled; SQLite's
shell and its `.recover` implementation are never compiled or copied. The Python
command-line wrapper is removed as well. Local installations are unchanged.
This dependency remains necessary for hosted portable-package import/export;
hosted canonical state continues to live in Firestore/GCS.

The runtime retains an accurate downstream Debian package record and immutable
source/compile/binary provenance under `/usr/share/breadmaker/sqlite`. The final
image must pass `scripts/assert_hosted_sqlite.py`: actual loaded-library hash and
source ID, absent shell/FTS5, serialization/deserialization, foreign-key rejection,
transaction rollback, integrity check and complete Schema-42 mapping.

The Debian advisories currently have no fixed distribution version. Preserve the
raw scan report, even when its source-package mapping flags this rebuilt core.
Any applicability handling must be restricted to the exact reviewed source,
runtime proof and two identified CVEs; unrelated or changed findings must fail.
Never drop package metadata, accept an old image scan or add a general ignore.
After Debian publishes a suitable fixed package, remove this downstream build
only after the same runtime/portability/security evidence passes.
