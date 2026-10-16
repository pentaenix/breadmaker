from __future__ import annotations

"""M12K: closed-world completeness guard for experimental encrypted recovery.

No HTTP routing or local runtime import.  The source owner's authorization must
be established before this scanner reads any Firestore collection. Scans use only
project-prefixed, registered collection paths; NEVER a global collection-group
query. Missing or unsupported records fail closed before backup publication.

Firestore does not allow recursive enumeration of arbitrary unknown subcollections
or children of nonexistent ancestor documents through this minimal provider API.
Consequently this proves catalog/export agreement for REGISTERED reachable model
paths, not exhaustive physical cloud inventory. M13 still needs independent GCP
inventory and source-loss recovery evidence before activation.
"""

from string import Formatter
from typing import Any, Mapping

from hosted.firestore.contracts import HostedValidationError
from hosted.firestore.ids import require_project_id, require_user_id
from hosted.firestore.model_registry import MODELS
from hosted.firestore.repositories import render_model_path
from hosted.security.private_firestore import HostedPrivateFirestoreFields, PRIVATE_FIELD_NAME
from hosted.security.envelope import FORMAT

# Operational authority is recreated at restore and must never be treated as
# transferable user content. Every other excluded model is a hard blocker if
# nonempty (e.g. member UI state, retained revision history, checkpoint assets).
_RECREATED_AUTHORITY = frozenset({
    'project_membership', 'hosted_model_metadata', 'recovery_state',
})
_MAX_CATALOG_ROWS = 12000
_MAX_COLLECTION_QUERIES = 1800


class RecoveryCoverageError(ValueError):
    """Only deterministic non-content error codes may reach callers/logs."""


def _declared_ids(template: str) -> tuple[str, ...]:
    return tuple(name for _, name, _, _ in Formatter().parse(template) if name)


