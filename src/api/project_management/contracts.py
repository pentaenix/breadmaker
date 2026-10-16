from __future__ import annotations

from typing import Optional
from pydantic import Field
from api.shared_contracts import BaseModel, Reorder

class DangerChallengeCreate(BaseModel):
    action: str
    project_id: Optional[str]=Field(default=None,max_length=128)

class DangerConfirm(BaseModel):
    challenge_id: str
    code: str
    replacement_name: Optional[str]=None
    erase_external_backups: bool=False
    external_challenge_id: Optional[str]=None
    external_code: Optional[str]=None
    external_warning_acknowledged: bool=False

class WorkspaceProjectCreate(BaseModel):
    name: str = "Untitled Book"

class WorkspaceProjectRename(BaseModel):
    name: str

class LocalProfileIn(BaseModel):
    display_name: str = Field(min_length=1,max_length=80)

class LocalProfileCreate(BaseModel):
    display_name: str = Field(min_length=1,max_length=80)

class ProjectMemberAdd(BaseModel):
    user_id: str = Field(min_length=5,max_length=96)
    role: str = Field(default='editor',pattern='^(editor)$')
