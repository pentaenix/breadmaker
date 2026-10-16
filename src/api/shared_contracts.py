from __future__ import annotations

from pydantic import BaseModel as PydanticBaseModel, ConfigDict

class BaseModel(PydanticBaseModel):
    """Shared finite-number HTTP payload base; not a domain model registry."""
    model_config = ConfigDict(allow_inf_nan=False)

class Reorder(BaseModel):
    ids:list[int]
