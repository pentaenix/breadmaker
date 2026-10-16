from __future__ import annotations

"""Manuscript HTTP-domain package. Implementation lives in focused owners."""

from fastapi import APIRouter

from . import document as _document
from . import images as _images
from . import chapter_structure as _chapter_structure
from . import chapters as _chapters
from . import scenes as _scenes

router=APIRouter()
for _owner in (_document,_images,_chapter_structure,_chapters,_scenes):
    router.include_router(_owner.router)
