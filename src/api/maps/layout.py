from __future__ import annotations

"""Story Map automatic layout routes."""

from fastapi import APIRouter, HTTPException
from backend.data_access import allrows, project
from storage import mutations as storage_mutations
from storage import values as storage_values
from . import anchors, domain

router=APIRouter()

@router.post("/api/maps/{mid}/auto-arrange")
def auto_arrange_map(mid:int):
    with storage_mutations.tx() as con:
        p=project(con)
        if not domain._map_row(con,p['id'],mid): raise HTTPException(404,detail="MAP_002: Map not found.")
        chronology=anchors._manuscript_chronology(con,p['id'])
        for line in allrows(con,"SELECT id FROM map_lines WHERE map_id=? ORDER BY position,id",(mid,)):
            raw=allrows(con,"SELECT * FROM map_items WHERE line_id=? ORDER BY position,id",(line['id'],))
            projected=[(item,anchors._project_map_item(con,item,chronology)) for item in raw]
            projected.sort(key=lambda pair:(pair[1].get('scaled_start') is None,pair[1].get('scaled_start') if pair[1].get('scaled_start') is not None else 2.0,pair[0]['position'],pair[0]['id']))
            n=len(projected)
            for index,(item,view) in enumerate(projected):
                start=.5 if n==1 else .05+.90*index/(n-1)
                end=None
                if item['item_type']=='span':
                    if view.get('scaled_start') is not None and view.get('scaled_end') is not None:
                        width=view['scaled_end']-view['scaled_start']
                    elif item.get('planned_start') is not None and item.get('planned_end') is not None:
                        width=item['planned_end']-item['planned_start']
                    else: width=.12
                    width=max(.06,min(.30,abs(width))); start=min(start,.97-width); end=start+width
                con.execute("UPDATE map_items SET position=?,schematic_start=?,schematic_end=?,label_pinned=0,label_offset=0,updated_at=? WHERE id=?",(index,start,end,storage_values.now(),item['id']))
    return {"ok":True}
