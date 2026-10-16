from __future__ import annotations

from typing import Optional
from pydantic import Field
from api.shared_contracts import BaseModel, Reorder

class FindRequest(BaseModel): query:str; case_sensitive:bool=False; whole_word:bool=False; scope:str="tab"; chapter_id:Optional[int]=None; tab_id:Optional[int]=None; scene_id:Optional[int]=None

class ReplaceRequest(FindRequest): replacement:str; preview:bool=True
