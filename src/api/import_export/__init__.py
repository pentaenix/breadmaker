from __future__ import annotations

"""Import/export HTTP domain. Composition only; helpers live with their canonical owner."""

from fastapi import APIRouter
from . import docx_import, docx_export, pdf_export, assets

router=APIRouter()
for _owner in (docx_import, docx_export, pdf_export, assets):
    router.include_router(_owner.router)
