from __future__ import annotations

import os

from .contracts import RuntimeServices
from .local import build_local_runtime


def build_runtime(workspace) -> RuntimeServices:
    """Build one infrastructure adapter set for the selected Breadmaker runtime.

    local       — downloadable/local-first Breadmaker.
    hosted_dev  — localhost/development hosted sandbox.
    hosted      — explicit, horizontally safe staging runtime (Firestore/GCS).

    Authentication remains a runtime adapter. Local Breadmaker needs no account; hosted
    identity derives exclusively from a server-side Breadmaker session.
    """
    mode=str(os.environ.get('BREADMAKER_RUNTIME') or 'local').strip().lower()
    if mode=='local':return build_local_runtime(workspace)
    if mode=='hosted_dev':
        from .hosted import build_hosted_dev_runtime
        return build_hosted_dev_runtime(workspace)
    if mode=='hosted':
        from .hosted import build_hosted_runtime
        return build_hosted_runtime(workspace)
    raise RuntimeError(f'BREADMAKER_RUNTIME_UNSUPPORTED:{mode}')


__all__=['RuntimeServices','build_runtime']
