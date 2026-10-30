from __future__ import annotations

"""Manuscript HTTP-domain package. Implementation lives in focused owners."""

from fastapi import APIRouter

from . import document as _document
from . import images as _images
from . import chapter_structure as _chapter_structure
from . import chapters as _chapters
from . import scenes as _scenes
from . import narration as _narration
from . import narration_voices as _narration_voices
from . import narration_pronunciation as _narration_pronunciation

router=APIRouter()
for _owner in (_document,_images,_chapter_structure,_chapters,_scenes,_narration,_narration_voices,_narration_pronunciation):
    router.include_router(_owner.router)
