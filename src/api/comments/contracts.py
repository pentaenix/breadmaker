from __future__ import annotations

from typing import Optional
from pydantic import Field
from api.shared_contracts import BaseModel, Reorder
from api.annotations.contracts import AnnotationItem

class CommentCreate(BaseModel): body:str; items:list[AnnotationItem]

class CommentThreadUpdate(BaseModel): status:Optional[str]=None

class CommentMessageCreate(BaseModel): body:str

class CommentMessageUpdate(BaseModel): body:str
