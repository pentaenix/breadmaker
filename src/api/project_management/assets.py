from __future__ import annotations

"""Project asset read surface."""

import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from backend.environment import _HOSTED_LIMITS, runtime
from security.image_security import UnsafeImage, inspect_hosted_image_bytes

router=APIRouter()


@router.get("/api/assets/{asset_path:path}")
def project_asset(asset_path: str):
    try:
        payload=runtime.assets.read_blob(asset_path)
    except ValueError:
        raise HTTPException(400,detail="ASSET_001: Invalid asset path.")
    except FileNotFoundError:
        raise HTTPException(404)

    suffix=Path(str(asset_path or '')).suffix.lower()
    media_type=mimetypes.guess_type('asset'+suffix)[0] or 'application/octet-stream'
    if _HOSTED_LIMITS is not None:
        try:
            info=inspect_hosted_image_bytes(payload,suffix,byte_limit=_HOSTED_LIMITS.image_bytes)
        except UnsafeImage as exc:
            raise HTTPException(415,detail='Unsafe or unsupported hosted image asset') from exc
        media_type=info.media_type
    return Response(content=payload,media_type=media_type,headers={'X-Content-Type-Options':'nosniff'})
