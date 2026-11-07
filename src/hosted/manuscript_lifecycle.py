"""Root-only manuscript compatibility for trusted physical-data operators.

This returns a lifecycle status, never a replacement root or a filtered store.
Inventories, digests and writes must keep the original physical document.
No format is activated here, and an erasure marker grants no purge authority.
"""
from hosted.firestore.contracts import HostedValidationError
from hosted.manuscript_visibility import FORMAT_FIELD, FORMAT_VERSION, decode_project_root


def read_project_lifecycle_status(root, *, default=None):
    """Validate a known root format without changing its physical representation."""
    if root is None:
        return default
    if root.path[0] != 'projects' or len(root.path) != 2:
        raise HostedValidationError('HOSTED_MANUSCRIPT_PROJECT_ROOT_REQUIRED')
    if FORMAT_FIELD in root.data:
        version = root.data[FORMAT_FIELD]
        if type(version) is not int or version != FORMAT_VERSION:
            raise HostedValidationError('HOSTED_MANUSCRIPT_FORMAT_UNSUPPORTED')
        if root.data.get('status') not in {
                'active_manuscript_v1', 'deleted_manuscript_v1', 'erasure_pending'}:
            raise HostedValidationError('HOSTED_MANUSCRIPT_FORMAT_STATUS_INVALID')
    return decode_project_root(root).data.get('status', default)
