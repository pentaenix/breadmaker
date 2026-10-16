from __future__ import annotations

from typing import Optional
from pydantic import Field
from api.shared_contracts import BaseModel, Reorder

class MapCreate(BaseModel): name:str="Story Map"; description:str=""; orientation:str="horizontal"; layout_mode:str="scaled"; label_mode:str="minimal"; ruler:str="chapters"

class MapUpdate(BaseModel): name:Optional[str]=None; description:Optional[str]=None; orientation:Optional[str]=None; layout_mode:Optional[str]=None; label_mode:Optional[str]=None; ruler:Optional[str]=None; background:Optional[str]=None

class MapLineCreate(BaseModel): name:str="Story Line"; description:str=""; color:str=""; visible:bool=True; planned_start:float=0.0; planned_end:float=1.0; schematic_start:float=0.0; schematic_end:float=1.0

class MapLineUpdate(BaseModel): name:Optional[str]=None; description:Optional[str]=None; color:Optional[str]=None; visible:Optional[bool]=None; position:Optional[int]=None; layout_locked:Optional[bool]=None; lane_offset:Optional[float]=None; planned_start:Optional[float]=None; planned_end:Optional[float]=None; schematic_start:Optional[float]=None; schematic_end:Optional[float]=None

class MapItemCreate(BaseModel):
    item_type:str="stop"; title:str="Untitled Stop"; short_label:str=""; description:str=""
    planned_start:Optional[float]=None; planned_end:Optional[float]=None; schematic_start:Optional[float]=None; schematic_end:Optional[float]=None
    start_anchor_type:Optional[str]=None; start_anchor_id:Optional[str]=None; start_block_id:Optional[str]=None; start_offset:int=0
    end_anchor_type:Optional[str]=None; end_anchor_id:Optional[str]=None; end_block_id:Optional[str]=None; end_offset:int=0
    excerpt:str=""; label_offset:float=0; label_pinned:bool=False; break_after:bool=False

class MapItemUpdate(BaseModel):
    item_type:Optional[str]=None; title:Optional[str]=None; short_label:Optional[str]=None; description:Optional[str]=None
    planned_start:Optional[float]=None; planned_end:Optional[float]=None; schematic_start:Optional[float]=None; schematic_end:Optional[float]=None
    start_anchor_type:Optional[str]=None; start_anchor_id:Optional[str]=None; start_block_id:Optional[str]=None; start_offset:Optional[int]=None
    end_anchor_type:Optional[str]=None; end_anchor_id:Optional[str]=None; end_block_id:Optional[str]=None; end_offset:Optional[int]=None
    excerpt:Optional[str]=None; label_offset:Optional[float]=None; label_pinned:Optional[bool]=None; break_after:Optional[bool]=None
    clear_start_anchor:bool=False; clear_end_anchor:bool=False

class MapJunctionCreate(BaseModel):
    kind:str="branch"
    from_line_id:Optional[int]=None; from_item_id:Optional[int]=None; from_edge:str="point"; from_planned_position:Optional[float]=None; from_schematic_position:Optional[float]=None
    to_line_id:Optional[int]=None; to_item_id:Optional[int]=None; to_edge:str="point"; to_planned_position:Optional[float]=None; to_schematic_position:Optional[float]=None

class MapJunctionUpdate(BaseModel):
    kind:Optional[str]=None
    from_line_id:Optional[int]=None; from_item_id:Optional[int]=None; from_edge:Optional[str]=None; from_planned_position:Optional[float]=None; from_schematic_position:Optional[float]=None
    to_line_id:Optional[int]=None; to_item_id:Optional[int]=None; to_edge:Optional[str]=None; to_planned_position:Optional[float]=None; to_schematic_position:Optional[float]=None

class MapPopulateRequest(BaseModel): template_id:str
