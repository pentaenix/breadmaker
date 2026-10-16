from __future__ import annotations

from typing import Optional
from pydantic import Field
from api.shared_contracts import BaseModel, Reorder

class CutCreate(BaseModel): title:str; content_html:str; source_chapter_id:Optional[int]=None; source_scene_id:Optional[int]=None; note:str=""

class CutUpdate(BaseModel): note:Optional[str]=None

class CutInsert(BaseModel): chapter_id:int; block_id:str; offset:int=0

class HistoryRestoreRequest(BaseModel):
    expected_generation:Optional[int]=None
