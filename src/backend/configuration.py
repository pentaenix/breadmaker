from __future__ import annotations

"""Read-only application configuration loaders."""

import json
import math

from fastapi import HTTPException

from backend.environment import FEATURE_FLAGS_CONFIG, CANVAS_CONFIG, METRO_VIEW_CONFIG

def _feature_flags_raw():
    try:
        data=json.loads(FEATURE_FLAGS_CONFIG.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError) as exc:
        raise HTTPException(500,f"Feature flag configuration is invalid: {exc}")
    features=data.get("features")
    if data.get("version")!=1 or not isinstance(features,dict):
        raise HTTPException(500,"Feature flag configuration must use version 1 with a features object")
    for key,value in features.items():
        if not isinstance(key,str) or not key.strip() or not isinstance(value,bool):
            raise HTTPException(500,"Feature flags require non-empty string keys and boolean values")
    return {"version":1,"features":features}

def _canvas_config_raw():
    try:
        data=json.loads(CANVAS_CONFIG.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError) as exc:
        raise HTTPException(500,f"Canvas configuration is invalid: {exc}")
    if data.get("version")!=1:
        raise HTTPException(500,"Canvas configuration must use version 1")
    typography=data.get("node_typography")
    if not isinstance(typography,dict):
        raise HTTPException(500,"Canvas configuration requires a node_typography object")
    size=typography.get("content_font_size_pt")
    if isinstance(size,bool) or not isinstance(size,(int,float)) or not math.isfinite(size) or not 8 <= float(size) <= 36:
        raise HTTPException(500,"Canvas node content_font_size_pt must be a finite number from 8 to 36")
    return {"version":1,"node_typography":{"content_font_size_pt":float(size)}}

def _metro_view_config_raw():
    try:
        data=json.loads(METRO_VIEW_CONFIG.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError) as exc:
        raise HTTPException(500,f"Metro View configuration is invalid: {exc}")
    if data.get("version")!=1:
        raise HTTPException(500,"Metro View configuration must use version 1")
    required={"routing","spacing","terrain"}
    if not required.issubset(data):
        raise HTTPException(500,"Metro View configuration requires routing, spacing, and terrain objects")
    def validate(value,path="metro_view"):
        if isinstance(value,bool) or value is None or isinstance(value,str): return
        if isinstance(value,(int,float)):
            if not math.isfinite(value): raise HTTPException(500,f"Metro View configuration contains a non-finite number at {path}")
            return
        if isinstance(value,list):
            for index,item in enumerate(value): validate(item,f"{path}[{index}]")
            return
        if isinstance(value,dict):
            for key,item in value.items():
                if not isinstance(key,str) or not key.strip(): raise HTTPException(500,f"Metro View configuration has an invalid key at {path}")
                validate(item,f"{path}.{key}")
            return
        raise HTTPException(500,f"Metro View configuration contains an unsupported value at {path}")
    validate(data)
    heading=data.get("routing",{}).get("initial_heading_weights",{})
    if set(heading)!={"0","45","90","135","180","225","270","315"}:
        raise HTTPException(500,"Metro View initial_heading_weights must define all eight octolinear headings")
    if not any(float(value)>0 for value in heading.values()):
        raise HTTPException(500,"Metro View requires at least one positive initial heading weight")
    spacing=data.get("spacing",{})
    routing=data.get("routing",{})
    terrain=data.get("terrain",{})
    if float(spacing.get("min_solid_line_mm",0))<=0:
        raise HTTPException(500,"Metro View min_solid_line_mm must be positive")
    if float(routing.get("segments",{}).get("line_separation_mm",0))<=0:
        raise HTTPException(500,"Metro View line_separation_mm must be positive")
    if float(routing.get("scale",{}).get("coverage_exponent",0))<=0:
        raise HTTPException(500,"Metro View routing.scale.coverage_exponent must be positive")
    if int(terrain.get("topography",{}).get("contour_levels",0))<3:
        raise HTTPException(500,"Metro View terrain.topography.contour_levels must be at least 3")
    strategies=routing.get("strategy_weights",{})
    if not isinstance(strategies,dict) or not any(float(value)>0 for value in strategies.values()):
        raise HTTPException(500,"Metro View routing.strategy_weights requires at least one positive strategy")
    connection_factor=float(data.get("appearance",{}).get("connection_width_factor",0))
    if not 0 < connection_factor < 1:
        raise HTTPException(500,"Metro View appearance.connection_width_factor must be between 0 and 1")
    if float(routing.get("connections",{}).get("departure_mm",0))<=0:
        raise HTTPException(500,"Metro View routing.connections.departure_mm must be positive")
    if float(data.get("poster",{}).get("legend_bottom_margin_mm",0))<=0:
        raise HTTPException(500,"Metro View poster.legend_bottom_margin_mm must be positive")
    return data
