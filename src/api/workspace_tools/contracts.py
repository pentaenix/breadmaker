from __future__ import annotations

from typing import Optional
from pydantic import Field
from api.shared_contracts import BaseModel, Reorder

class ReferenceCreate(BaseModel):
    source_type:str; source_id:str; target_type:str; target_id:str
    start_block_id:Optional[str]=None; end_block_id:Optional[str]=None
    start_offset:int=0; end_offset:int=0; excerpt:str=""; relationship:str=""

class UIStateIn(BaseModel): value:object

class BookmarkSet(BaseModel):
    target_type:str
    chapter_id:Optional[int]=None
    scene_id:Optional[int]=None
    start_block_id:Optional[str]=None
    end_block_id:Optional[str]=None
    start_offset:int=0
    end_offset:int=0

class DocumentCreate(BaseModel): title:str="Notes"; description:str=""; is_manuscript:bool=False


class DocumentUpdate(BaseModel):
    title:Optional[str]=None
    description:Optional[str]=None
