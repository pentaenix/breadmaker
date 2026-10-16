from __future__ import annotations
import math
from .validation import ProjectJSONError

def _layout_positions(canvas:dict)->dict[str,dict[str,float]]:
    nodes={n["id"]:n for n in canvas["nodes"]}
    positions:dict[str,dict[str,float]]={}
    explicit={nid for nid,n in nodes.items() if n["position"].get("x") is not None and n["position"].get("y") is not None}
    for nid in explicit:
        p=nodes[nid]["position"];positions[nid]={"x":float(p["x"]),"y":float(p["y"]),"w":float(p["width"]),"h":float(p["height"])}
    assigned=set(explicit)

    def arrange(mode:str,refs:list[str],origin:dict,gap:float):
        refs=[r for r in refs if r in nodes and r not in assigned]
        if not refs:return
        ox=float(origin.get("x",100));oy=float(origin.get("y",100));gap=float(gap)
        if mode=="manual":
            missing=[r for r in refs if nodes[r]["position"].get("x") is None or nodes[r]["position"].get("y") is None]
            if missing:raise ProjectJSONError(f"Manual layout requires x/y for: {', '.join(missing[:5])}","$.canvases")
            return
        if mode=="horizontal":
            x=ox
            for r in refs:
                p=nodes[r]["position"];w=float(p["width"]);h=float(p["height"]);positions[r]={"x":x,"y":oy,"w":w,"h":h};x+=w+gap;assigned.add(r)
        elif mode=="vertical":
            y=oy
            for r in refs:
                p=nodes[r]["position"];w=float(p["width"]);h=float(p["height"]);positions[r]={"x":ox,"y":y,"w":w,"h":h};y+=h+gap;assigned.add(r)
        elif mode=="circular":
            if len(refs)==1:
                r=refs[0];p=nodes[r]["position"];positions[r]={"x":ox,"y":oy,"w":float(p["width"]),"h":float(p["height"])};assigned.add(r);return
            maxdim=max(max(float(nodes[r]["position"]["width"]),float(nodes[r]["position"]["height"])) for r in refs)
            radius=max(150.0,maxdim*.75+len(refs)*18)
            cx=ox+radius;cy=oy+radius
            for i,r in enumerate(refs):
                p=nodes[r]["position"];w=float(p["width"]);h=float(p["height"]);ang=-math.pi/2+2*math.pi*i/len(refs)
                positions[r]={"x":cx+math.cos(ang)*radius-w/2,"y":cy+math.sin(ang)*radius-h/2,"w":w,"h":h};assigned.add(r)
        else: # compact
            cols=max(1,min(4,math.ceil(math.sqrt(len(refs)))))
            rows=[refs[i:i+cols] for i in range(0,len(refs),cols)];y=oy
            for row in rows:
                x=ox;row_h=max(float(nodes[r]["position"]["height"]) for r in row)
                for r in row:
                    p=nodes[r]["position"];w=float(p["width"]);h=float(p["height"]);positions[r]={"x":x,"y":y,"w":w,"h":h};x+=w+gap;assigned.add(r)
                y+=row_h+gap

    for group in canvas.get("layouts") or []:
        refs=group.get("nodes") or [r for r in nodes if r not in assigned]
        arrange(group.get("mode") or "compact",refs,group.get("origin") or {"x":100,"y":100},float(group.get("gap") or 32))
    remaining=[r for r in nodes if r not in assigned]
    if remaining:
        # Keep manually positioned objects out of the default group and lay the rest
        # in a tidy compact block. Put it below explicit objects when necessary.
        base_y=100.0
        if positions:base_y=max(v["y"]+v["h"] for v in positions.values())+40
        arrange("compact",remaining,{"x":100,"y":base_y},32)
    return positions


# ---------- Import ----------


