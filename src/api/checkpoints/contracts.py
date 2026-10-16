from __future__ import annotations

from typing import Optional
from pydantic import Field
from api.shared_contracts import BaseModel, Reorder

class CheckpointCreate(BaseModel):
    scope:str="chapter"; chapter_id:Optional[int]=None; name:str=""; description:str=""
    include_scenes:bool=True; include_markup:bool=True; include_notes:bool=True

class CheckpointUpdate(BaseModel):
    name:Optional[str]=None; description:Optional[str]=None

class CheckpointApply(BaseModel):
    target_chapter_id:Optional[int]=None
    add_as_chapter:bool=False
    confirm_conflicts:bool=False
    preflight_token:Optional[str]=None
