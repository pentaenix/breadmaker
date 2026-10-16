from __future__ import annotations

from typing import Optional
from pydantic import Field
from api.shared_contracts import BaseModel, Reorder

class IconMarkerCreate(BaseModel): name:str; description:str=""; icon:str

class IconMarkerUpdate(BaseModel): name:Optional[str]=None; description:Optional[str]=None; icon:Optional[str]=None
