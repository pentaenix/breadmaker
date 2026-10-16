from __future__ import annotations

from typing import Optional
from pydantic import Field
from api.shared_contracts import BaseModel, Reorder

class TestAuthLoginIn(BaseModel):
    fixture: str = Field(pattern='^(owner|editor|second_owner|unowned_user)$')

class SecurityStepUpIn(BaseModel):
    purpose: str = Field(min_length=1,max_length=80)
    target: str = Field(default='',max_length=1024)
    return_to: str = Field(default='/',max_length=2048)


class SecurityIdentityIn(BaseModel):
    subject: str = Field(min_length=1,max_length=1024)


class SecurityTotpMethodIn(BaseModel):
    method_id: str = Field(min_length=1,max_length=512)
    return_to: str = Field(default='/',max_length=2048)


class SecuritySessionIn(BaseModel):
    session_id: str = Field(min_length=1,max_length=128)


class SecurityReturnIn(BaseModel):
    return_to: str = Field(default='/',max_length=2048)

