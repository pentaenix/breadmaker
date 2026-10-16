from __future__ import annotations

from typing import Optional
from pydantic import Field
from api.shared_contracts import BaseModel, Reorder

class CanvasCreate(BaseModel): name:str; description:str=""; canvas_type:str="blank"; developmental_scope:str="chapters"; parent_id:Optional[int]=None

class CanvasUpdate(BaseModel): name:Optional[str]=None; description:Optional[str]=None; parent_id:Optional[int]=None

class CanvasVersionCreate(BaseModel): name:str=""; from_version_id:Optional[int]=None

class CanvasVersionUpdate(BaseModel): name:str

class DevelopmentalLayoutItem(BaseModel): node_id:int; lane_position:Optional[int]=None; x:Optional[float]=None; y:Optional[float]=None

class DevelopmentalLayout(BaseModel): items:list[DevelopmentalLayoutItem]

class DevelopmentalPromote(BaseModel): chapter_id:Optional[int]=None; title:Optional[str]=None

class NodeCreate(BaseModel): node_type:str="free"; shape:str="rect"; icon:str=""; x:float=100; y:float=100; w:float=220; h:float=130; title:str=""; body:str=""; display_mode:str="full"; text_align:str="left"; color:str=""; text_color:str=""; figure_fill_color:str=""; figure_stroke_color:str=""; figure_border_width:float=1.0; figure_layer:str="back"; figure_line_x1:float=0.02; figure_line_y1:float=0.5; figure_line_x2:float=0.98; figure_line_y2:float=0.5; label_id:Optional[int]=None; chapter_id:Optional[int]=None; scene_id:Optional[int]=None; image_path:str=""; icon_marker_id:Optional[int]=None

class NodeUpdate(BaseModel): x:Optional[float]=None; y:Optional[float]=None; w:Optional[float]=None; h:Optional[float]=None; title:Optional[str]=None; body:Optional[str]=None; display_mode:Optional[str]=None; text_align:Optional[str]=None; color:Optional[str]=None; text_color:Optional[str]=None; figure_fill_color:Optional[str]=None; figure_stroke_color:Optional[str]=None; figure_border_width:Optional[float]=None; figure_layer:Optional[str]=None; figure_line_x1:Optional[float]=None; figure_line_y1:Optional[float]=None; figure_line_x2:Optional[float]=None; figure_line_y2:Optional[float]=None; shape:Optional[str]=None; icon:Optional[str]=None; label_id:Optional[int]=None; icon_marker_id:Optional[int]=None; image_path:Optional[str]=None

class EdgeCreate(BaseModel): from_node_id:int; to_node_id:int; from_port:str="right"; to_port:str="left"; label:str=""; color:str=""; marker_id:Optional[int]=None; line_style:str="solid"; end_style:str="arrow"; line_width:float=1.5; route_style:str="curved"

class EdgeUpdate(BaseModel): from_node_id:Optional[int]=None; to_node_id:Optional[int]=None; from_port:Optional[str]=None; to_port:Optional[str]=None; label:Optional[str]=None; color:Optional[str]=None; marker_id:Optional[int]=None; line_style:Optional[str]=None; end_style:Optional[str]=None; line_width:Optional[float]=None; route_style:Optional[str]=None

class CanvasNodeBulkDelete(BaseModel): ids:list[int]

class CanvasNodePosition(BaseModel): id:int; x:float; y:float

class CanvasNodePositionBatch(BaseModel): items:list[CanvasNodePosition]

class DevelopmentalApply(BaseModel): ordered_node_ids:list[int]

class CanvasPopulateRequest(BaseModel): template_id:str; x:float=100; y:float=100
