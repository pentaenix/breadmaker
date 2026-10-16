from __future__ import annotations

from typing import Optional
from pydantic import Field
from api.shared_contracts import BaseModel, Reorder

class TaskCreate(BaseModel): title:str; description:str=""; status:str="open"; marker_id:Optional[int]=None

class TaskUpdate(BaseModel): title:Optional[str]=None; description:Optional[str]=None; status:Optional[str]=None; marker_id:Optional[int]=None

class MilestoneCreate(BaseModel): title:str; description:str=""

class MilestoneUpdate(BaseModel): title:Optional[str]=None; description:Optional[str]=None; completed:Optional[bool]=None
