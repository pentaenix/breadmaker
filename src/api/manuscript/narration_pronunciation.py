"""Local pronunciation HTTP owner; native algorithms are shared with the private helper."""
from __future__ import annotations

import threading
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from backend.environment import runtime
from narration.pronunciation import PronunciationError, validate_input, _frontend, _items, _italian
from .narration_boundary import NarrationRoute

router = APIRouter(route_class=NarrationRoute)
_lock = threading.Lock()


class PronunciationInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    language: Literal['it','ja','zh-CN']
    text: str = Field(default='', max_length=20000)
    words: list[str] = Field(default_factory=list, max_length=10000)


@router.post('/api/narration-pronunciation')
def prepare_narration_pronunciation(data:PronunciationInput):
    if runtime.mode!='local':raise HTTPException(404)
    if runtime.context.current() is None:raise HTTPException(409,'PROJECT_CONTEXT_REQUIRED')
    try:
        validate_input(data.language, data.text, data.words)
    except PronunciationError as exc:
        raise HTTPException(400, str(exc)) from None
    if not _lock.acquire(blocking=False):raise HTTPException(429,'NARRATION_PRONUNCIATION_BUSY',headers={'Retry-After':'1'})
    try:
        frontend=_frontend(data.language)
        return {'phonemes':[_italian(word,frontend) for word in data.words]} if data.language=='it' else {'items':_items(data.text,data.language,frontend)}
    except ImportError:raise HTTPException(503,'NARRATION_LANGUAGE_NOT_INSTALLED')
    except PronunciationError as exc:raise HTTPException(422,str(exc)) from None
    except (ValueError,AssertionError,KeyError):raise HTTPException(422,'NARRATION_PRONUNCIATION_UNSUPPORTED')
    finally:_lock.release()
