from __future__ import annotations

from typing import Optional, Literal
from pydantic import Field, StrictInt
from api.shared_contracts import BaseModel, Reorder

class LabelCreate(BaseModel): name:str; color:str; description:str=""

class AnnotationItem(BaseModel): block_id:Optional[str]=None; chapter_id:Optional[int]=None; start_offset:int; end_offset:int; selected_text:str=""; prefix_context:str=""; suffix_context:str=""

class AnnotationBatch(BaseModel): annotation_type:str="highlight"; label_id:Optional[int]=None; body:str=""; items:list[AnnotationItem]

class AnnotationClear(BaseModel): items:list[AnnotationItem]

class AnnotationUpdate(BaseModel): status:Optional[str]=None; body:Optional[str]=None; label_id:Optional[int]=None

class EditingSearchRequest(BaseModel):
    scan:Literal['telling','filter','adverbs','qualifiers','wordy','redundancies','emotions']
    tab_id:StrictInt
    action:Literal['scan','clear','remove','recheck']='scan'
