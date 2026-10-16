#!/usr/bin/env python3
"""Keep the Editor's release identity in sync with its single VERSION file.

    python scripts/sync_editor_release_metadata.py --set-version 10.5.29
    python scripts/sync_editor_release_metadata.py --write
    python scripts/sync_editor_release_metadata.py --check

All frontend cache tags and the current documentation marker are generated
from VERSION. No tests, cloud commands, or network access are involved. The
version-setting operation rolls back *all* changes if validation fails.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")

# Intentionally enumerate the files that *own* current release identity. Old
# milestone documentation and test archaeology are not mutable release inputs.
FIELDS = (
    ("static/index.html", r"(?<=\?v=)[0-9]+\.[0-9]+\.[0-9]+", "entrypoint cache keys", 1),
    ("static/css/reviews/workspace.css", r"(?<=breadmaker_logo\.svg\?v=)[0-9]+\.[0-9]+\.[0-9]+", "logo cache keys", 1),
    ("static/js/core/boot.js", r"(?<=frontend:')[0-9]+\.[0-9]+\.[0-9]+", "bootstrap identity", 1),
    ("static/js/features/account/security.js", r"(?<=identity-platform-client\.js\?v=)[0-9]+\.[0-9]+\.[0-9]+", "security import", 1),
    ("static/js/features/account/identity-platform.js", r"(?<=identity-platform-client\.js\?v=)[0-9]+\.[0-9]+\.[0-9]+", "identity import", 1),
    ("docs/BREADMAKER_REVIEW_IMPLEMENTATION_PLAN.md", r"(?<=^Implemented through \*\*)[0-9]+\.[0-9]+\.[0-9]+(?=\*\*:)", "Review current-release marker", 1),
)


def _version(root: Path) -> str:
    value = (root / "VERSION").read_text(encoding="utf-8").strip()
    if SEMVER.fullmatch(value) is None:
        raise ValueError(f"Invalid Editor VERSION {value!r}; expected X.Y.Z")
    return value


def _replacements(root: Path, value: str) -> dict[Path, str]:
    """Return the complete intended change set; refuse unrecognized layouts."""
    updates = {}
    for relative, pattern, label, expected in FIELDS:
        path = root / relative
        original = path.read_text(encoding="utf-8")
        matches = list(re.finditer(pattern, original, flags=re.MULTILINE))
        # Do not freeze the number of assets; that has repeatedly turned
        # legitimate frontend refactors into unrelated release failures.
        if len(matches) < expected:
            raise ValueError(f"{relative}: missing {label}")
        # Reject malformed/unversioned cache keys instead of ignoring them.
        marker = ("frontend:'" if relative.endswith("boot.js") else
                  "Implemented through **" if relative.startswith("docs/") else "?v=")
        if original.count(marker) != len(matches):
            raise ValueError(f"{relative}: unsupported or unversioned {label}")
        # The Review plan contains a historical milestone marker after the
        # current top marker; do not rewrite historical documentation.
        replace_count = 1 if relative.startswith("docs/") else 0
        updated = re.sub(pattern, lambda _match: value, original, count=replace_count, flags=re.MULTILINE)
        if updated != original:
            updates[path] = updated
    return updates


def validate(root: Path = ROOT) -> str:
    """The ONE authoritative exact-version integrity contract for preflight."""
    value = _version(root)
    stale = _replacements(root, value)
    if stale:
        names = ", ".join(str(p.relative_to(root)) for p in stale)
        raise ValueError(f"Editor {value}: stale release metadata: {names}; run --write")
    return value


def synchronize(root: Path = ROOT, *, new_version: str | None = None) -> str:
    """Atomically synchronize source files, restoring them on any failure."""
    value = _version(root) if new_version is None else new_version
    if SEMVER.fullmatch(value) is None:
        raise ValueError(f"Invalid Editor release version: {value!r}")
    # Precompute before writing, so missing patterns cannot partially bump a
    # release. Snapshot bytes so a failed follow-up validation can roll back.
    replacements = _replacements(root, value)
    version_path = root / "VERSION"
    if new_version is not None:
        replacements[version_path] = value + "\n"
    before = {path: path.read_bytes() for path in replacements}
    try:
        for path, content in replacements.items():
            path.write_text(content, encoding="utf-8")
        validate(root)
    except BaseException:
        for path, data in before.items():
            path.write_bytes(data)
        raise
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--set-version", metavar="X.Y.Z")
    args = parser.parse_args()
    try:
        if args.check:
            value = validate()
            action = "consistent"
        else:
            value = synchronize(new_version=args.set_version)
            action = "synchronized"
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Release identity ERROR: {exc}\n")
    print(f"Breadmaker Editor {value}: release metadata {action} ({len(FIELDS)} owners)")


if __name__ == "__main__":
    main()
