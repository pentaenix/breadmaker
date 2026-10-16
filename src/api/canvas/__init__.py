from __future__ import annotations

"""Canvas HTTP/domain package.

Routes are composed in the historical registration order. Implementation lives in
explicit subdomain owners; this package does not re-export internal helpers.
"""

from fastapi import APIRouter
from . import populate, registry, versions, lifecycle, nodes, edges, structure

router = APIRouter()
for _owner in (populate, registry, versions, lifecycle, nodes, edges, structure):
    router.include_router(_owner.router)
