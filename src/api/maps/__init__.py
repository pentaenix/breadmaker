from __future__ import annotations

"""Story Maps HTTP/domain package.

Route owners are composed in the historical registration order. Internal helpers live
in explicit subdomain modules and are not re-exported from this package boundary.
"""

from fastapi import APIRouter
from . import lifecycle, lines, items, junctions, layout, populate

router=APIRouter()
for _owner in (lifecycle, lines, items, junctions, layout, populate):
    router.include_router(_owner.router)
