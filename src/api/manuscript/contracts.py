from __future__ import annotations

from typing import Optional
from pydantic import Field
from api.shared_contracts import BaseModel, Reorder

class ProjectUpdate(BaseModel): title: str

class ChapterCreate(BaseModel): title: str = "New Chapter"; tab_id: Optional[int]=None; after_chapter_id:Optional[int]=None

class ChapterTitle(BaseModel): title:str; expected_revision:Optional[str]=None

class ChapterUpdate(BaseModel): description:Optional[str]=None; expected_revision:Optional[str]=None

class ChapterSplit(BaseModel): block_id:str; title:str=""

class BlockIn(BaseModel): id:Optional[str]=None; block_type:str="paragraph"; semantic_style:str="body"; content_html:str=""; text_content:str=""; image_path:str=""; image_width:int=100; image_footer:str=""; image_description:str=""; image_row_id:str=""; scene_id:Optional[int]=None

class BlocksUpdate(BaseModel): blocks:list[BlockIn]; expected_revision:Optional[str]=None

class ManuscriptImageInsert(BaseModel): image_path:str; target_block_id:str; placement:str="at_offset"; offset:int=0; image_width:int=100; image_footer:str=""; image_description:str=""

class ManuscriptImageMove(BaseModel): target_block_id:str; placement:str="before"; offset:int=0

class SceneCreate(BaseModel): title:str=""; description:str=""; block_ids:list[str]=[]; tab_id:Optional[int]=None; after_scene_id:Optional[int]=None; default_title_prefix:str=""

class SceneUpdate(BaseModel): title:Optional[str]=None; description:Optional[str]=None

class SceneRangeUpdate(BaseModel): block_ids:list[str]=[]

class SceneBlockIn(BlockIn): chapter_id:int

class SceneBlocksUpdate(BaseModel): blocks:list[SceneBlockIn]; expected_revisions:dict[str,str]={}

class SceneMove(BaseModel): before_scene_id:Optional[int]=None; after_scene_id:Optional[int]=None; target_block_id:Optional[str]=None
