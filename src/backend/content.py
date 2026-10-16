from __future__ import annotations

"""Shared manuscript/content validation and sanitization primitives."""

import html
import re

from bs4 import BeautifulSoup, NavigableString
from fastapi import HTTPException

from security import content_security

_ALLOWED_INLINE=set(content_security.ALLOWED_INLINE)

def _text_from_inline_html(fragment:str)->str:
    soup=BeautifulSoup(fragment or "", "html.parser")
    out=[]
    for node in soup.descendants:
        if isinstance(node,NavigableString): out.append(str(node))
        elif getattr(node,"name",None)=="br": out.append("\n")
    return "".join(out)

def _html_from_text(text:str)->str:
    return html.escape(text or "").replace("\n","<br>")

HEX_COLOR_RE=re.compile(r'^#[0-9a-fA-F]{6}$')

VALID_BLOCK_TYPES={'paragraph','image'}

VALID_SEMANTIC_STYLES={'body','heading2','heading3','heading4','title','image'}

VALID_NODE_SHAPES={'rect','rounded','text','routing','label','circle','triangle','oval','line'}

CONTENT_CARD_SHAPES={'rect','text'}

FIGURE_NODE_SHAPES={'rect','circle','triangle','oval','line'}

VALID_NODE_TYPES={'free','image','chapter','scene','routing','label','figure'}

VALID_CANVAS_TYPES={'blank','developmental'}

DEVELOPMENTAL_LANE_START_X=120.0

DEVELOPMENTAL_LANE_CARD_Y=118.0

DEVELOPMENTAL_LANE_BOTTOM=286.0

DEVELOPMENTAL_FREE_Y=390.0

DEVELOPMENTAL_CARD_GAP=32.0

VALID_ANNOTATION_TYPES={'highlight','comment','note','suggestion'}

def normalize_color(value:str, allow_empty:bool=True)->str:
    value=(value or '').strip()
    if allow_empty and not value: return ''
    if not HEX_COLOR_RE.fullmatch(value): raise HTTPException(400,'Color must be a six-digit hex color')
    return value.lower()

def sanitize_inline_html(fragment:str,text_content:str)->str:
    return content_security.sanitize_inline_html(fragment,text_content)