def _scan_registered_project_documents(documents, project_id: str) -> dict[str, set[tuple[str, ...]]]:
    """Bounded project-only traversal of each registered Firestore model.

    Every query is tied to the owner-admitted project prefix. Intermediate paths
    (including nested Reviews, Canvas versions and member UI state) are scanned
    even if those models are excluded from normal ZIP packages.
    """
    pid = require_project_id(project_id)
    catalog: dict[str, set[tuple[str, ...]]] = {}
    cache: dict[tuple[str, ...], tuple[tuple[str, ...], ...]] = {}
    traversed = 0

    def collection(path: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:
        nonlocal traversed
        if path not in cache:
            if len(cache) >= _MAX_COLLECTION_QUERIES:
                raise RecoveryCoverageError('HOSTED_RECOVERY_CATALOG_QUERY_LIMIT')
            try:
                rows = documents.query_collection(path)
            except Exception:
                raise RecoveryCoverageError('HOSTED_RECOVERY_CATALOG_READ_FAILED') from None
            if len(rows) > _MAX_CATALOG_ROWS - traversed:
                raise RecoveryCoverageError('HOSTED_RECOVERY_CATALOG_ROW_LIMIT')
            paths = set()
            for row in rows:
                actual = tuple(row.path)
                if len(actual) != len(path) + 1 or actual[:len(path)] != path:
                    raise RecoveryCoverageError('HOSTED_RECOVERY_CATALOG_PATH_INVALID')
                paths.add(actual)
            traversed += len(rows)
            cache[path] = tuple(sorted(paths))
        return cache[path]

    for logical, model in sorted(MODELS.items(), key=lambda item: (len(item[1].path_template.split('/')), item[0])):
        parts = model.path_template.split('/')
        if len(parts) < 4 or parts[:2] != ['projects', '{project_id}']:
            continue
        if len(parts) % 2 or any(not part for part in parts):
            raise RecoveryCoverageError('HOSTED_RECOVERY_CATALOG_TEMPLATE_INVALID')
        parents = {('projects', pid)}
        for depth in range(2, len(parts), 2):
            name, identity = parts[depth:depth + 2]
            next_paths: set[tuple[str, ...]] = set()
            for parent in parents:
                for actual in collection(parent + (name,)):
                    if (identity.startswith('{') and identity.endswith('}')) or actual[-1] == identity:
                        next_paths.add(actual)
            parents = next_paths
            if not parents:
                break
        catalog[logical] = parents
    return catalog


def physical_project_fingerprint(documents, project_id: str) -> tuple[tuple[tuple[str, ...], int], ...]:
    """Fail closed unless provider exposes independent physical subtree scans.

    Used to fence a package's plaintext read phase against noncanonical writes
    that do not advance the project generation. Revision tokens are Firestore
    update timestamps on GCP and per-write revisions in the memory driver.
    """
    pid = require_project_id(project_id)
    provider = getattr(documents, 'project_document_inventory', None)
    if not callable(provider):
        raise RecoveryCoverageError('HOSTED_RECOVERY_PHYSICAL_INVENTORY_UNAVAILABLE')
    try:
        rows = provider(pid, max_documents=_MAX_CATALOG_ROWS,
                        max_collections=_MAX_COLLECTION_QUERIES)
    except Exception:
        raise RecoveryCoverageError('HOSTED_RECOVERY_PHYSICAL_INVENTORY_FAILED') from None
    root = ('projects', pid)
    observed = {}
    for row in rows:
        path = tuple(row.path)
        if (path[:2] != root or len(path) % 2 != 0 or
                path in observed or type(row.revision) is not int or row.revision < 1):
            raise RecoveryCoverageError('HOSTED_RECOVERY_PHYSICAL_INVENTORY_INVALID')
        observed[path] = row.revision
    if root not in observed:
        raise RecoveryCoverageError('HOSTED_RECOVERY_PHYSICAL_PROJECT_MISSING')
    return tuple(sorted(observed.items()))



def physical_project_gcs_fingerprint(objects, project_id: str) -> tuple[tuple[Any, ...], ...]:
    """Bounded, independent GCS state fence for an encrypted source snapshot.

    A Firestore revision fence alone cannot detect replaced/deleted immutable
    payloads or a concurrent object publication that skipped a canonical
    project-generation update. Compare both sides of in-memory ZIP generation;
    do not claim an atomic snapshot when object metadata changed mid-read.
    """
    pid = require_project_id(project_id)
    prefix = f'projects/{pid}/'
    try:
        rows = objects.list_prefix(prefix)
    except Exception:
        raise RecoveryCoverageError('HOSTED_RECOVERY_GCS_INVENTORY_FAILED') from None
    if len(rows) > _MAX_CATALOG_ROWS:
        raise RecoveryCoverageError('HOSTED_RECOVERY_GCS_OBJECT_LIMIT')
    observed = set()
    result = []
    for meta in rows:
        name = getattr(meta, 'name', None)
        generation = getattr(meta, 'generation', None)
        size = getattr(meta, 'size', None)
        digest = getattr(meta, 'sha256', None)
        content_type = getattr(meta, 'content_type', None)
        attributes = getattr(meta, 'metadata', None)
        if (not isinstance(name, str) or not name.startswith(prefix)
                or name in observed or type(generation) is not int or generation < 1
                or type(size) is not int or size < 0
                or not isinstance(digest, str) or len(digest) != 64
                or any(char not in '0123456789abcdef' for char in digest)
                or not isinstance(content_type, str) or not content_type
                or not isinstance(attributes, Mapping)
                or any(not isinstance(k, str) or not isinstance(v, str)
                       for k, v in attributes.items())):
            raise RecoveryCoverageError('HOSTED_RECOVERY_GCS_INVENTORY_INVALID')
        observed.add(name)
        result.append((name, generation, size, digest, content_type,
                       tuple(sorted(attributes.items()))))
    return tuple(sorted(result))


def require_registered_physical_paths(project_id: str,
                                      fingerprint: tuple[tuple[tuple[str, ...], int], ...],
                                      catalog: dict[str, set[tuple[str, ...]]]) -> None:
    """Prohibit unknown/orphaned Firestore documents before publication."""
    expected = {('projects', require_project_id(project_id))}
    for paths in catalog.values():
        expected.update(paths)
    physical = {path for path, _revision in fingerprint}
    if physical != expected:
        raise RecoveryCoverageError('HOSTED_RECOVERY_UNKNOWN_OR_ORPHANED_RECORD')


def require_project_gcs_private_inventory(projects, project_id: str,
                                          catalog: dict[str, set[tuple[str, ...]]]) -> None:
    """Independently inspect physical live GCS keys under just this project.

    Former ciphertext versions can be unreachable immutable orphans after CAS
    retries, and should be retained under the retention policy, NOT silently
    restored as authoritative content. Opaque, correctly formed encrypted block
    objects are allowed; legacy plaintext/unknown families abort publication.
    """
    from hosted.storage.encrypted_objects import HostedEncryptedObjectStore
    from hosted.storage.keys import require_object_name

    pid = require_project_id(project_id)
    try:
        observed = projects.objects.list_prefix(f'projects/{pid}/')
    except Exception:
        raise RecoveryCoverageError('HOSTED_RECOVERY_GCS_INVENTORY_FAILED') from None
    if len(observed) > _MAX_CATALOG_ROWS:
        raise RecoveryCoverageError('HOSTED_RECOVERY_GCS_OBJECT_LIMIT')
    by_name = {}
    # The M12 encrypted-object namespace has a CLOSED set of payload families.
    # Old immutable ciphertext versions may remain for retention, but unknown
    # families or plaintext objects can never enter a successful snapshot.
    from hosted.domain_store import _HYBRID_PAYLOAD_FIELDS, HostedDomainStore
    allowed_prefixes = tuple(f'projects/{pid}/private/v1/{name}/'
                             for name in sorted(set(_HYBRID_PAYLOAD_FIELDS) | {'project_asset'}))
    for meta in observed:
        name = getattr(meta, 'name', None)
        try:
            require_object_name(name)
        except Exception:
            raise RecoveryCoverageError('HOSTED_RECOVERY_GCS_PATH_INVALID') from None
        if (name in by_name or not any(name.startswith(p) for p in allowed_prefixes)
                or not name.endswith('.bme')):
            raise RecoveryCoverageError('HOSTED_RECOVERY_UNSUPPORTED_GCS_OBJECT')
        if (getattr(meta, 'content_type', None) != 'application/vnd.breadmaker.encrypted+json'
                or getattr(meta, 'metadata', {}).get('breadmaker-private-format') != FORMAT):
            raise RecoveryCoverageError('HOSTED_RECOVERY_UNENCRYPTED_GCS_OBJECT')
        by_name[name] = meta
    for logical in set(_HYBRID_PAYLOAD_FIELDS) | {'project_asset'}:
        for path in catalog.get(logical, set()):
            model = MODELS[logical]
            declared = _declared_ids(model.path_template)
            ids = {key: path[2 * index + 1] for index, key in enumerate(declared)}
            # Template represents alternating collection/doc segments; index 0
            # refers to project_id at path[1].
            row = projects.documents.get(path)
            if row is None:
                raise RecoveryCoverageError('HOSTED_RECOVERY_UNENCRYPTED_SOURCE_RECORD')
            if logical == 'manuscript_block':
                pointer = row.data.get('payload_object')
            else:
                try:
                    decoded = projects.domain.encrypted_fields.open(logical, ids, row.data)
                    pointer = decoded if logical == 'project_asset' else decoded.get('payload_object')
                except Exception:
                    raise RecoveryCoverageError('HOSTED_RECOVERY_UNENCRYPTED_SOURCE_RECORD') from None
            if not isinstance(pointer, dict):
                raise RecoveryCoverageError('HOSTED_RECOVERY_UNENCRYPTED_SOURCE_RECORD')
            name = pointer.get('object_name')
            meta = by_name.get(name)
            record_id = (ids['asset_id'] if logical == 'project_asset' else
                         HostedDomainStore._encrypted_hybrid_record_id(logical, ids))
            if (meta is None or name != HostedEncryptedObjectStore.object_name(
                    pid, logical, record_id, str(pointer.get('encryption_operation') or ''))
                    or meta.generation != pointer.get('object_generation')
                    or meta.sha256 != pointer.get('object_sha256')
                    or meta.size != pointer.get('object_size')
                    or pointer.get('encryption_format') != FORMAT):
                raise RecoveryCoverageError('HOSTED_RECOVERY_GCS_POINTER_MISMATCH')


def require_complete_private_recovery(projects, project_id: str, exported: Mapping[str, list[dict[str, Any]]], *, owner_user_id: str) -> None:
    """Reject missing, plaintext or unsupported registered project records.

    Call ONLY from the owner-admitted, dual-codec sealed-recovery source path.
    The exporter must have already read through authorized/domain decryption.
    This guard independently inventories the raw registered Firestore families
    and checks the final package row identities match exactly (no truncation).
    """
    pid = require_project_id(project_id)
    if projects.domain.encrypted_fields is None or projects.domain.encrypted_objects is None:
        raise RecoveryCoverageError('HOSTED_RECOVERY_CODECS_REQUIRED')
    # The workspace title is private author content. It must not be a cleartext
    # Firestore project-root property in a source admitted to private recovery.
    root = projects.documents.get(('projects', pid))
    if (root is None or root.data.get('title') != 'Private project'
            or not isinstance(root.data.get('_bm_private_title'), dict)):
        raise RecoveryCoverageError('HOSTED_RECOVERY_PROJECT_TITLE_UNENCRYPTED')
    # Authenticate title ciphertext under the exact authorized source project.
    projects.authority._open_project(pid, root.data)
    catalog = _scan_registered_project_documents(projects.documents, pid)
    # A second, independent physical walk sees unfamiliar collections, even
    # beneath a deleted parent in the deterministic memory implementation.
    # Only registered paths have explicitly reviewed portability semantics.
    fingerprint = physical_project_fingerprint(projects.documents, pid)
    require_registered_physical_paths(pid, fingerprint, catalog)
    for logical, paths in catalog.items():
        if not paths:
            if exported.get(logical):
                raise RecoveryCoverageError('HOSTED_RECOVERY_CATALOG_MISMATCH')
            continue
        if logical in _RECREATED_AUTHORITY:
            if logical in exported:
                raise RecoveryCoverageError('HOSTED_RECOVERY_AUTHORITY_NOT_PORTABLE')
            continue
        # project itself is in the root authority document and represented by
        # the recovery ZIP manifest, not by a project-scoped child model.
        if logical == 'project':
            continue
        # The project's operator recovery is not a migration of accounts.
        # Only the source owner's own UI/bookmark state is portable, and it
        # must be re-keyed to the destination owner on import. A non-owner
        # state row blocks snapshot publication, not silently lost or granted.
        if logical in {'member_bookmark', 'member_ui_state'}:
            owner_prefix = ('projects', pid, 'members', require_user_id(owner_user_id))
            if any(path[:4] != owner_prefix for path in paths):
                raise RecoveryCoverageError('HOSTED_RECOVERY_FOREIGN_MEMBER_STATE_NOT_PORTABLE')
        if logical != 'manuscript_block' and not HostedPrivateFirestoreFields.protects(logical):
            # Old plaintext/nonportable hybrid objects, system/configuration,
            # historical revisions and user state must never silently vanish.
            raise RecoveryCoverageError('HOSTED_RECOVERY_UNSUPPORTED_SOURCE_MODEL')
        model = MODELS[logical]
        declared = _declared_ids(model.path_template)
        actual: set[tuple[str, ...]] = set()
        for entry in exported.get(logical, []):
            if not isinstance(entry, dict) or not isinstance(entry.get('ids'), dict):
                raise RecoveryCoverageError('HOSTED_RECOVERY_EXPORTED_ROW_INVALID')
            ids = entry['ids']
            if set(ids) != set(declared) or ids.get('project_id') != pid:
                raise RecoveryCoverageError('HOSTED_RECOVERY_EXPORTED_ROW_INVALID')
            try:
                path = tuple(render_model_path(model, **ids))
            except Exception:
                raise RecoveryCoverageError('HOSTED_RECOVERY_EXPORTED_ROW_INVALID') from None
            if path in actual:
                raise RecoveryCoverageError('HOSTED_RECOVERY_DUPLICATE_ROW')
            actual.add(path)
        if actual != paths:
            raise RecoveryCoverageError('HOSTED_RECOVERY_CATALOG_MISMATCH')
        # Enforce that the directly stored Firestore records are ciphertext for
        # supported families, not merely that a codec was injected for reads.
        if logical != 'manuscript_block':
            for path in paths:
                raw = projects.documents.get(path)
                if raw is None or not isinstance(raw.data.get(PRIVATE_FIELD_NAME), dict):
                    raise RecoveryCoverageError('HOSTED_RECOVERY_UNENCRYPTED_SOURCE_RECORD')
        else:
            for path in paths:
                raw = projects.documents.get(path)
                pointer = raw.data.get('payload_object') if raw is not None else None
                if not isinstance(pointer, dict) or pointer.get('encryption_format') != 'bm-hosted-envelope-v1':
                    # The independent domain reader already rejects legacy
                    # plaintext pointers, but keep the source inventory strict.
                    raise RecoveryCoverageError('HOSTED_RECOVERY_UNENCRYPTED_SOURCE_RECORD')

    # Physical GCS may contain unreferenced immutable ciphertext from failed CAS;
    # it must never contain a hidden plaintext or unrecognized object family.
    require_project_gcs_private_inventory(projects, pid, catalog)
