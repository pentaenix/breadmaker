from __future__ import annotations

from typing import Optional
from pydantic import Field
from api.shared_contracts import BaseModel, Reorder

class PDFExportOptions(BaseModel):
    cover:bool=True
    title:str=""
    author:str=""
    cover_date:str=""
    version:str=""
    notes:str=""
    page_numbering:bool=True
    header:str=""
    watermark:str=""
    font_size:int=11
    font_type:str="book-serif"
    page_size:str="letter"
    chapter_page_breaks:bool=True
